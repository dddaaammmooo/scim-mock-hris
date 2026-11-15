"""Outbound provisioning integrations (PingOne-style): connection + scope + attribute mapping.

An integration provisions HR users (and optionally groups) to a SCIM 2.0 target:

  connection   where and how to call the target (base URL, auth, headers, TLS, pacing)
  scope        which users: members of a group, a query, or everyone; inactive -> deprovision
  mappings     target SCIM attribute <- source expression (+ transforms, default, apply-on)
  roles        rule-based role value (e.g. GitHub enterprise_owner) exposed to mappings as $role
  groups       which HR groups to push, how to name them
  actions      allow create / update, and deprovision by disable, delete or nothing

Flow: resolve scope -> map payloads -> diff against last-synced state -> plan -> execute
(-> verify: read the target back and reconcile it against the HRIS).

Templates: "github-emu" (GitHub Enterprise Managed Users) and "scim2" (generic SCIM 2.0,
e.g. the MockSCIMTarget app). GitHub EMU reference:
docs.github.com/enterprise-cloud@latest/rest/enterprise-admin/scim
"""
import copy
import fnmatch
import hashlib
import json
import re
import threading
import time
import unicodedata
import uuid
from datetime import datetime, timezone

import httpx

from . import settings
from .hr import Conflict, Invalid, NotFound
from .query import ALIASES, QueryError, get_path

USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
ENTERPRISE = "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User"
GITHUB_ROLES = ["user", "enterprise_owner", "billing_manager", "guest_collaborator"]
MASK = "********"
SECRET_FIELDS = ("token", "password")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# Source expressions and transforms
# --------------------------------------------------------------------------- #
SPECIAL_SOURCES = {
    "$active": "true when HR status is Active",
    "$role": "role value from the role rules",
    "$managerScimId": "target SCIM id of the user's manager (once the manager is provisioned)",
    "$managerEmail": "manager's email",
    "$fullName": "first name + surname",
}


def _fold(s):
    s = s.replace("ß", "ss").replace("ø", "o").replace("Ø", "O").replace("æ", "ae")
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


TRANSFORMS = {
    "lowercase": lambda v: v.lower() if isinstance(v, str) else v,
    "uppercase": lambda v: v.upper() if isinstance(v, str) else v,
    "trim": lambda v: v.strip() if isinstance(v, str) else v,
    "localPart": lambda v: v.split("@")[0] if isinstance(v, str) else v,
    "domain": lambda v: v.split("@")[-1] if isinstance(v, str) and "@" in v else v,
    "asciiFold": lambda v: _fold(v) if isinstance(v, str) else v,
    "removeSpaces": lambda v: re.sub(r"\s+", "", v) if isinstance(v, str) else v,
    "slugify": lambda v: re.sub(r"[^a-z0-9]+", "-", _fold(v).lower()).strip("-") if isinstance(v, str) else v,
    "toString": lambda v: v if v is None or isinstance(v, str) else (json.dumps(v) if isinstance(v, (dict, list)) else str(v).lower() if isinstance(v, bool) else str(v)),
    "firstItem": lambda v: v[0] if isinstance(v, list) and v else (None if isinstance(v, list) else v),
}


def _resolve_path(user, path):
    return get_path(user, ALIASES.get(path, path))


def evaluate_source(source, user, ctx):
    """Evaluate a mapping source expression for one user. ctx carries $role etc."""
    source = (source or "").strip()
    if not source:
        return None
    if source in SPECIAL_SOURCES:
        return ctx.get(source)
    if len(source) >= 2 and source[0] == source[-1] and source[0] in "\"'":
        return source[1:-1]
    if source in ("true", "false"):
        return source == "true"
    if "{" in source:
        def sub(m):
            v = evaluate_source(m.group(1), user, ctx)
            return "" if v is None else str(v)
        return re.sub(r"\{([^{}]+)\}", sub, source).strip() or None
    return _resolve_path(user, source)


def validate_source(source, known_paths):
    source = (source or "").strip()
    if not source or source in SPECIAL_SOURCES or source in ("true", "false"):
        return
    if len(source) >= 2 and source[0] == source[-1] and source[0] in "\"'":
        return
    parts = re.findall(r"\{([^{}]+)\}", source) if "{" in source else [source]
    for p in parts:
        p = p.strip()
        if p in SPECIAL_SOURCES:
            continue
        if ALIASES.get(p, p) not in known_paths:
            raise Invalid(f"unknown source field {p!r}")


# --------------------------------------------------------------------------- #
# Target attribute paths -> SCIM payload
# --------------------------------------------------------------------------- #
MULTI_RE = re.compile(r'^(\w+)\[(\w+) eq "([^"]+)"\]\.(\w+)$')   # emails[type eq "work"].value
LIST_RE = re.compile(r"^(\w+)\[\]\.(\w+)$")                       # roles[].value


def set_target(payload, target, value):
    if value is None or value == "":
        return
    if target.startswith("urn:"):
        urn, _, attr = target.rpartition(":")
        if not urn or not attr:
            raise Invalid(f"invalid extension attribute {target!r}")
        ext = payload.setdefault(urn, {})
        _set_dotted(ext, attr, value)
        if urn not in payload["schemas"]:
            payload["schemas"].append(urn)
        return
    m = MULTI_RE.match(target)
    if m:
        attr, key, keyval, sub = m.groups()
        items = payload.setdefault(attr, [])
        item = next((i for i in items if i.get(key) == keyval), None)
        if item is None:
            item = {key: keyval}
            if attr in ("emails", "phoneNumbers", "addresses"):
                item["primary"] = not items
            items.append(item)
        item[sub] = value
        return
    m = LIST_RE.match(target)
    if m:
        attr, sub = m.groups()
        values = value if isinstance(value, list) else [value]
        payload[attr] = [{sub: v, "primary": False} for v in values if v not in (None, "")]
        return
    _set_dotted(payload, target, value)


def _set_dotted(obj, path, value):
    parts = path.split(".")
    for p in parts[:-1]:
        obj = obj.setdefault(p, {})
    obj[parts[-1]] = value


