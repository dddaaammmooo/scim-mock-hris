"""Provisioning engine: mapping, validation, planning, execution/recovery, verify, runs.

Uses a programmable in-memory SCIM server (httpx.MockTransport) so every failure path can be injected.
"""
import re
import threading
import uuid

import boto3
import httpx
import pytest
from moto import mock_aws

from mockhris import integrations as I
from mockhris import settings
from mockhris.hr import Conflict, HRService, Invalid, NotFound
from mockhris.store import Store, set_store

BASE = "http://fake/scim/v2"
SCOPE = 'team == "Payments" and status == "Active"'


class FakeScim:
    """Minimal SCIM 2.0 server. `fail[(METHOD, "Users"|"Groups")]` is a list of statuses returned once each."""

    def __init__(self):
        self.users, self.groups, self.fail, self.calls = {}, {}, {}, []

    def handler(self, request):
        self.calls.append(request)
        parts = request.url.path.split("/scim/v2/", 1)[1].split("/")
        kind, rid = parts[0], (parts[1] if len(parts) > 1 else None)
        queue = self.fail.get((request.method, kind))
        if queue:
            status = queue.pop(0)
            if status == "network":
                raise httpx.ConnectError("connection refused", request=request)
            if status == "text":
                return httpx.Response(502, text="bad gateway")
            return httpx.Response(status, json={"detail": f"injected {status}"},
                                  headers={"Retry-After": "0"} if status == 429 else {})
        store = self.users if kind == "Users" else self.groups
        body = request.content and httpx.Response(200, content=request.content).json()
        if request.method == "GET" and rid is None:
            items = list(store.values())
            flt = request.url.params.get("filter")
            if flt:
                attr, value = re.fullmatch(r'(\w+) eq "(.*)"', flt).groups()
                items = [i for i in items if str(i.get(attr, "")).lower() == value.lower()]
            start, count = int(request.url.params.get("startIndex", 1)), int(request.url.params.get("count", 100))
            page = items[start - 1:start - 1 + count]
            return httpx.Response(200, json={"totalResults": len(items), "Resources": page})
        if request.method == "POST":
            key = "userName" if kind == "Users" else "displayName"
            if any(i[key].lower() == body[key].lower() for i in store.values()):
                return httpx.Response(409, json={"detail": "exists"})
            res = {**body, "id": str(uuid.uuid4())}
            store[res["id"]] = res
            return httpx.Response(201, json=res)
        if rid not in store:
            return httpx.Response(404, json={"message": "not found"})
        if request.method == "GET":
            return httpx.Response(200, json=store[rid])
        if request.method == "PUT":
            store[rid] = {**body, "id": rid}
            return httpx.Response(200, json=store[rid])
        if request.method == "PATCH":
            for op in body["Operations"]:
                store[rid][op["path"]] = op["value"]
            return httpx.Response(200, json=store[rid])
        if request.method == "DELETE":
            del store[rid]
            return httpx.Response(204)
        raise AssertionError(request.method)


@pytest.fixture()
def env(monkeypatch):
    settings.reset()
    monkeypatch.setattr(I.time, "sleep", lambda s: None)
    with mock_aws():
        store = Store(resource=boto3.resource("dynamodb", region_name="us-east-1"), prefix="inttest")
        store.ensure_tables()
        set_store(store)
        hr = HRService(store)
        hr.seed()
        integ = I.Integrations(store, hr)
        integ.ensure_defaults()
        fake = FakeScim()
        http = httpx.Client(transport=httpx.MockTransport(fake.handler))
        integ.create({"type": "scim2", "id": "fake", "connection": {"baseUrl": BASE}})
        integ.update("fake", {"scope": {"mode": "query", "query": SCOPE}, "groups": {"patterns": ["team-payments"]}})

        class Env:
            pass
        e = Env()
        e.store, e.hr, e.integ, e.fake, e.http = store, hr, integ, fake, http
        e.engine = lambda iid="fake": I.Engine(store, hr, integ.raw(iid), http)
        e.runner = I.Runner(store, hr, http)
        e.sync = lambda iid="fake", **kw: e.runner.start(iid, background=False, **kw)
        yield e
        set_store(None)


def payments(env, n=0):
    return env.hr.evaluate_query(SCOPE)["emails"][n]


