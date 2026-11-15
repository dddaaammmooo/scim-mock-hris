"""DynamoDB persistence (DynamoDB Local in a container, or real DynamoDB).

Tables (prefix configurable via TABLE_PREFIX):
  <prefix>-users       PK email   HiBob-style employee records
  <prefix>-groups      PK name    group definitions (static members and/or query)
  <prefix>-settings    PK key     app settings
  <prefix>-integrations PK id     outbound provisioning integrations (connection, scope, mappings)
  <prefix>-scim-state  PK key     "<integration>#user#<email>" / "<integration>#group#<name>" -> remote id + hash
  <prefix>-sync-runs   PK id      SCIM sync run history
  <prefix>-mock-scim   PK id      resources held by the built-in mock GitHub SCIM target
"""
import copy
import json
import threading
import time
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.config import Config
from botocore.exceptions import ClientError, EndpointConnectionError

from . import settings

TABLES = {
    "users": "email",
    "groups": "name",
    "settings": "key",
    "integrations": "id",
    "scim-state": "key",
    "sync-runs": "id",
    "mock-scim": "id",
}


def to_dynamo(obj):
    """JSON-compatible python -> DynamoDB-compatible (floats become Decimal)."""
    return json.loads(json.dumps(obj), parse_float=Decimal)


def from_dynamo(obj):
    if isinstance(obj, list):
        return [from_dynamo(x) for x in obj]
    if isinstance(obj, dict):
        return {k: from_dynamo(v) for k, v in obj.items()}
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    return obj