def get_target(resource, target):
    """Read a mapped attribute back from a SCIM resource (for verification)."""
    if target.startswith("urn:"):
        urn, _, attr = target.rpartition(":")
        return get_path(resource.get(urn) or {}, attr)
    m = MULTI_RE.match(target)
    if m:
        attr, key, keyval, sub = m.groups()
        item = next((i for i in resource.get(attr) or [] if str(i.get(key, "")).lower() == keyval.lower()), None)
        if item is None and resource.get(attr):
            item = next((i for i in resource[attr] if i.get("primary")), None)
        return (item or {}).get(sub)
    m = LIST_RE.match(target)
    if m:
        attr, sub = m.groups()
        return [i.get(sub) for i in resource.get(attr) or []]
    return get_path(resource, target)


TARGET_ATTRIBUTES = [
    "userName", "externalId", "active", "displayName", "nickName", "title", "userType", "preferredLanguage",
    "locale", "timezone", "name.givenName", "name.familyName", "name.formatted", "name.middleName",
    'emails[type eq "work"].value', 'phoneNumbers[type eq "work"].value', 'addresses[type eq "work"].country',
    'addresses[type eq "work"].locality', 'addresses[type eq "work"].region', "roles[].value",
    f"{ENTERPRISE}:employeeNumber", f"{ENTERPRISE}:costCenter", f"{ENTERPRISE}:organization",
    f"{ENTERPRISE}:division", f"{ENTERPRISE}:department", f"{ENTERPRISE}:manager.value",
]


def _m(target, source, transforms=None, default=None, apply_on="always"):
    return {"target": target, "source": source, "transforms": transforms or [], "default": default, "applyOn": apply_on}


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #
def templates():
    cfg = settings.get()
    base = cfg.public_base_url.rstrip("/")
    return {
        "github-emu": {
            "type": "github-emu",
            "label": "GitHub Enterprise Managed Users",
            "description": "SCIM provisioning for GitHub EMU (github.com or GHE.com data residency).",
            "required": ["userName", "externalId", "active", "displayName", 'emails[type eq "work"].value'],
            "roleValues": GITHUB_ROLES,
            "groupMemberDisplayKey": "displayName",
            "defaults": {
                "name": "GitHub EMU",
                "description": "Provision engineering, security and IT staff to GitHub Enterprise Managed Users.",
                "enabled": True,
                "connection": {"baseUrl": f"{base}/mock-github/scim/v2/enterprises/halcyon", "authType": "bearer",
                               "token": cfg.mock_scim_token, "username": "", "password": "", "headers": {},
                               "apiVersion": "2026-03-10", "verifyTls": True, "timeoutSeconds": 30,
                               "requestDelayMs": 0},
                "scope": {"mode": "group", "group": "github-emu-users", "query": ""},
                "mappings": [
                    _m("externalId", "id"),
                    _m("userName", "email", ["lowercase"]),
                    _m("active", "$active"),
                    _m("name.givenName", "firstName"),
                    _m("name.familyName", "surname"),
                    _m("name.formatted", "$fullName"),
                    _m("displayName", "displayName"),
                    _m('emails[type eq "work"].value', "email", ["lowercase"]),
                    _m("roles[].value", "$role"),
                ],
                "roles": {"enabled": True, "rules": [{"group": "github-enterprise-owners", "value": "enterprise_owner"}],
                          "defaultValue": "user"},
                "groups": {"enabled": True, "nameSource": "displayName", "skipEmpty": True, "exclude": [],
                           "patterns": ["team-*", "engineering-org", "github-copilot", "github-enterprise-owners",
                                        "security-champions", "emea-engineering", "soc2-production-access",
                                        "incident-commanders"]},
                "actions": {"create": True, "update": True, "deprovision": "disable"},
            },
        },
        "scim2": {
            "type": "scim2",
            "label": "Generic SCIM 2.0",
            "description": "Any RFC 7643/7644 SCIM 2.0 service provider, including the MockSCIMTarget app.",
            "required": ["userName"],
            "roleValues": None,
            "groupMemberDisplayKey": "display",
            "defaults": {
                "name": "SCIM 2.0 target",
                "description": "Provision all current staff to a SCIM 2.0 application.",
                "enabled": True,
                "connection": {"baseUrl": "http://host.docker.internal:8100/t/hris/scim/v2", "authType": "bearer",
                               "token": "target-token", "username": "", "password": "", "headers": {},
                               "apiVersion": "", "verifyTls": True, "timeoutSeconds": 30, "requestDelayMs": 0},
                "scope": {"mode": "group", "group": "all-staff", "query": ""},
                "mappings": [
                    _m("externalId", "id"),
                    _m("userName", "email", ["lowercase"]),
                    _m("active", "$active"),
                    _m("name.givenName", "firstName"),
                    _m("name.familyName", "surname"),
                    _m("name.formatted", "$fullName"),
                    _m("displayName", "displayName"),
                    _m("nickName", "personal.preferredName"),
                    _m("title", "title"),
                    _m("userType", "employmentType"),
                    _m("timezone", "address.timezone"),
                    _m('emails[type eq "work"].value', "email", ["lowercase"]),
                    _m('addresses[type eq "work"].locality', "address.city"),
                    _m('addresses[type eq "work"].country', "country"),
                    _m(f"{ENTERPRISE}:employeeNumber", "employeeId"),
                    _m(f"{ENTERPRISE}:costCenter", "costCentre"),
                    _m(f"{ENTERPRISE}:organization", "legalEntity"),
                    _m(f"{ENTERPRISE}:division", "division"),
                    _m(f"{ENTERPRISE}:department", "department"),
                    _m(f"{ENTERPRISE}:manager.value", "$managerScimId"),
                ],
                "roles": {"enabled": False, "rules": [], "defaultValue": ""},
                "groups": {"enabled": True, "nameSource": "displayName", "skipEmpty": True, "exclude": [],
                           "patterns": ["div-*", "region-*", "people-managers"]},
                "actions": {"create": True, "update": True, "deprovision": "disable"},
            },
        },
    }


def reference():
    """Everything the mapping editor needs: templates, target attributes, sources, transforms."""
    return {
        "templates": {k: {kk: vv for kk, vv in v.items()} for k, v in templates().items()},
        "targetAttributes": TARGET_ATTRIBUTES,
        "specialSources": SPECIAL_SOURCES,
        "aliases": ALIASES,
        "transforms": list(TRANSFORMS),
        "deprovisionModes": ["disable", "delete", "none"],
        "scopeModes": ["group", "query", "all"],
        "authTypes": ["bearer", "basic", "none"],
    }