# ----------------------------------------------------------------- pure helpers
class TestHelpers:
    def test_evaluate_source(self):
        user = {"firstName": "Ann", "surname": "Lee", "email": "ann@x", "work": {"title": "Eng", "custom": {"team": "T"}}}
        ev = lambda s, ctx=None: I.evaluate_source(s, user, ctx or {"$role": "admin"})
        assert ev("") is None and ev(None) is None
        assert ev("$role") == "admin" and ev('"lit"') == "lit" and ev("'lit'") == "lit"
        assert ev("true") is True and ev("false") is False
        assert ev("{firstName}.{surname}") == "Ann.Lee" and ev("{nope.path}") is None
        assert ev("{firstName} {$role}") == "Ann admin" and ev("team") == "T" and ev("work.title") == "Eng"

    def test_validate_source(self):
        known = {"email", "work.title", "firstName", "work.custom.team"}
        for ok in ("", "$role", "true", '"x"', "{firstName}-{$fullName}", "team", "email"):
            I.validate_source(ok, known)
        for bad in ("nope", "{firstName}{nope}"):
            with pytest.raises(Invalid, match="unknown source field"):
                I.validate_source(bad, known)

    def test_transforms(self):
        T = I.TRANSFORMS
        assert T["lowercase"]("AbC") == "abc" and T["uppercase"]("a") == "A" and T["trim"](" a ") == "a"
        assert T["localPart"]("a@b") == "a" and T["domain"]("a@b") == "b" and T["domain"]("ab") == "ab"
        assert T["asciiFold"]("Ó Søren Groß æ") == "O Soren Gross ae" and T["removeSpaces"]("a b\tc") == "abc"
        assert T["slugify"]("Hello, Wörld!") == "hello-world"
        assert T["toString"](True) == "true" and T["toString"](3) == "3" and T["toString"]([1]) == "[1]"
        assert T["toString"](None) is None and T["toString"]("s") == "s"
        assert T["firstItem"]([1, 2]) == 1 and T["firstItem"]([]) is None and T["firstItem"]("x") == "x"
        for name in ("lowercase", "uppercase", "trim", "localPart", "domain", "asciiFold", "removeSpaces", "slugify"):
            assert T[name](5) == 5     # non-strings pass through

    def test_set_and_get_target(self):
        p = {"schemas": [I.USER_SCHEMA]}
        I.set_target(p, "name.givenName", "Ann")
        I.set_target(p, 'emails[type eq "work"].value', "a@x")
        I.set_target(p, 'emails[type eq "work"].display', "A")           # same element reused
        I.set_target(p, 'emails[type eq "home"].value', "h@x")           # second element not primary
        I.set_target(p, 'xThings[kind eq "k"].v', 1)                    # non-primary multi-valued attr
        I.set_target(p, "roles[].value", "admin")
        I.set_target(p, "groupsList[].value", ["a", "", None, "b"])
        I.set_target(p, f"{I.ENTERPRISE}:manager.value", "m1")
        I.set_target(p, f"{I.ENTERPRISE}:department", "D")
        I.set_target(p, "title", "")                                       # empty -> omitted
        assert p["emails"] == [{"type": "work", "primary": True, "value": "a@x", "display": "A"},
                               {"type": "home", "primary": False, "value": "h@x"}]
        assert p["xThings"] == [{"kind": "k", "v": 1}] and p["roles"] == [{"value": "admin", "primary": False}]
        assert p["groupsList"] == [{"value": "a", "primary": False}, {"value": "b", "primary": False}]
        assert p[I.ENTERPRISE] == {"manager": {"value": "m1"}, "department": "D"} and p["schemas"].count(I.ENTERPRISE) == 1
        assert "title" not in p
        with pytest.raises(Invalid, match="extension"):
            I.set_target(p, "urn:", "v")
        assert I.get_target(p, f"{I.ENTERPRISE}:manager.value") == "m1"
        assert I.get_target(p, 'emails[type eq "WORK"].value') == "a@x"
        assert I.get_target(p, 'emails[type eq "other"].value') == "a@x"   # falls back to the primary
        assert I.get_target({}, 'emails[type eq "work"].value') is None
        assert I.get_target(p, "roles[].value") == ["admin"] and I.get_target(p, "name.givenName") == "Ann"
        assert I.changed_fields(None, {"a": 1}) == [] and I.changed_fields({"a": 1, "b": 2}, {"a": 1, "c": 3}) == ["b", "c"]

    def test_reference_and_templates(self):
        ref = I.reference()
        assert set(ref["templates"]) == {"github-emu", "scim2"} and "lowercase" in ref["transforms"]
        assert ref["templates"]["github-emu"]["roleValues"] == I.GITHUB_ROLES