class Store:
    def __init__(self, endpoint_url=None, region=None, prefix=None, resource=None):
        cfg = settings.get()
        self.prefix = prefix or cfg.table_prefix
        if resource is None:
            kwargs = dict(region_name=region or cfg.aws_region,
                          config=Config(retries={"max_attempts": 3}, connect_timeout=3, read_timeout=10))
            endpoint = endpoint_url if endpoint_url is not None else cfg.dynamodb_endpoint
            if endpoint:
                kwargs["endpoint_url"] = endpoint
                # DynamoDB Local accepts any credentials; only supply dummies if none are configured.
                session = boto3.session.Session()
                if session.get_credentials() is None:
                    kwargs.update(aws_access_key_id="local", aws_secret_access_key="local")
            resource = boto3.resource("dynamodb", **kwargs)
        self.db = resource
        self._cache = {}
        self._lock = threading.RLock()
        # one refresh at a time per table: concurrent requests after cache expiry share a single scan
        self._refresh = {name: threading.Lock() for name in TABLES}
        self._tables = {name: self.db.Table(self.table_name(name)) for name in TABLES}

    def table_name(self, name):
        return f"{self.prefix}-{name}"

    def table(self, name):
        return self._tables[name]

    # -- lifecycle -------------------------------------------------------------
    def ensure_tables(self, wait_seconds=60):
        """Create any missing tables. Retries while DynamoDB Local is still starting."""
        deadline = time.time() + wait_seconds
        while True:
            try:
                existing = set(self.db.meta.client.list_tables()["TableNames"])
                break
            except (EndpointConnectionError, ClientError, OSError) as e:
                if time.time() > deadline:
                    raise RuntimeError(f"cannot reach DynamoDB: {e}") from e
                time.sleep(1)
        for name, key in TABLES.items():
            tname = self.table_name(name)
            if tname in existing:
                continue
            self.db.create_table(
                TableName=tname,
                KeySchema=[{"AttributeName": key, "KeyType": "HASH"}],
                AttributeDefinitions=[{"AttributeName": key, "AttributeType": "S"}],
                BillingMode="PAY_PER_REQUEST",
            ).wait_until_exists()

    def ping(self):
        self.db.meta.client.list_tables(Limit=1)
        return True

    # -- scan cache --------------------------------------------------------------
    # Every request needs whole-table views (group resolution touches all users), so full
    # scans are cached in-process and kept current by this process's own writes. The TTL
    # bounds staleness from writes made by other processes (e.g. a stdio MCP server).
    CACHE_TTL = 3.0

    def _cache_get(self, table):
        entry = self._cache.get(table)
        if entry and time.monotonic() - entry[0] < self.CACHE_TTL:
            return entry[1]
        return None

    def _cache_put(self, table, item):
        with self._lock:
            entry = self._cache.get(table)
            if entry:
                entry[1][item[TABLES[table]]] = copy.deepcopy(item)

    def _cache_delete(self, table, key):
        with self._lock:
            entry = self._cache.get(table)
            if entry:
                entry[1].pop(key, None)

    def invalidate(self, table=None):
        with self._lock:
            if table:
                self._cache.pop(table, None)
            else:
                self._cache.clear()

    # -- generic helpers -------------------------------------------------------
    def get(self, table, key):
        with self._lock:
            cached = self._cache_get(table)
            if cached is not None:
                item = cached.get(key)
                return copy.deepcopy(item) if item else None
        item = self.table(table).get_item(Key={TABLES[table]: key}).get("Item")
        return from_dynamo(item) if item else None

    def put(self, table, item, *, must_not_exist=False):
        kwargs = {"Item": to_dynamo(item)}
        if must_not_exist:
            kwargs["ConditionExpression"] = Attr(TABLES[table]).not_exists()
        try:
            self.table(table).put_item(**kwargs)
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise KeyError(item[TABLES[table]]) from e
            raise
        self._cache_put(table, from_dynamo(kwargs["Item"]))

    def delete(self, table, key):
        self.table(table).delete_item(Key={TABLES[table]: key})
        self._cache_delete(table, key)

    def _load(self, table):
        """Fresh cached view of a whole table, scanning DynamoDB at most once per expiry."""
        with self._refresh[table]:
            cached = self._cache_get(table)
            if cached is not None:          # another thread refreshed it while we waited
                return cached
            t = self.table(table)
            items, kwargs = [], {}
            while True:
                resp = t.scan(**kwargs)
                items.extend(resp.get("Items", []))
                if "LastEvaluatedKey" not in resp:
                    break
                kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
            cached = {i[TABLES[table]]: from_dynamo(i) for i in items}
            with self._lock:
                self._cache[table] = (time.monotonic(), cached)
            return cached

    def scan(self, table):
        cached = self._cache_get(table)
        if cached is None:
            cached = self._load(table)
        with self._lock:
            return copy.deepcopy(list(cached.values()))

    def find(self, table, predicate):
        """First item matching predicate, without copying the whole table."""
        self.scan_keys(table)
        with self._lock:
            for item in (self._cache_get(table) or self._load(table)).values():
                if predicate(item):
                    return copy.deepcopy(item)
        return None

    def scan_keys(self, table):
        """Warm the cache for a table if needed and return its keys (without copying the items)."""
        cached = self._cache_get(table) or self._load(table)
        with self._lock:
            return list(cached)

    def put_many(self, table, items):
        with self.table(table).batch_writer() as batch:
            for item in items:
                batch.put_item(Item=to_dynamo(item))
        for item in items:
            self._cache_put(table, from_dynamo(to_dynamo(item)))

    def clear(self, table):
        self.invalidate(table)
        key = TABLES[table]
        t = self.table(table)
        kwargs = {"ProjectionExpression": "#k", "ExpressionAttributeNames": {"#k": key}}
        with t.batch_writer() as batch:
            while True:
                resp = t.scan(**kwargs)
                for item in resp.get("Items", []):
                    batch.delete_item(Key={key: item[key]})
                if "LastEvaluatedKey" not in resp:
                    break
                kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        self.invalidate(table)

    def count(self, table):
        cached = self._cache_get(table)
        if cached is not None:
            return len(cached)
        t = self.table(table)
        total, kwargs = 0, {"Select": "COUNT"}
        while True:
            resp = t.scan(**kwargs)
            total += resp["Count"]
            if "LastEvaluatedKey" not in resp:
                return total
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


_store = None


def get_store():
    global _store
    if _store is None:
        _store = Store()
    return _store


def set_store(store):
    """Override the process-wide store (used by tests)."""
    global _store
    _store = store
