"""Unit tests for pure modules: query language, avatars, store helpers, settings, generator."""
import re
import xml.etree.ElementTree as ET
from decimal import Decimal

import boto3
import pytest
from moto import mock_aws

from mockhris import avatar, generator, settings
from mockhris.query import (QueryError, Resolver, compare, compare_scalar, get_path, parse, referenced_groups,
                            tokenize)
from mockhris.store import Store, from_dynamo, to_dynamo

USERS = {
    "a@x.example": {"email": "a@x.example", "firstName": "Ann", "work": {"title": "Staff Engineer", "custom": {"team": "Core"},
                    "startDate": "2020-01-01", "tenureYears": 6, "isManager": True, "directReports": ["b@x.example"]},
                    "personal": {"nationality": ["Ireland", "UK"]}, "internal": {"status": "Active"}},
    "b@x.example": {"email": "b@x.example", "firstName": "Bob", "work": {"title": "Engineer", "custom": {"team": "Edge"},
                    "startDate": "2024-06-01", "tenureYears": 1, "isManager": False, "directReports": []},
                    "personal": {"nationality": []}, "internal": {"status": "Inactive"}},
}


def run(query, groups=None):
    r = Resolver(USERS, groups or {})
    return r.run_query(r.compile(query, "q"))


# ------------------------------------------------------------------- query
class TestQuery:
    def test_tokenizer(self):
        toks = tokenize("""title == 'it\\'s' and n >= -2.5 or x in [1, "a"]""")
        kinds = [t[0] for t in toks]
        assert ("string", "it's") in toks and ("number", -2.5) in toks and ("number", 1) in toks
        assert kinds[-1] == "eof" and ("kw", "and") in toks
        with pytest.raises(QueryError, match="unexpected character"):
            tokenize("title == @")

    @pytest.mark.parametrize("q,expected", [
        ('team == "core"', {"a@x.example"}),
        ('team != "core"', {"b@x.example"}),
        ('title contains "staff"', {"a@x.example"}),
        ('title startsWith "eng"', {"b@x.example"}),
        ('title endsWith "engineer"', {"a@x.example", "b@x.example"}),
        ('title matches "^staff"', {"a@x.example"}),
        ('startDate < "2021-01-01"', {"a@x.example"}),
        ('startDate <= "2020-01-01"', {"a@x.example"}),
        ('startDate > "2021-01-01"', {"b@x.example"}),
        ('work.tenureYears >= 6', {"a@x.example"}),
        ('team in ["edge", "nope"]', {"b@x.example"}),
        ('isManager == true', {"a@x.example"}),
        ('personal.nationality contains "uk"', {"a@x.example"}),
        ('personal.nationality == "ireland"', {"a@x.example"}),
        ('directReports exists', {"a@x.example"}),
        ('not (status == "Active")', {"b@x.example"}),
        ('(team == "core" or team == "edge") and not isManager == true', {"b@x.example"}),
    ])
    def test_operators(self, q, expected):
        assert run(q) == expected

    def test_type_mismatches_never_match(self):
        assert run('work.tenureYears > "5"') == set()           # number vs string
        assert run('isManager > 0') == set()                    # bool ordering
        assert run('work.tenureYears contains "6"') == set()    # contains on a number
        assert compare_scalar(None, "<", 3) is False
        assert compare_scalar(3, "<", None) is False
        with pytest.raises(QueryError, match="unknown operator"):
            compare_scalar("a", "~~", "b")
        assert compare(["x", "y"], "!=", "z") is True

    @pytest.mark.parametrize("q,msg", [
        ('team ==', "expected a value"),
        ('team "x"', "expected an operator"),
        ('== "x"', "expected a field name"),
        ('(team == "x"', "expected ')'"),
        ('team == "x" )', "unexpected"),
        ('memberOf(x)', "expected 'string'"),
        ('team in "x"', "expected '['"),
        ('team matches 5', "matches requires a string"),
        ('team matches "("', "invalid regex"),
        ('nope == 1', "unknown field"),
        ('memberOf("ghost")', "unknown group"),
        ('team in ["a", ]', "expected a value"),
    ])
    def test_errors(self, q, msg):
        with pytest.raises(QueryError, match=re.escape(msg)):
            run(q)

    def test_empty_list_and_null(self):
        assert run('team in []') == set()
        assert run('work.custom.team == null') == set()

    def test_groups_nesting_cycles_and_static(self):
        groups = {
            "core": {"name": "core", "query": 'team == "core"'},
            "all": {"name": "all", "query": 'memberOf("core") or team == "edge"'},
            "static": {"name": "static", "members": ["b@x.example"]},
            "ghost-member": {"name": "ghost-member", "members": ["nobody@x.example"]},
            "c1": {"name": "c1", "query": 'memberOf("c2")'}, "c2": {"name": "c2", "query": 'memberOf("c1")'},
        }
        r = Resolver(USERS, groups)
        assert r.resolve("all") == {"a@x.example", "b@x.example"}
        assert r.resolve("static") == {"b@x.example"}
        assert r.resolve("all") is r.resolve("all")           # cached
        with pytest.raises(QueryError, match="no file in users"):
            r.resolve("ghost-member")
        with pytest.raises(QueryError, match="circular group reference: c1 -> c2 -> c1"):
            r.resolve("c1")
        assert referenced_groups(parse('memberOf("a") and (not memberOf("b") or team == "x")')) == {"a", "b"}

    def test_get_path(self):
        assert get_path({"a": {"b": 1}}, "a.b") == 1
        assert get_path({"a": 1}, "a.b") is None