# ------------------------------------------------------------ CRUD & validation
class TestIntegrationStore:
    def test_defaults_list_get_masking(self, env, monkeypatch):
        ids = [i["id"] for i in env.integ.list()]
        assert ids == sorted(ids, key=str.lower) or set(ids) == {"github-emu", "scim-target", "fake"}
        gh = env.integ.get("github-emu")
        assert gh["connection"]["token"] == I.MASK and gh["connection"]["tokenSource"] == "stored"
        assert gh["connection"]["usesBuiltInMock"] and gh["required"] and gh["roleValues"] == I.GITHUB_ROLES
        monkeypatch.setenv("INTEGRATION_TOKEN_SCIM_TARGET", "from-env")
        assert env.integ.raw("scim-target")["connection"]["token"] == "from-env"
        assert env.integ.get("scim-target")["connection"]["tokenSource"] == "env"
        monkeypatch.setenv("GITHUB_SCIM_TOKEN", "gh-env")
        settings.reset()
        assert env.integ.raw("github-emu")["connection"]["token"] == "gh-env"
        settings.reset()
        env.integ.update("fake", {"connection": {"token": ""}})
        assert env.integ.get("fake")["connection"]["tokenSource"] == "none"
        with pytest.raises(NotFound):
            env.integ.raw("ghost")

    def test_create_update_delete(self, env):
        with pytest.raises(Invalid, match="type"):
            env.integ.create({"type": "ldap", "id": "x1"})
        with pytest.raises(Invalid, match="id must"):
            env.integ.create({"type": "scim2", "id": "X"})
        with pytest.raises(Conflict):
            env.integ.create({"type": "scim2", "id": "fake"})
        c = env.integ.create({"type": "github-emu", "id": "gh2", "name": "GH 2", "description": "d",
                              "connection": {"baseUrl": "https://api.github.com/scim/v2/enterprises/x", "token": "t"}})
        assert c["name"] == "GH 2" and c["description"] == "d" and c["connection"]["tokenSet"]
        with pytest.raises(NotFound):
            env.integ.update("ghost", {})
        u = env.integ.update("gh2", {"connection": {"token": I.MASK, "password": None, "tokenSet": False,
                                                    "tokenSource": "x", "usesBuiltInMock": True, "username": "u"},
                                     "name": "Renamed", "ignored": 1})
        assert u["name"] == "Renamed" and env.integ.raw("gh2")["connection"]["token"] == "t"
        env.integ.update("gh2", {"mappings": [m for m in u["mappings"] if m["target"] != "roles[].value"]})
        assert len(env.integ.reset_mappings("gh2")["mappings"]) == len(I.templates()["github-emu"]["defaults"]["mappings"])
        env.store.put("scim-state", {"key": "gh2#user#a@x", "scimId": "1"})
        assert env.integ.delete("gh2") == {"deleted": "gh2"} and env.store.get("scim-state", "gh2#user#a@x") is None
        with pytest.raises(NotFound):
            env.integ.delete("gh2")
        draft = env.integ.draft("fake", {"name": "Draft"})
        assert draft["name"] == "Draft" and env.integ.raw("fake")["name"] != "Draft"

    @pytest.mark.parametrize("patch,msg", [
        ({"connection": {"baseUrl": "ftp://x"}}, "baseUrl"),
        ({"connection": {"authType": "oauth"}}, "authType"),
        ({"connection": {"headers": ["x"]}}, "headers"),
        ({"scope": {"mode": "random"}}, "scope.mode"),
        ({"scope": {"mode": "group", "group": "ghost"}}, "scope group"),
        ({"scope": {"mode": "query", "query": "team =="}}, "expected a value"),
        ({"mappings": [{"target": "", "source": "email"}]}, "needs a target"),
        ({"mappings": [{"target": "userName", "source": "email"}, {"target": "userName", "source": "email"}]}, "mapped twice"),
        ({"mappings": [{"target": "userName", "source": "nope"}]}, "unknown source"),
        ({"mappings": [{"target": "userName", "source": "email", "transforms": ["x"]}]}, "unknown transform"),
        ({"mappings": [{"target": "userName", "source": "email", "applyOn": "never"}]}, "applyOn"),
        ({"mappings": [{"target": "title", "source": "title"}]}, "required target"),
        ({"roles": {"rules": [{"group": "ghost", "value": "x"}]}}, "role rule group"),
        ({"actions": {"deprovision": "archive"}}, "deprovision"),
        ({"groups": {"patterns": "team-*"}}, "glob"),
        ({"groups": {"nameSource": "id"}}, "nameSource"),
    ])
    def test_validation(self, env, patch, msg):
        with pytest.raises(Invalid, match=msg):
            env.integ.update("fake", patch)

    def test_github_role_validation(self, env):
        with pytest.raises(Invalid, match="role value"):
            env.integ.update("github-emu", {"roles": {"rules": [{"group": "all-staff", "value": "god"}]}})
        with pytest.raises(Invalid, match="default role"):
            env.integ.update("github-emu", {"roles": {"enabled": True, "defaultValue": "god"}})

    def test_group_references(self, env):
        refs = env.integ.group_references("github-enterprise-owners")
        assert refs == ["integration:github-emu:roles"]
        assert "integration:github-emu:scope" in env.integ.group_references("github-emu-users")

    def test_migration_from_single_scim_config(self, env):
        env.store.clear("integrations")
        env.store.put("settings", {"key": "scim", "config": {
            "baseUrl": "https://api.github.com/scim/v2/enterprises/old", "token": "old", "scopeGroup": "all-staff",
            "groupPatterns": ["div-*"], "excludeGroupPatterns": ["div-x"], "skipEmptyGroups": False,
            "roleMappings": [{"group": "github-enterprise-owners", "role": "billing_manager"}],
            "defaultRole": "guest_collaborator", "deprovisionMode": "delete"}})
        env.store.put("scim-state", {"key": "user#a@x", "scimId": "1"})
        env.store.put("scim-state", {"key": "fake#user#b@x", "scimId": "2"})
        env.integ.ensure_defaults()
        gh = env.integ.raw("github-emu")
        assert gh["connection"]["baseUrl"].endswith("/old") and gh["scope"]["group"] == "all-staff"
        assert gh["roles"]["rules"] == [{"group": "github-enterprise-owners", "value": "billing_manager"}]
        assert gh["actions"]["deprovision"] == "delete" and gh["groups"]["exclude"] == ["div-x"]
        assert env.store.get("scim-state", "github-emu#user#a@x")["scimId"] == "1"
        assert env.store.get("scim-state", "fake#user#b@x")
        env.integ.ensure_defaults()      # idempotent

    def test_migration_with_invalid_old_config_disables(self, env):
        env.store.clear("integrations")
        env.store.put("settings", {"key": "scim", "config": {"scopeGroup": "deleted-group"}})
        env.integ.ensure_defaults()
        assert env.integ.raw("github-emu")["enabled"] is False