# --------------------------------------------------------------------------- #
# Integration store
# --------------------------------------------------------------------------- #
class Integrations:
    def __init__(self, store, hr):
        self.store, self.hr = store, hr

    # -- CRUD ------------------------------------------------------------------
    def list(self):
        items = sorted(self.store.scan("integrations"), key=lambda i: i.get("name", "").lower())
        runs = self.store.scan("sync-runs")
        out = []
        for i in items:
            # runs recorded before integrations existed belong to the migrated github-emu integration
            last = max((r for r in runs if r.get("integrationId", "github-emu") == i["id"]),
                       key=lambda r: r["id"], default=None)
            pub = self.public(i)
            pub["lastRun"] = {k: last[k] for k in ("id", "status", "startedAt", "finishedAt", "total", "failed")} if last else None
            out.append(pub)
        return out

    def raw(self, iid):
        i = self.store.get("integrations", iid)
        if not i:
            raise NotFound(f"integration {iid!r} not found")
        i = copy.deepcopy(i)
        env = _env_token(iid)
        if env:
            i["connection"]["token"] = env
        return i

    def public(self, i):
        i = copy.deepcopy(i)
        conn = i.get("connection", {})
        env = _env_token(i["id"])
        conn["tokenSource"] = "env" if env else ("stored" if conn.get("token") else "none")
        for f in SECRET_FIELDS:
            conn[f + "Set"] = bool(conn.get(f)) or (f == "token" and bool(env))
            conn[f] = MASK if conn[f + "Set"] else ""
        conn["usesBuiltInMock"] = "/mock-github/" in conn.get("baseUrl", "")
        tpl = templates().get(i.get("type"), {})
        i["typeLabel"] = tpl.get("label", i.get("type"))
        i["required"] = tpl.get("required", [])
        i["roleValues"] = tpl.get("roleValues")
        return i

    def get(self, iid):
        return self.public(self.raw(iid))

    def create(self, data):
        iid = str(data.get("id", "")).strip().lower()
        itype = data.get("type")
        tpl = templates().get(itype)
        if not tpl:
            raise Invalid(f"type must be one of {list(templates())}")
        if not ID_RE.match(iid):
            raise Invalid("id must be 2-41 chars: lowercase letters, digits and '-'")
        if self.store.get("integrations", iid):
            raise Conflict(f"integration {iid!r} already exists")
        doc = copy.deepcopy(tpl["defaults"])
        for k in ("name", "description"):
            if data.get(k):
                doc[k] = data[k]
        if data.get("connection"):
            doc["connection"].update(data["connection"])
        doc.update(id=iid, type=itype, createdAt=now(), updatedAt=now())
        self._validate(doc)
        self.store.put("integrations", doc, must_not_exist=True)
        return self.get(iid)

    def update(self, iid, data):
        current = self.store.get("integrations", iid)
        if not current:
            raise NotFound(f"integration {iid!r} not found")
        doc = self.merge(current, data)
        doc["updatedAt"] = now()
        self._validate(doc)
        self.store.put("integrations", doc)
        return self.get(iid)

    def draft(self, iid, data):
        """The integration with unsaved edits applied (validated, not stored); secrets resolved."""
        doc = self.merge(self.raw(iid), data or {})
        self._validate(doc)
        return doc

    @staticmethod
    def merge(current, data):
        doc = copy.deepcopy(current)
        for k in ("name", "description", "enabled", "scope", "mappings", "roles", "groups", "actions"):
            if k in data:
                doc[k] = data[k] if not isinstance(data[k], dict) else {**doc.get(k, {}), **data[k]}
        if "connection" in data:
            conn = {**doc.get("connection", {})}
            for k, v in data["connection"].items():
                if k in SECRET_FIELDS and (v is None or v == MASK):
                    continue
                if k.endswith("Set") or k in ("tokenSource", "usesBuiltInMock"):
                    continue
                conn[k] = v
            doc["connection"] = conn
        return doc

    def delete(self, iid):
        if not self.store.get("integrations", iid):
            raise NotFound(f"integration {iid!r} not found")
        self.store.delete("integrations", iid)
        for key in [k for k in self.store.scan_keys("scim-state") if k.startswith(f"{iid}#")]:
            self.store.delete("scim-state", key)
        return {"deleted": iid}

    def reset_mappings(self, iid):
        doc = self.raw(iid)
        return self.update(iid, {"mappings": templates()[doc["type"]]["defaults"]["mappings"]})

    def _validate(self, doc):
        conn = doc.get("connection") or {}
        if not str(conn.get("baseUrl", "")).startswith(("http://", "https://")):
            raise Invalid("connection.baseUrl must start with http:// or https://")
        if conn.get("authType") not in ("bearer", "basic", "none"):
            raise Invalid("connection.authType must be bearer, basic or none")
        if not isinstance(conn.get("headers") or {}, dict):
            raise Invalid("connection.headers must be an object")
        groups = self.hr.groups_map()
        users = self.hr.users_map()
        scope = doc.get("scope") or {}
        if scope.get("mode") not in ("group", "query", "all"):
            raise Invalid("scope.mode must be group, query or all")
        if scope["mode"] == "group" and scope.get("group") not in groups:
            raise Invalid(f"scope group {scope.get('group')!r} does not exist")
        if scope["mode"] == "query":
            r = self.hr.resolver(users, groups)
            try:
                r.compile(scope.get("query") or "", "scope query")
            except QueryError as e:
                raise Invalid(str(e))
        known = self.hr.resolver(users, {}).known_paths
        targets = set()
        for m in doc.get("mappings") or []:
            t = (m.get("target") or "").strip()
            if not t:
                raise Invalid("every mapping needs a target attribute")
            if t in targets:
                raise Invalid(f"target attribute {t!r} is mapped twice")
            targets.add(t)
            validate_source(m.get("source"), known)
            for tr in m.get("transforms") or []:
                if tr not in TRANSFORMS:
                    raise Invalid(f"unknown transform {tr!r}")
            if m.get("applyOn", "always") not in ("always", "create"):
                raise Invalid("applyOn must be 'always' or 'create'")
        missing = [t for t in templates()[doc["type"]]["required"] if t not in targets]
        if missing:
            raise Invalid(f"required target attribute(s) not mapped: {', '.join(missing)}")
        roles = doc.get("roles") or {}
        allowed = templates()[doc["type"]]["roleValues"]
        for rule in roles.get("rules") or []:
            if rule.get("group") not in groups:
                raise Invalid(f"role rule group {rule.get('group')!r} does not exist")
            if allowed and rule.get("value") not in allowed:
                raise Invalid(f"role value must be one of {allowed}")
        if roles.get("enabled") and allowed and roles.get("defaultValue") not in allowed:
            raise Invalid(f"default role must be one of {allowed}")
        if doc.get("actions", {}).get("deprovision") not in ("disable", "delete", "none"):
            raise Invalid("actions.deprovision must be disable, delete or none")
        g = doc.get("groups") or {}
        for k in ("patterns", "exclude"):
            if not isinstance(g.get(k, []), list):
                raise Invalid(f"groups.{k} must be a list of glob patterns")
        if g.get("nameSource", "displayName") not in ("displayName", "name"):
            raise Invalid("groups.nameSource must be displayName or name")

    # -- references from groups (used by HR delete checks) ---------------------
    def group_references(self, group_name):
        refs = []
        for i in self.store.scan("integrations"):
            if (i.get("scope") or {}).get("mode") == "group" and i["scope"].get("group") == group_name:
                refs.append(f"integration:{i['id']}:scope")
            if any(r.get("group") == group_name for r in (i.get("roles") or {}).get("rules") or []):
                refs.append(f"integration:{i['id']}:roles")
        return refs

    # -- migration -------------------------------------------------------------
    def ensure_defaults(self):
        """First start: create the default integrations; migrate the old single-SCIM config/state."""
        if self.store.count("integrations"):
            return
        old = (self.store.get("settings", "scim") or {}).get("config")
        gh = copy.deepcopy(templates()["github-emu"]["defaults"])
        if old:
            gh["connection"].update({k: old[k] for k in ("baseUrl", "token", "apiVersion", "verifyTls",
                                                          "timeoutSeconds", "requestDelayMs") if k in old})
            gh["scope"]["group"] = old.get("scopeGroup", gh["scope"]["group"])
            gh["groups"].update(patterns=old.get("groupPatterns", gh["groups"]["patterns"]),
                                exclude=old.get("excludeGroupPatterns", []),
                                skipEmpty=old.get("skipEmptyGroups", True))
            gh["roles"]["rules"] = [{"group": m["group"], "value": m["role"]} for m in old.get("roleMappings", [])]
            gh["roles"]["defaultValue"] = old.get("defaultRole", "user")
            gh["actions"]["deprovision"] = "delete" if old.get("deprovisionMode") == "delete" else "disable"
        gh.update(id="github-emu", type="github-emu", createdAt=now(), updatedAt=now())
        sc = copy.deepcopy(templates()["scim2"]["defaults"])
        sc.update(id="scim-target", type="scim2", name="MockSCIMTarget", createdAt=now(), updatedAt=now(),
                  description="Provision all current staff to the MockSCIMTarget app (separate container) "
                              "to prove provisioning end to end.")
        for doc in (gh, sc):
            try:
                self._validate(doc)
            except Invalid:
                doc["enabled"] = False
            self.store.put("integrations", doc)
        for key in self.store.scan_keys("scim-state"):
            if key.startswith(("user#", "group#")):
                item = self.store.get("scim-state", key)
                item["key"] = f"github-emu#{key}"
                self.store.put("scim-state", item)
                self.store.delete("scim-state", key)