# ------------------------------------------------------------------ avatar
class TestAvatar:
    def test_every_style_renders_valid_svg(self):
        seen = set()
        for gender in ("Female", "Male", "Non-binary", None):
            for age in (22, 35, 47, 60):
                for i in range(40):
                    svg = avatar.render({"id": f"{gender}-{age}-{i}", "displayName": "A & <B>",
                                         "personal": {"gender": gender, "age": age},
                                         "work": {"custom": {"division": ["Engineering", None][i % 2]}}})
                    root = ET.fromstring(svg)
                    assert root.tag.endswith("svg") and "A &amp; &lt;B&gt;" in svg
                    seen.add(svg)
        assert len(seen) == 4 * 4 * 40   # every person gets a distinct portrait

    def test_deterministic_and_minimal_input(self):
        assert avatar.render({"id": "1"}) == avatar.render({"id": "1"})
        assert avatar.render({}) and avatar.render({"email": "x@y"})
        assert avatar._darken("#ffffff", 0.5) == "#7f7f7f"


# ------------------------------------------------------------------- store
@pytest.fixture()
def store():
    with mock_aws():
        s = Store(resource=boto3.resource("dynamodb", region_name="us-east-1"), prefix="unit")
        s.ensure_tables()
        yield s


class TestStore:
    def test_decimal_round_trip(self):
        assert to_dynamo({"f": 1.5})["f"] == Decimal("1.5")
        assert from_dynamo({"f": Decimal("1.5"), "i": Decimal("2"), "l": [Decimal("3")], "s": "x"}) ==             {"f": 1.5, "i": 2, "l": [3], "s": "x"}

    def test_construction_variants(self, monkeypatch):
        monkeypatch.setattr(boto3.session.Session, "get_credentials", lambda self: None)
        local = Store(endpoint_url="http://127.0.0.1:9", region="us-east-1", prefix="p")   # dummy creds supplied
        assert local.table_name("users") == "p-users"
        aws = Store(endpoint_url="", region="us-east-1", prefix="p")                        # real AWS: no endpoint
        assert aws.db.meta.client.meta.endpoint_url.startswith("https://dynamodb.")

    def test_ensure_tables_retries_then_skips_existing(self, store, monkeypatch):
        client = store.db.meta.client
        real, calls = client.list_tables, []

        def flaky(**kw):
            calls.append(1)
            if len(calls) == 1:
                raise OSError("starting up")
            return real(**kw)
        monkeypatch.setattr(client, "list_tables", flaky)
        monkeypatch.setattr("mockhris.store.time.sleep", lambda s: None)
        store.ensure_tables(wait_seconds=30)   # all tables already exist -> nothing created
        assert len(calls) == 2

    def test_crud_cache_and_conditions(self, store):
        store.put("groups", {"name": "g1", "x": 1}, must_not_exist=True)
        with pytest.raises(KeyError):
            store.put("groups", {"name": "g1"}, must_not_exist=True)
        assert store.get("groups", "g1")["x"] == 1                 # uncached read
        assert store.count("groups") == 1                          # uncached count
        assert [g["name"] for g in store.scan("groups")] == ["g1"]  # warms cache
        assert store.count("groups") == 1 and store.get("groups", "g1")["x"] == 1  # cached
        assert store.get("groups", "missing") is None
        store.put_many("groups", [{"name": f"g{i}"} for i in range(2, 30)])
        assert store.count("groups") == 29
        assert store.find("groups", lambda g: g["name"] == "g7")["name"] == "g7"
        assert store.find("groups", lambda g: False) is None
        assert sorted(store.scan_keys("groups"))[:2] == ["g1", "g10"]
        store.delete("groups", "g1")
        assert store.get("groups", "g1") is None
        store.invalidate()
        assert store.scan_keys("groups") and store.count("groups") == 28
        store.clear("groups")
        assert store.count("groups") == 0
        assert store.ping()

    def test_cache_expiry_reads_through(self, store, monkeypatch):
        store.put("groups", {"name": "a"})
        store.scan("groups")
        monkeypatch.setattr(Store, "CACHE_TTL", 0)
        store.table("groups").put_item(Item={"name": "b"})          # written behind the cache's back
        assert {g["name"] for g in store.scan("groups")} == {"a", "b"}

    def test_paginated_scan_count_and_clear(self, store):
        big = "x" * 300_000                                          # forces DynamoDB scan pagination (1MB pages)
        store.put_many("settings", [{"key": f"k{i}", "blob": big} for i in range(8)])
        store.invalidate()
        assert len(store.scan("settings")) == 8
        store.invalidate()
        assert store.count("settings") == 8
        store.clear("settings")
        assert store.count("settings") == 0

    def test_ensure_tables_unreachable(self):
        s = Store(endpoint_url="http://127.0.0.1:9", region="us-east-1", prefix="x")
        with pytest.raises(RuntimeError, match="cannot reach DynamoDB"):
            s.ensure_tables(wait_seconds=0)

    def test_other_errors_propagate(self, store):
        with pytest.raises(Exception):
            store.put("groups", {"wrong-key": 1})

    def test_get_store_singleton(self, monkeypatch):
        from mockhris import store as store_mod
        monkeypatch.setattr(store_mod, "_store", None)
        settings.reset()
        s = store_mod.get_store()
        assert s is store_mod.get_store()
        store_mod.set_store(None)