# ------------------------------------------------------------- engine: mapping
class TestMappingAndPreview:
    def test_scope_modes(self, env):
        users, groups = env.hr.users_map(), env.hr.groups_map()
        r = env.hr.resolver(users, groups)
        eng = env.engine()
        assert eng.scope_emails(users, groups, r, {"mode": "all"}) == set(users)
        assert eng.scope_emails(users, groups, r, {"mode": "group", "group": "team-payments"})
        with pytest.raises(Invalid):
            eng.scope_emails(users, groups, r, {"mode": "query", "query": "team =="})
        with pytest.raises(Invalid, match="does not exist"):
            eng.scope_emails(users, groups, r, {"mode": "group", "group": "ghost"})
        sp = eng.scope_preview({"mode": "query", "query": SCOPE}, sample=2)
        assert sp["count"] == sp["active"] and len(sp["sample"]) == 2 and sp["inactive"] == 0
        assert [g["name"] for g in eng.groups_preview()] == ["team-payments"]

    def test_preview_actions_through_lifecycle(self, env):
        email = payments(env)
        eng = env.engine
        assert eng().preview_user(email)["nextAction"] == "create"
        with pytest.raises(NotFound):
            eng().preview_user("ghost@x.example")
        outsider = env.hr.evaluate_query('team == "Mobile" and status == "Active"')["emails"][0]
        assert eng().preview_user(outsider)["nextAction"] == "none (out of scope)"
        hired = env.hr.create_user({"email": "hired@halcyon.example", "firstName": "H", "surname": "I",
                                    "internal": {"lifecycleStatus": "hired"}, "work": {"custom": {"team": "Payments"}}})
        env.integ.update("fake", {"scope": {"mode": "query", "query": 'team == "Payments"'}})
        assert eng().preview_user(hired["email"])["nextAction"].startswith("none (inactive")
        env.integ.update("fake", {"scope": {"mode": "query", "query": SCOPE}})
        assert env.sync()["status"] == "succeeded"
        assert eng().preview_user(email)["nextAction"] == "no change"
        env.hr.update_user(email, {"work": {"title": "Changed Title"}})
        pv = eng().preview_user(email)
        assert pv["nextAction"] == "update" and pv["provisioned"] and pv["scimId"]
        env.integ.update("fake", {"scope": {"mode": "query", "query": 'team == "Payments"'}})
        env.hr.terminate_user(email)
        assert eng().preview_user(email)["nextAction"] == "deprovision"
        env.sync()
        env.hr.update_user(email, {"internal": {"lifecycleStatus": "employed"}})
        assert eng().preview_user(email)["nextAction"] == "reactivate"
        env.integ.update("fake", {"scope": {"mode": "query", "query": 'team == "Nobody"'}})
        assert eng().preview_user(email)["nextAction"] in ("none (out of scope)", "deprovision")

    def test_apply_on_create_keeps_first_value_and_roles(self, env):
        mappings = env.integ.raw("fake")["mappings"] + [
            {"target": "nickName", "source": "title", "applyOn": "create"},
            {"target": "x-role", "source": "$role", "default": "fallback"}]
        mappings = [m for m in mappings if m["target"] != "nickName"][:-1] + mappings[-2:]
        env.integ.update("fake", {"mappings": mappings,
                                  "roles": {"enabled": True, "rules": [{"group": "people-managers", "value": "lead"}],
                                            "defaultValue": "member"}})
        email = payments(env)
        first_title = env.hr.get_user(email)["work"]["title"]
        pv = env.engine().preview_user(email)
        assert pv["payload"]["nickName"] == first_title and pv["role"] in ("lead", "member")
        env.sync()
        env.hr.update_user(email, {"work": {"title": "Totally New"}})
        assert env.engine().preview_user(email)["payload"]["nickName"] == first_title
        env.integ.update("fake", {"roles": {"enabled": False}})
        pv = env.engine().preview_user(email)
        assert pv["role"] is None and pv["payload"]["x-role"] == "fallback"