def _env_token(iid):
    import os
    env = os.environ.get("INTEGRATION_TOKEN_" + re.sub(r"[^A-Z0-9]", "_", iid.upper()))
    if not env and iid == "github-emu":
        env = settings.get().github_scim_token
    return env or ""


# --------------------------------------------------------------------------- #
# SCIM HTTP client
# --------------------------------------------------------------------------- #
class ScimError(Exception):
    def __init__(self, status, detail):
        super().__init__(f"HTTP {status}: {detail}")
        self.status, self.detail = status, detail


class ScimClient:
    def __init__(self, integration, http=None):
        conn = integration["connection"]
        self.base = conn["baseUrl"].rstrip("/")
        self.delay = (conn.get("requestDelayMs") or 0) / 1000
        self.headers = {"Accept": "application/scim+json", "Content-Type": "application/scim+json",
                        "User-Agent": "MockHRIS-Provisioning"}
        if conn.get("authType") == "bearer":
            self.headers["Authorization"] = f"Bearer {conn.get('token') or ''}"
        if integration["type"] == "github-emu" and conn.get("apiVersion"):
            self.headers["X-GitHub-Api-Version"] = conn["apiVersion"]
        self.headers.update(conn.get("headers") or {})
        auth = (conn.get("username") or "", conn.get("password") or "") if conn.get("authType") == "basic" else None
        self.http = http
        self.auth = auth
        if self.http is None:
            self.http = httpx.Client(timeout=conn.get("timeoutSeconds", 30), verify=conn.get("verifyTls", True))
        self.retries = 3

    def call(self, method, path, body=None, params=None, ok=(200, 201, 204)):
        url = path if path.startswith("http") else f"{self.base}{path}"
        kwargs = {"headers": self.headers, "params": params,
                  "content": json.dumps(body) if body is not None else None}
        if self.auth:
            kwargs["auth"] = self.auth
        attempt = 0
        while True:
            if self.delay:
                time.sleep(self.delay)
            resp = self.http.request(method, url, **kwargs)
            if resp.status_code != 429 or attempt >= self.retries:
                break
            attempt += 1                                   # honour rate limiting, then retry
            time.sleep(min(float(resp.headers.get("Retry-After", "1") or 1), 10))
        if resp.status_code not in ok:
            detail = resp.text[:500]
            try:
                j = resp.json()
                detail = j.get("detail") or j.get("message") or detail
            except ValueError:
                pass
            raise ScimError(resp.status_code, detail)
        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()

    def find(self, resource, attr, value):
        res = self.call("GET", f"/{resource}", params={"filter": f'{attr} eq "{value}"'})
        items = res.get("Resources") or []
        return items[0] if items else None

    def list_all(self, resource, page=100, extra=None):
        out, start = [], 1
        while True:
            res = self.call("GET", f"/{resource}", params={"startIndex": start, "count": page, **(extra or {})})
            items = res.get("Resources") or []
            out.extend(items)
            total = res.get("totalResults", len(out))
            if not items or len(out) >= total:
                return out
            start += len(items)