# ---------------------------------------------------------------- settings
def test_settings_from_environment(monkeypatch):
    monkeypatch.setenv("AUTO_SEED", "no")
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "a:*, b:* ,")
    monkeypatch.setenv("DYNAMODB_ENDPOINT", "")
    settings.reset()
    cfg = settings.get()
    assert cfg.auto_seed is False and cfg.mcp_allowed_hosts == ["a:*", "b:*"] and cfg.dynamodb_endpoint == ""
    monkeypatch.delenv("AUTO_SEED")
    settings.reset()
    assert settings.get().auto_seed is True
    settings.reset()


# --------------------------------------------------------------- generator
def test_generator_is_deterministic_and_consistent():
    u1, g1 = generator.generate_dataset(7)
    u2, _ = generator.generate_dataset(7)
    assert [u["email"] for u in u1] == [u["email"] for u in u2]
    emails = {u["email"] for u in u1}
    assert len(emails) == len(u1)
    ids = {u["id"] for u in u1}
    for u in u1:
        mgr = u["work"]["reportsTo"]
        assert mgr is None or mgr["id"] in ids
        assert re.fullmatch(r"[a-z0-9.-]+@[a-z.]+", u["email"]), u["email"]
    roots = [u for u in u1 if not u["work"]["reportsTo"]]
    assert len(roots) == 1 and roots[0]["work"]["custom"]["jobLevel"] == "E1"
    assert {g["name"] for g in g1} >= {"all-staff", "github-emu-users", "engineering-org"}
    assert generator.email_local("Ó Súilleabháin", "Groß") == "osuilleabhain.gross"
    assert generator.email_local("Siobhán", "Ní Bhriain") == "siobhan.nibhriain"
    assert generator.ascii_fold("Søren Æ") == "Soren "


def test_generator_edge_branches(monkeypatch):
    from datetime import date
    today = date(2026, 11, 14)                                    # a Saturday
    assert generator.future_monday(today, 0) == date(2026, 11, 16)   # Monday before is in the past -> next week
    assert generator.future_monday(today, 10) == date(2026, 11, 23)
    gen = generator.Generator(1)
    monkeypatch.setattr(gen, "pick_name", lambda country: ("Ann", "Lee"))
    monkeypatch.setattr(gen.rng, "random", lambda: 0.99)          # never accept a namesake voluntarily
    kw = dict(title="T", level="IC2", division="Engineering", department="D", team="Payments", site="Sydney",
              manager=None)
    first, second = gen.add_person(**kw), gen.add_person(**kw)
    assert first["email"] == "ann.lee@halcyon.example" and second["email"] == "ann.lee2@halcyon.example"
    # a team with no senior engineers contributes no security champion
    full = generator.Generator(2)
    full.build()
    for person in full.people:
        if person["team"] == "Payments":
            person["level"] = "IC1"
    champions = next(g for g in full.groups() if g["name"] == "security-champions")["members"]
    assert all(p["team"] != "Payments" for p in full.people if p["email"] in champions)


def test_resolver_reuses_compiled_queries():
    groups = {"core": {"name": "core", "query": 'team == "core"'}}
    r = Resolver(USERS, groups)
    first = r.resolve("core")
    r.cache.clear()                                               # resolution cache gone, compiled AST kept
    assert r.resolve("core") == first and "core" in r._asts



def test_store_refresh_is_shared(store):
    store.put("groups", {"name": "g"})
    first = store._load("groups")                     # scans
    assert store._load("groups") is first             # still fresh: no second scan
    assert store.find("groups", lambda g: g["name"] == "g") and store.scan_keys("groups") == ["g"]