# ------------------------------------------------------- engine: plan & execute
class TestPlanAndExecute:
    def test_full_cycle_and_actions(self, env):
        run = env.sync()
        assert run["status"] == "succeeded" and run["counts"]["create_user"] > 3
        assert env.engine().plan()["counts"] == {}
        assert env.engine().plan(full=True)["counts"]["update_user"] > 3
        assert len(env.fake.groups) == 1
        # rename the pushed group -> summary mentions rename
        env.hr.update_group("team-payments", {"displayName": "Payments Squad"})
        ops = env.engine().plan()["operations"]
        assert any(o["op"] == "update_group" and "rename" in o["summary"] for o in ops)
        # stop pushing groups -> delete_group
        env.integ.update("fake", {"groups": {"enabled": False}})
        assert [o["op"] for o in env.engine().plan()["operations"]] == ["delete_group"]
        env.sync()
        assert env.fake.groups == {}
        # updates disabled -> no update ops; creates disabled -> no create ops
        email = payments(env)
        env.hr.update_user(email, {"work": {"title": "Another"}})
        env.integ.update("fake", {"actions": {"update": False}})
        assert env.engine().plan()["counts"] == {}
        new = env.hr.create_user({"email": "newbie@halcyon.example", "firstName": "N", "surname": "B",
                                  "work": {"custom": {"team": "Payments"}}})
        env.integ.update("fake", {"actions": {"create": False}})
        assert "create_user" not in env.engine().plan()["counts"]
        # deprovision none -> leavers untouched
        env.integ.update("fake", {"actions": {"create": True, "update": True, "deprovision": "none"}})
        env.hr.terminate_user(email)
        assert "deactivate_user" not in env.engine().plan()["counts"]
        # delete mode: removed from HRIS entirely
        env.integ.update("fake", {"actions": {"deprovision": "delete"}})
        env.sync()
        victim = env.hr.evaluate_query(SCOPE + " and isManager == false")["emails"][0]
        env.hr.delete_user(victim)
        ops = env.engine().plan()["operations"]
        assert {"op": "delete_user", "key": victim} in [{"op": o["op"], "key": o["key"]} for o in ops]
        assert new["email"]

    def test_recovery_paths(self, env):
        fake = env.fake
        a, b = payments(env, 0), payments(env, 1)
        # pre-existing remote user -> 409 -> adopted (by userName); another found by externalId
        pre = {"userName": a, "externalId": "x", "id": "pre-1"}
        fake.users["pre-1"] = pre
        ext_id = env.hr.get_user(b)["id"]
        fake.users["pre-2"] = {"userName": "someone-else", "externalId": ext_id, "id": "pre-2"}
        orig = fake.handler

        def conflict_for_b(request):
            if request.method == "POST" and request.url.path.endswith("/Users") and b.encode() in request.content:
                return httpx.Response(409, json={"detail": "exists"})
            return orig(request)
        env.http._transport = httpx.MockTransport(conflict_for_b)
        run = env.sync()
        details = {r["key"]: r.get("detail") for r in run["results"] if r["op"] == "create_user"}
        assert details[a] == "adopted existing" and details[b] == "adopted existing"
        env.http._transport = httpx.MockTransport(fake.handler)
        # remote user vanished -> update gets 404 -> recreated
        st = env.store.get("scim-state", f"fake#user#{a}")
        del fake.users[st["scimId"]]
        env.hr.update_user(a, {"work": {"title": "Re-created"}})
        res = {r["key"]: r for r in env.sync()["results"]}
        assert res[a]["detail"] == "recreated (missing remotely)"
        # group vanished -> recreated; group exists on create -> adopted by displayName
        gid = env.store.get("scim-state", "fake#group#team-payments")["scimId"]
        del fake.groups[gid]
        env.hr.update_user(b, {"work": {"title": "Touch"}})
        env.store.put("scim-state", {**env.store.get("scim-state", "fake#group#team-payments"), "hash": "stale"})
        res = {r["key"]: r for r in env.sync()["results"]}
        assert res["team-payments"]["detail"] == "recreated (missing remotely)"
        env.store.delete("scim-state", "fake#group#team-payments")
        res = {r["key"]: r for r in env.sync()["results"]}
        assert res["team-payments"]["detail"] == "adopted existing"
        # deactivate a user that is already gone remotely -> state cleared
        st = env.store.get("scim-state", f"fake#user#{b}")
        del fake.users[st["scimId"]]
        env.hr.terminate_user(b)
        res = {r["key"]: r for r in env.sync()["results"]}
        assert res[b]["detail"] == "already gone remotely; state cleared"
        assert env.store.get("scim-state", f"fake#user#{b}") is None

    def test_failures_are_recorded_and_retried_next_run(self, env):
        fake = env.fake
        fake.fail[("POST", "Users")] = [500, "network", 409]   # 409 with nothing to adopt -> failure
        fake.fail[("POST", "Groups")] = [400]
        run = env.sync()
        assert run["status"] == "partial"
        failed = [r for r in run["results"] if r["status"] == "failed"]
        assert {r.get("httpStatus") for r in failed} >= {500, 409, 400}
        assert any("ConnectError" in r["error"] for r in failed)
        assert env.sync()["status"] == "succeeded"                 # next run completes the job
        # group members skipped when their user failed in the same run
        env.store.clear("scim-state")
        fake.users.clear(), fake.groups.clear()
        fake.fail[("POST", "Users")] = [500]
        res = {r["key"]: r for r in env.sync()["results"]}
        assert res["team-payments"]["skippedMembers"]
        # update path skipped members + failures of non-404 errors propagate
        fake.fail[("PUT", "Groups")] = [500]
        env.hr.update_user(payments(env), {"work": {"title": "x"}})
        env.store.put("scim-state", {**env.store.get("scim-state", "fake#group#team-payments"), "hash": "stale"})
        fake.fail[("POST", "Users")] = [500]
        res = {r["key"]: r for r in env.sync()["results"]}
        assert res["team-payments"]["status"] == "failed"
        fake.fail[("PUT", "Users")] = [500]
        fake.fail[("PATCH", "Users")] = [500]
        env.hr.update_user(payments(env), {"work": {"title": "y"}})
        env.hr.terminate_user(payments(env, 2))
        run = env.sync()
        assert {r["op"] for r in run["results"] if r["status"] == "failed"} >= {"update_user", "deactivate_user"}

    def test_unreachable_target_stops_after_consecutive_network_errors(self, env):
        env.fake.fail[("POST", "Users")] = ["network"] * 50
        env.fake.fail[("POST", "Groups")] = ["network"] * 5
        run = env.sync()
        results = run["results"]
        assert run["status"] == "failed" and run["done"] == run["total"] == len(results)
        limit = I.Engine.MAX_CONSECUTIVE_NETWORK_ERRORS
        assert all("ConnectError" in r["error"] for r in results[:limit])
        assert all(r["error"] == "skipped: target unreachable" for r in results[limit:])
        calls_before = len(env.fake.calls)
        env.fake.fail.clear()
        assert env.sync()["status"] == "succeeded"            # nothing was recorded as done, so all is retried
        assert len(env.fake.calls) > calls_before

    def test_conflict_without_match_and_group_conflict(self, env):
        fake = env.fake
        fake.fail[("POST", "Groups")] = [409]
        run = env.sync()
        assert any(r["key"] == "team-payments" and r["status"] == "failed" for r in run["results"])

    def test_client_features(self, env, monkeypatch):
        seen = []

        def handler(request):
            seen.append(request)
            if len(seen) == 1:
                return httpx.Response(429, headers={"Retry-After": ""}, json={})
            if request.url.params.get("startIndex") == "1":
                return httpx.Response(200, json={"totalResults": 3, "Resources": [{"id": 1}, {"id": 2}]})
            if request.url.params.get("startIndex") == "3":
                return httpx.Response(200, json={"totalResults": 3, "Resources": [{"id": 3}]})
            if request.method == "DELETE":
                return httpx.Response(204)
            return httpx.Response(200, json={"Resources": []})
        sleeps = []
        monkeypatch.setattr(I.time, "sleep", lambda s: sleeps.append(s))
        doc = env.integ.raw("github-emu")
        doc["connection"].update(authType="basic", username="u", password="p", headers={"X-Extra": "1"},
                                 requestDelayMs=5, apiVersion="2026-03-10")
        client = I.ScimClient(doc, httpx.Client(transport=httpx.MockTransport(handler)))
        assert [r["id"] for r in client.list_all("Users", page=2)] == [1, 2, 3]
        assert client.find("Users", "userName", "x") is None
        assert client.call("DELETE", "/Users/1") == {}
        assert client.call("GET", "http://elsewhere/abs") == {"Resources": []}
        req = seen[0]
        assert req.headers["Authorization"].startswith("Basic ") and req.headers["X-Extra"] == "1"
        assert req.headers["X-GitHub-Api-Version"] == "2026-03-10"
        assert 0.005 in sleeps and 1.0 in sleeps                    # pacing + Retry-After fallback
        empty = I.ScimClient(doc, httpx.Client(transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"totalResults": 5, "Resources": []}))))
        assert empty.list_all("Users") == []
        none_auth = dict(doc, connection={**doc["connection"], "authType": "none"})
        none_auth["type"] = "scim2"
        c = I.ScimClient(none_auth, httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))))
        assert "Authorization" not in c.headers and "X-GitHub-Api-Version" not in c.headers
        default = I.ScimClient(env.integ.raw("fake"))                 # builds its own httpx client
        assert isinstance(default.http, httpx.Client)

    def test_test_connection(self, env):
        assert env.engine().test_connection()["ok"]
        env.fake.fail[("GET", "Users")] = [401, "text", "network"]
        r = env.engine().test_connection()
        assert not r["ok"] and r["httpStatus"] == 401 and r["error"] == "injected 401"
        r = env.engine().test_connection()
        assert r["httpStatus"] == 502 and "bad gateway" in r["error"]
        assert "ConnectError" in env.engine().test_connection()["error"]