# --------------------------------------------------------------------------- #
# Engine: map, plan, execute, verify
# --------------------------------------------------------------------------- #
class Engine:
    def __init__(self, store, hr, integration, http=None):
        self.store, self.hr, self.i, self.http = store, hr, integration, http
        self.iid = integration["id"]
        self.tpl = templates()[integration["type"]]

    # -- state -----------------------------------------------------------------
    def state(self):
        users, groups = {}, {}
        prefix = f"{self.iid}#"
        for key in self.store.scan_keys("scim-state"):
            if not key.startswith(prefix):
                continue
            item = self.store.get("scim-state", key)
            kind, _, name = key[len(prefix):].partition("#")
            (users if kind == "user" else groups)[name] = item
        return users, groups

    def reset_state(self):
        for key in [k for k in self.store.scan_keys("scim-state") if k.startswith(f"{self.iid}#")]:
            self.store.delete("scim-state", key)
        return {"reset": True}

    # -- scope & mapping -------------------------------------------------------
    def scope_emails(self, users, groups, r, scope=None):
        scope = scope or self.i["scope"]
        if scope["mode"] == "all":
            return set(users)
        if scope["mode"] == "query":
            try:
                return r.run_query(r.compile(scope.get("query") or "", "scope query"))
            except QueryError as e:
                raise Invalid(str(e))
        if scope.get("group") not in groups:
            raise Invalid(f"scope group {scope.get('group')!r} does not exist")
        return set(r.resolve(scope["group"]))

    def role_for(self, email, role_sets):
        roles = self.i.get("roles") or {}
        if not roles.get("enabled"):
            return None
        return next((v for v, members in role_sets if email in members), roles.get("defaultValue") or None)

    def map_user(self, user, ctx, previous=None, creating=True):
        payload = {"schemas": [USER_SCHEMA]}
        trace = []
        for m in self.i["mappings"]:
            raw = evaluate_source(m.get("source"), user, ctx)
            value = raw
            for t in m.get("transforms") or []:
                value = TRANSFORMS[t](value)
            if value in (None, "", []) and m.get("default") not in (None, ""):
                value = m["default"]
            if m.get("applyOn") == "create" and not creating:
                value = get_target(previous or {}, m["target"]) if previous else None
            trace.append({"target": m["target"], "source": m.get("source"), "raw": raw, "value": value})
            set_target(payload, m["target"], value)
        return payload, trace

    def _context(self, user, users, role_sets, state_users):
        mgr = (user.get("work", {}).get("reportsTo") or {}).get("email")
        mgr_state = state_users.get(mgr) if mgr else None
        return {
            "$active": user.get("internal", {}).get("status") == "Active",
            "$role": self.role_for(user["email"], role_sets),
            "$managerEmail": mgr,
            "$managerScimId": mgr_state["scimId"] if mgr_state and mgr_state.get("active") else None,
            "$fullName": f"{user.get('firstName', '')} {user.get('surname', '')}".strip(),
        }

    def desired(self, users=None, groups=None):
        users = users if users is not None else self.hr.users_map()
        groups = groups if groups is not None else self.hr.groups_map()
        r = self.hr.resolver(users, groups)
        scope = self.scope_emails(users, groups, r)
        role_sets = [(rule["value"], r.resolve(rule["group"]))
                     for rule in (self.i.get("roles") or {}).get("rules") or [] if rule.get("group") in groups]
        state_users, _ = self.state()
        desired_users = {}
        for email in sorted(scope):
            u = users[email]
            st = state_users.get(email)
            ctx = self._context(u, users, role_sets, state_users)
            payload, _ = self.map_user(u, ctx, previous=(st or {}).get("payload"), creating=not st)
            payload["active"] = ctx["$active"]  # SCIM core attribute; drives provisioning decisions
            desired_users[email] = payload

        active_emails = {e for e, p in desired_users.items() if p.get("active")}
        desired_groups = {}
        gcfg = self.i.get("groups") or {}
        if gcfg.get("enabled"):
            for name in sorted(groups):
                if not any(fnmatch.fnmatchcase(name, p) for p in gcfg.get("patterns") or []):
                    continue
                if any(fnmatch.fnmatchcase(name, p) for p in gcfg.get("exclude") or []):
                    continue
                members = sorted(r.resolve(name) & active_emails)
                if not members and gcfg.get("skipEmpty", True):
                    continue
                display = name if gcfg.get("nameSource") == "name" else (groups[name].get("displayName") or name)
                desired_groups[name] = {"externalId": name, "displayName": display, "members": members}
        return desired_users, desired_groups, users

    def preview_user(self, email):
        users, groups = self.hr.users_map(), self.hr.groups_map()
        email = email.lower()
        if email not in users:
            raise NotFound(f"user {email!r} not found")
        r = self.hr.resolver(users, groups)
        in_scope = email in self.scope_emails(users, groups, r)
        role_sets = [(rule["value"], r.resolve(rule["group"]))
                     for rule in (self.i.get("roles") or {}).get("rules") or [] if rule.get("group") in groups]
        state_users, _ = self.state()
        st = state_users.get(email)
        ctx = self._context(users[email], users, role_sets, state_users)
        payload, trace = self.map_user(users[email], ctx, previous=(st or {}).get("payload"), creating=not st)
        if not in_scope:
            action = "deprovision" if st and st.get("active") else "none (out of scope)"
        elif not ctx["$active"]:
            action = "deprovision" if st and st.get("active") else "none (inactive, never provisioned)"
        elif not st:
            action = "create"
        elif not st.get("active"):
            action = "reactivate"
        else:
            action = "no change" if st.get("hash") == digest(payload) else "update"
        return {"email": email, "inScope": in_scope, "active": ctx["$active"], "role": ctx["$role"],
                "provisioned": bool(st), "scimId": (st or {}).get("scimId"), "nextAction": action,
                "trace": trace, "payload": payload}

    def scope_preview(self, scope, sample=12):
        users, groups = self.hr.users_map(), self.hr.groups_map()
        r = self.hr.resolver(users, groups)
        emails = sorted(self.scope_emails(users, groups, r, scope))
        active = [e for e in emails if users[e].get("internal", {}).get("status") == "Active"]
        return {"count": len(emails), "active": len(active), "inactive": len(emails) - len(active),
                "sample": [{"email": e, "displayName": users[e].get("displayName"), "id": users[e].get("id"),
                            "avatar": (users[e].get("about") or {}).get("avatar"),
                            "title": users[e].get("work", {}).get("title")} for e in active[:sample]]}

    def groups_preview(self):
        _, desired_groups, _ = self.desired()
        return [{"name": n, "displayName": g["displayName"], "members": len(g["members"])}
                for n, g in desired_groups.items()]

    # -- plan --------------------------------------------------------------------
    def plan(self, full=False):
        desired_users, desired_groups, users = self.desired()
        state_users, state_groups = self.state()
        actions = self.i.get("actions") or {}
        deprovision = actions.get("deprovision", "disable")
        ops = []

        for email, payload in desired_users.items():
            st = state_users.get(email)
            h = digest(payload)
            if not payload.get("active"):
                if st and st.get("active") and deprovision != "none":
                    ops.append(self._deprovision_op(deprovision, email, st, "inactive in HRIS"))
                continue
            if not st:
                if actions.get("create", True):
                    ops.append({"op": "create_user", "kind": "user", "key": email, "payload": payload,
                                "summary": f"create {payload.get('userName')}"})
            elif not st.get("active"):
                ops.append({"op": "reactivate_user", "kind": "user", "key": email, "payload": payload,
                            "scimId": st["scimId"], "summary": "reactivate"})
            elif (full or st.get("hash") != h) and actions.get("update", True):
                fields = changed_fields(st.get("payload"), payload)
                ops.append({"op": "update_user", "kind": "user", "key": email, "payload": payload,
                            "scimId": st["scimId"], "changes": fields,
                            "summary": "update " + (", ".join(fields) if fields else "(full resync)")})

        if deprovision != "none":
            for email, st in state_users.items():
                if email in desired_users:
                    continue
                reason = "removed from HRIS" if email not in users else "left provisioning scope"
                if deprovision == "delete" or st.get("active"):
                    ops.append(self._deprovision_op(deprovision, email, st, reason))

        for name, g in desired_groups.items():
            st = state_groups.get(name)
            h = digest(g)
            if not st:
                ops.append({"op": "create_group", "kind": "group", "key": name, "group": g,
                            "summary": f"create '{g['displayName']}' with {len(g['members'])} members"})
            elif full or st.get("hash") != h:
                before, after = set(st.get("members", [])), set(g["members"])
                ops.append({"op": "update_group", "kind": "group", "key": name, "group": g, "scimId": st["scimId"],
                            "added": sorted(after - before), "removed": sorted(before - after),
                            "summary": f"+{len(after - before)} / -{len(before - after)} members"
                                       + ("" if st.get("displayName") == g["displayName"] else ", rename")})
        for name, st in state_groups.items():
            if name not in desired_groups:
                ops.append({"op": "delete_group", "kind": "group", "key": name, "scimId": st["scimId"],
                            "summary": "no longer selected, or empty"})

        order = {"create_user": 0, "reactivate_user": 1, "update_user": 2, "create_group": 3,
                 "update_group": 4, "delete_group": 5, "deactivate_user": 6, "delete_user": 7}
        ops.sort(key=lambda o: (order[o["op"]], o["key"]))
        counts = {}
        for o in ops:
            counts[o["op"]] = counts.get(o["op"], 0) + 1
        return {"integrationId": self.iid, "generatedAt": now(), "target": self.i["connection"]["baseUrl"],
                "inScope": len(desired_users),
                "activeInScope": sum(1 for p in desired_users.values() if p.get("active")),
                "groupsSelected": len(desired_groups), "counts": counts, "operations": ops}

    @staticmethod
    def _deprovision_op(mode, email, st, reason):
        if mode == "delete":
            return {"op": "delete_user", "kind": "user", "key": email, "scimId": st["scimId"],
                    "summary": f"delete ({reason})"}
        return {"op": "deactivate_user", "kind": "user", "key": email, "scimId": st["scimId"],
                "summary": f"disable ({reason})"}

    # -- execute -------------------------------------------------------------------
    def client(self):
        return ScimClient(self.i, self.http)

    def execute(self, plan, progress=None):
        client = self.client()
        state_users, _ = self.state()
        ids = {e: s["scimId"] for e, s in state_users.items() if s.get("active")}
        results = []
        unreachable = 0       # consecutive transport failures (connection refused, DNS, timeouts)
        ops = plan["operations"]
        for n, op in enumerate(ops):
            started = time.time()
            res = {"op": op["op"], "key": op["key"], "summary": op.get("summary")}
            if unreachable >= self.MAX_CONSECUTIVE_NETWORK_ERRORS:
                res.update(status="failed", error="skipped: target unreachable", durationMs=0)
                results.append(res)
                continue
            try:
                detail = getattr(self, f"_do_{op['op']}")(client, op, ids)
                res.update(status="ok", **(detail or {}))
                unreachable = 0
            except ScimError as e:
                res.update(status="failed", httpStatus=e.status, error=str(e.detail)[:300])
                unreachable = 0
            except httpx.HTTPError as e:
                res.update(status="failed", error=f"{type(e).__name__}: {e}"[:300])
                unreachable += 1
            res["durationMs"] = int((time.time() - started) * 1000)
            results.append(res)
            if progress:
                progress(n + 1, len(ops), results)
        if progress and results:
            progress(len(ops), len(ops), results)
        return results

    # Stop calling a target that keeps refusing connections; the remaining operations are marked as
    # skipped (state is not advanced, so the next sync retries them).
    MAX_CONSECUTIVE_NETWORK_ERRORS = 5

    def _key(self, kind, name):
        return f"{self.iid}#{kind}#{name}"

    def _save_user(self, email, scim_id, payload, active):
        self.store.put("scim-state", {"key": self._key("user", email), "scimId": scim_id, "active": active,
                                      "userName": (payload or {}).get("userName"),
                                      "hash": digest(payload) if payload else None,
                                      "payload": payload, "lastSyncedAt": now()})

    def _do_create_user(self, client, op, ids):
        payload = op["payload"]
        try:
            res = client.call("POST", "/Users", payload)
            outcome = "created"
        except ScimError as e:
            if e.status != 409:
                raise
            existing = client.find("Users", "userName", payload["userName"])
            if not existing and payload.get("externalId"):
                existing = client.find("Users", "externalId", payload["externalId"])
            if not existing:
                raise
            res = client.call("PUT", f"/Users/{existing['id']}", payload)
            outcome = "adopted existing"
        ids[op["key"]] = res["id"]
        self._save_user(op["key"], res["id"], payload, True)
        return {"scimId": res["id"], "detail": outcome}

    def _do_update_user(self, client, op, ids):
        try:
            res = client.call("PUT", f"/Users/{op['scimId']}", op["payload"])
        except ScimError as e:
            if e.status != 404:
                raise
            return self._do_create_user(client, op, ids) | {"detail": "recreated (missing remotely)"}
        ids[op["key"]] = res.get("id", op["scimId"])
        self._save_user(op["key"], ids[op["key"]], op["payload"], True)
        return {"scimId": ids[op["key"]]}

    _do_reactivate_user = _do_update_user

    def _do_deactivate_user(self, client, op, ids):
        body = {"schemas": [PATCH_SCHEMA], "Operations": [{"op": "replace", "path": "active", "value": False}]}
        try:
            client.call("PATCH", f"/Users/{op['scimId']}", body)
        except ScimError as e:
            if e.status != 404:
                raise
            self.store.delete("scim-state", self._key("user", op["key"]))
            ids.pop(op["key"], None)
            return {"detail": "already gone remotely; state cleared"}
        st = self.store.get("scim-state", self._key("user", op["key"])) or {}
        payload = {**st["payload"], "active": False} if st.get("payload") else None
        self._save_user(op["key"], op["scimId"], payload, False)
        ids.pop(op["key"], None)

    def _do_delete_user(self, client, op, ids):
        client.call("DELETE", f"/Users/{op['scimId']}", ok=(200, 204, 404))
        self.store.delete("scim-state", self._key("user", op["key"]))
        ids.pop(op["key"], None)

    def _group_body(self, op, ids):
        g = op["group"]
        key = self.tpl["groupMemberDisplayKey"]
        members, skipped = [], []
        for email in g["members"]:
            if email in ids:
                members.append({"value": ids[email], key: email})
            else:  # the user failed to provision earlier in this run
                skipped.append(email)
        return {"schemas": [GROUP_SCHEMA], "externalId": g["externalId"], "displayName": g["displayName"],
                "members": members}, skipped

    def _save_group(self, name, scim_id, group, sent):
        recorded = {**group, "members": sorted(sent)}
        self.store.put("scim-state", {"key": self._key("group", name), "scimId": scim_id,
                                      "displayName": group["displayName"], "members": recorded["members"],
                                      "hash": digest(recorded), "lastSyncedAt": now()})

    def _sent(self, body):
        key = self.tpl["groupMemberDisplayKey"]
        return [m[key] for m in body["members"]]

    def _do_create_group(self, client, op, ids):
        body, skipped = self._group_body(op, ids)
        try:
            res = client.call("POST", "/Groups", body)
            outcome = "created"
        except ScimError as e:
            if e.status != 409:
                raise
            existing = client.find("Groups", "externalId", body["externalId"]) or \
                client.find("Groups", "displayName", body["displayName"])
            if not existing:
                raise
            res = client.call("PUT", f"/Groups/{existing['id']}", body)
            outcome = "adopted existing"
        self._save_group(op["key"], res["id"], op["group"], self._sent(body))
        out = {"scimId": res["id"], "detail": outcome, "members": len(body["members"])}
        if skipped:
            out["skippedMembers"] = skipped[:20]
        return out

    def _do_update_group(self, client, op, ids):
        body, skipped = self._group_body(op, ids)
        try:
            res = client.call("PUT", f"/Groups/{op['scimId']}", body)
        except ScimError as e:
            if e.status != 404:
                raise
            return self._do_create_group(client, op, ids) | {"detail": "recreated (missing remotely)"}
        self._save_group(op["key"], res.get("id", op["scimId"]), op["group"], self._sent(body))
        out = {"members": len(body["members"])}
        if skipped:
            out["skippedMembers"] = skipped[:20]
        return out

    def _do_delete_group(self, client, op, ids):
        client.call("DELETE", f"/Groups/{op['scimId']}", ok=(200, 204, 404))
        self.store.delete("scim-state", self._key("group", op["key"]))

    def test_connection(self):
        client = self.client()
        started = time.time()
        try:
            res = client.call("GET", "/Users", params={"count": 1})
            return {"ok": True, "target": client.base, "totalUsers": res.get("totalResults"),
                    "latencyMs": int((time.time() - started) * 1000)}
        except ScimError as e:
            return {"ok": False, "target": client.base, "httpStatus": e.status, "error": str(e.detail)[:300]}
        except httpx.HTTPError as e:
            return {"ok": False, "target": client.base, "error": f"{type(e).__name__}: {e}"[:300]}

    # -- verify (end-to-end reconciliation) -----------------------------------
    VERIFY_FIELDS = ["userName", "externalId", "active", "displayName", "name.givenName", "name.familyName",
                     'emails[type eq "work"].value']

    def verify(self):
        """Read users/groups back from the target and reconcile them against the HRIS."""
        started = time.time()
        desired_users, desired_groups, users = self.desired()
        state_users, _ = self.state()
        deprovision = (self.i.get("actions") or {}).get("deprovision", "disable")
        client = self.client()
        try:
            remote_users = client.list_all("Users")
            remote_groups = client.list_all("Groups") if (self.i.get("groups") or {}).get("enabled") else []
        except (ScimError, httpx.HTTPError) as e:
            return {"ok": False, "error": str(e)[:300], "checkedAt": now()}
        by_name = {str(u.get("userName", "")).lower(): u for u in remote_users}
        by_id = {u.get("id"): u for u in remote_users}
        mapped_fields = {m["target"] for m in self.i["mappings"]}
        # Every mapped attribute is compared (core identity attributes first), plus `active`.
        fields = [f for f in self.VERIFY_FIELDS if f in mapped_fields or f == "active"] + \
                 sorted(f for f in mapped_fields if f not in self.VERIFY_FIELDS)

        missing, mismatched, wrongly_active, matched = [], [], [], 0
        expected_names = set()
        for email, payload in desired_users.items():
            uname = str(payload.get("userName", "")).lower()
            remote = by_name.get(uname)
            if payload.get("active"):
                expected_names.add(uname)
                if not remote:
                    missing.append({"email": email, "userName": payload.get("userName")})
                    continue
                diffs = []
                for f in fields:
                    exp, act = get_target(payload, f), get_target(remote, f)
                    if f == "userName" or isinstance(exp, str) and isinstance(act, str) and f.endswith("value"):
                        exp, act = (str(exp).lower() if exp is not None else None), (str(act).lower() if act is not None else None)
                    if exp != act:
                        diffs.append({"attribute": f, "expected": exp, "actual": act})
                if diffs:
                    mismatched.append({"email": email, "userName": payload.get("userName"), "differences": diffs})
                else:
                    matched += 1
            elif remote:
                expected_names.add(uname)
                if remote.get("active") and deprovision != "none":
                    wrongly_active.append({"email": email, "userName": remote.get("userName")})
        # previously provisioned users that left scope
        for email, st in state_users.items():
            if email in desired_users:
                continue
            remote = by_name.get(str(st.get("userName") or "").lower()) or by_id.get(st.get("scimId"))
            if remote:
                expected_names.add(str(remote.get("userName", "")).lower())
                if remote.get("active") and deprovision != "none":
                    wrongly_active.append({"email": email, "userName": remote.get("userName")})
        extra = [{"userName": u.get("userName"), "active": u.get("active"), "id": u.get("id")}
                 for name, u in by_name.items() if name not in expected_names and u.get("active")]

        group_report = {"expected": len(desired_groups), "matched": 0, "missing": [], "memberDiffs": []}
        if (self.i.get("groups") or {}).get("enabled"):
            remote_by_name = {str(g.get("displayName", "")).lower(): g for g in remote_groups}
            for name, g in desired_groups.items():
                rg = remote_by_name.get(g["displayName"].lower())
                if not rg:
                    group_report["missing"].append(name)
                    continue
                remote_members = set()
                for m in rg.get("members") or []:
                    ru = by_id.get(m.get("value"))
                    remote_members.add(str(ru.get("userName")).lower() if ru else f"<unknown id {m.get('value')}>")
                expected_members = {str(desired_users[e].get("userName", "")).lower() for e in g["members"]}
                miss, ext = sorted(expected_members - remote_members), sorted(remote_members - expected_members)
                if miss or ext:
                    group_report["memberDiffs"].append({"group": name, "missing": miss[:50], "extra": ext[:50],
                                                        "missingCount": len(miss), "extraCount": len(ext)})
                else:
                    group_report["matched"] += 1

        expected_active = sum(1 for p in desired_users.values() if p.get("active"))
        ok = not (missing or mismatched or wrongly_active or extra or group_report["missing"]
                  or group_report["memberDiffs"])
        return {
            "ok": ok, "checkedAt": now(), "durationMs": int((time.time() - started) * 1000),
            "target": client.base, "fieldsCompared": fields,
            "users": {"expectedActive": expected_active, "remoteTotal": len(remote_users),
                      "remoteActive": sum(1 for u in remote_users if u.get("active")), "matched": matched,
                      "missing": missing[:200], "mismatched": mismatched[:200],
                      "wronglyActive": wrongly_active[:200], "unexpected": extra[:200]},
            "groups": group_report,
        }