# ------------------------------------------------------------------ verify
class TestVerify:
    def test_verify_reports_every_kind_of_drift(self, env):
        assert env.sync()["status"] == "succeeded"
        assert env.engine().verify()["ok"]
        fake = env.fake
        a, b, c = payments(env, 0), payments(env, 1), payments(env, 2)
        by_name = {u["userName"]: u for u in fake.users.values()}
        del fake.users[by_name[a]["id"]]                                      # missing
        by_name[b]["displayName"] = "Wrong"                                   # mismatch
        by_name[c]["userName"] = c.upper()                                    # case-insensitive match
        fake.users["extra"] = {"id": "extra", "userName": "intruder@x", "active": True}   # unexpected
        group = next(iter(fake.groups.values()))
        group["members"] = group["members"][1:] + [{"value": "ghost-id"}]    # member diffs + unknown id
        report = env.engine().verify()
        assert not report["ok"]
        u = report["users"]
        assert [m["email"] for m in u["missing"]] == [a]
        assert u["mismatched"][0]["differences"][0]["attribute"] == "displayName"
        assert [x["userName"] for x in u["unexpected"]] == ["intruder@x"]
        diff = report["groups"]["memberDiffs"][0]
        assert diff["extraCount"] == 1 and "<unknown id ghost-id>" in diff["extra"]
        fake.groups.clear()
        assert env.engine().verify()["groups"]["missing"] == ["team-payments"]

    def test_verify_deprovisioned_and_out_of_scope(self, env):
        env.integ.update("fake", {"scope": {"mode": "query", "query": 'team == "Payments"'}})
        env.sync()
        a = payments(env, 0)
        env.hr.terminate_user(a)
        r = env.engine().verify()
        assert [w["email"] for w in r["users"]["wronglyActive"]] == [a]   # inactive, still active remotely
        env.sync()
        rep = env.engine().verify()
        assert rep["ok"], {k: rep["users"][k] for k in ("missing", "mismatched", "wronglyActive", "unexpected")} | {"g": rep["groups"]}
        # out of scope but still active remotely (state exists, desired doesn't include them)
        b = payments(env, 1)
        env.integ.update("fake", {"scope": {"mode": "query", "query": f'team == "Payments" and email != "{b}"'}})
        r = env.engine().verify()
        assert any(w["email"] == b for w in r["users"]["wronglyActive"])
        env.integ.update("fake", {"actions": {"deprovision": "none"}})
        assert not any(w["email"] == b for w in env.engine().verify()["users"]["wronglyActive"])

    def test_verify_target_unreachable_and_groups_disabled(self, env):
        env.integ.update("fake", {"groups": {"enabled": False}})
        env.sync()
        r = env.engine().verify()
        assert r["ok"] and r["groups"]["expected"] == 0
        env.fake.fail[("GET", "Users")] = [503]
        r = env.engine().verify()
        assert r["ok"] is False and "503" in r["error"]


# -------------------------------------------------------------------- runner
class TestRunner:
    def test_disabled_busy_errors_and_history(self, env, monkeypatch):
        env.integ.update("fake", {"enabled": False})
        with pytest.raises(Conflict, match="disabled"):
            env.sync()
        env.integ.update("fake", {"enabled": True})
        lock = env.runner._lock("fake")
        lock.acquire()
        try:
            with pytest.raises(Conflict, match="already running"):
                env.sync()
        finally:
            lock.release()
        # plan failure releases the lock
        env.store.put("integrations", {**env.integ.raw("fake"), "scope": {"mode": "group", "group": "ghost"}})
        with pytest.raises(Invalid):
            env.sync()
        assert lock.acquire(blocking=False)
        lock.release()
        env.integ.update("fake", {"scope": {"mode": "query", "query": SCOPE}})
        # execution crash -> run recorded as error
        monkeypatch.setattr(I.Engine, "execute", lambda self, plan, progress=None: 1 / 0)
        run = env.sync()
        assert run["status"] == "error" and "ZeroDivisionError" in run["error"]
        monkeypatch.undo()
        settings.reset()
        # background run completes and is recorded
        run = env.runner.start("fake", background=True)
        assert run["status"] == "running"
        for t in threading.enumerate():
            if t is not threading.current_thread() and t.daemon:
                t.join(timeout=60)
        final = env.runner.get_run(run["id"])
        assert final["status"] in ("succeeded", "partial") and final["finishedAt"]
        assert env.runner.runs("fake", limit=1)[0]["id"] == run["id"]
        assert all("results" not in r for r in env.runner.runs())
        with pytest.raises(NotFound):
            env.runner.get_run("nope")

    def test_progress_flushes_during_long_runs(self, env, monkeypatch):
        clock = iter(range(0, 10_000, 2))
        monkeypatch.setattr(I.time, "time", lambda: next(clock))
        run = env.sync()
        assert run["status"] == "succeeded" and run["done"] == run["total"]

    def test_follow_up_pass_for_manager_references(self, env):
        env.integ.update("fake", {"scope": {"mode": "group", "group": "dept-product-engineering"},
                                  "groups": {"enabled": False}})
        run = env.sync()
        assert any(r["summary"].startswith("follow-up") for r in run["results"])
        assert env.engine().plan()["counts"] == {}