def changed_fields(old, new):
    if not old:
        return []
    return [k for k in sorted(set(old) | set(new)) if old.get(k) != new.get(k)]


# --------------------------------------------------------------------------- #
# Runs (background execution + history)
# --------------------------------------------------------------------------- #
class Runner:
    _locks = {}
    _guard = threading.Lock()

    def __init__(self, store, hr, http=None):
        self.store, self.hr, self.http = store, hr, http
        self.integrations = Integrations(store, hr)

    def engine(self, iid, draft=None):
        doc = self.integrations.draft(iid, draft) if draft else self.integrations.raw(iid)
        return Engine(self.store, self.hr, doc, self.http)

    def _lock(self, iid):
        with self._guard:
            return self._locks.setdefault(iid, threading.Lock())

    def start(self, iid, full=False, background=True):
        integration = self.integrations.raw(iid)
        if not integration.get("enabled", True):
            raise Conflict(f"integration {iid!r} is disabled")
        lock = self._lock(iid)
        if not lock.acquire(blocking=False):
            raise Conflict(f"a sync is already running for {iid!r}")
        try:
            engine = Engine(self.store, self.hr, integration, self.http)
            plan = engine.plan(full=full)
        except Exception:
            lock.release()
            raise
        run = {
            "id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + "-" + uuid.uuid4().hex[:6],
            "integrationId": iid, "integrationName": integration.get("name"),
            "status": "running", "startedAt": now(), "finishedAt": None, "full": full,
            "target": plan["target"], "counts": plan["counts"], "total": len(plan["operations"]),
            "done": 0, "succeeded": 0, "failed": 0, "results": [],
        }
        self.store.put("sync-runs", run)
        if background:
            threading.Thread(target=self._run, args=(engine, run, plan, lock), daemon=True).start()
            return run
        return self._run(engine, run, plan, lock)

    def _run(self, engine, run, plan, lock):
        last = [time.time()]

        def progress(done, total, results):
            run["done"] = done
            if time.time() - last[0] > 1.0:
                run["succeeded"] = sum(r["status"] == "ok" for r in results)
                run["failed"] = len(results) - run["succeeded"]
                self.store.put("sync-runs", {**run, "results": results[-50:]})
                last[0] = time.time()

        try:
            results = engine.execute(plan, progress)
            # Creating, reactivating, disabling or deleting users changes which references resolve
            # (e.g. $managerScimId), so run one follow-up pass of attribute updates to converge in one sync.
            changes_refs = {"create_user", "reactivate_user", "deactivate_user", "delete_user"}
            if any(r["op"] in changes_refs and r["status"] == "ok" for r in results) and \
                    any(m.get("source") == "$managerScimId" for m in engine.i["mappings"]):
                follow = engine.plan()
                follow["operations"] = [o for o in follow["operations"] if o["op"] == "update_user"]
                if follow["operations"]:
                    for o in follow["operations"]:
                        o["summary"] = "follow-up: " + o.get("summary", "")
                    run["total"] += len(follow["operations"])
                    extra = engine.execute(follow, lambda d, t, r: progress(len(results) + d, run["total"], results + r))
                    results = results + extra
            run["results"] = results[:1500]
            run["succeeded"] = sum(r["status"] == "ok" for r in results)
            run["failed"] = len(results) - run["succeeded"]
            run["done"] = len(results)
            run["status"] = "succeeded" if not run["failed"] else ("failed" if not run["succeeded"] else "partial")
        except Exception as e:  # noqa: BLE001 - recorded on the run
            run["status"], run["error"] = "error", f"{type(e).__name__}: {e}"[:500]
        finally:
            run["finishedAt"] = now()
            self.store.put("sync-runs", run)
            lock.release()
        return run

    def runs(self, iid=None, limit=20):
        runs = [r for r in self.store.scan("sync-runs") if not iid or r.get("integrationId", "github-emu") == iid]
        runs.sort(key=lambda r: r["id"], reverse=True)
        return [{k: v for k, v in r.items() if k != "results"} for r in runs[:limit]]

    def get_run(self, run_id):
        run = self.store.get("sync-runs", run_id)
        if not run:
            raise NotFound(f"run {run_id!r} not found")
        return run