class TestRemainingPaths:
    def test_state_is_per_integration_and_excludes_apply(self, env):
        env.store.put("scim-state", {"key": "github-emu#user#someone@x.example", "scimId": "x", "active": True})
        users, groups = env.engine().state()
        assert users == {} and groups == {}                       # other integrations' state is ignored
        env.integ.update("fake", {"groups": {"patterns": ["team-*"], "exclude": ["team-p*"], "skipEmpty": False}})
        names = [g["name"] for g in env.engine().groups_preview()]
        assert names and "team-payments" not in names

    def test_disabled_user_leaving_scope_needs_no_operation(self, env):
        env.sync()
        a = payments(env)
        env.hr.terminate_user(a)
        env.sync()                                               # a is now disabled on the target
        env.integ.update("fake", {"scope": {"mode": "query", "query": SCOPE + f' and email != "{a}"'}})
        assert all(o["key"] != a for o in env.engine().plan()["operations"])

    def test_execute_without_progress_and_update_group_skipped_members(self, env):
        env.sync()
        a, b = payments(env, 0), payments(env, 1)
        env.hr.update_user(a, {"work": {"title": "Changed"}})
        env.store.put("scim-state", {**env.store.get("scim-state", "fake#group#team-payments"), "hash": "stale"})
        plan = env.engine().plan()
        st = env.store.get("scim-state", f"fake#user#{b}")
        env.store.put("scim-state", {**st, "active": False})    # b is not an active provisioned id this run
        results = env.engine().execute(plan)                       # no progress callback
        group = next(r for r in results if r["key"] == "team-payments")
        assert group["status"] == "ok" and b in group["skippedMembers"]

    def test_verify_ignores_out_of_scope_users_already_removed_remotely(self, env):
        env.sync()
        a = payments(env)
        st = env.store.get("scim-state", f"fake#user#{a}")
        del env.fake.users[st["scimId"]]
        env.integ.update("fake", {"scope": {"mode": "query", "query": SCOPE + f' and email != "{a}"'}})
        report = env.engine().verify()
        assert not report["users"]["missing"] and not report["users"]["wronglyActive"]

    def test_run_ids_sort_chronologically_within_a_second(self, env):
        first, second = env.sync(), env.sync()
        assert first["id"] < second["id"]
        assert [r["id"] for r in env.runner.runs("fake", limit=2)] == [second["id"], first["id"]]
