"""Front ends: built-in GitHub EMU mock, REST API odds and ends, app middleware, MCP tools, CLI."""
import json

import pytest
from fastapi.testclient import TestClient

from mockhris import __main__ as cli
from mockhris import mcp_server as m
from mockhris import services, settings
from mockhris.integrations import Engine

BASE = "/mock-github/scim/v2/enterprises/halcyon"
AUTH = {"Authorization": "Bearer mock-scim-token"}
PATCH = "urn:ietf:params:scim:api:messages:2.0:PatchOp"


def user_body(n, **extra):
    return {"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"], "externalId": f"ext-{n}",
            "userName": f"user{n}@x.example", "displayName": f"User {n}", "active": True,
            "emails": [{"value": f"user{n}@x.example", "type": "work", "primary": True}], **extra}


def ops(*operations):
    return {"schemas": [PATCH], "Operations": list(operations)}


# ------------------------------------------------------------- GitHub mock
class TestMockGitHub:
    def test_users(self, client):
        c = client
        assert c.get(f"{BASE}/Users", headers={"Authorization": "Bearer wrong"}).status_code == 401
        r = c.post(f"{BASE}/Users", headers=AUTH, json=user_body(1, roles=[{"value": "enterprise_owner"}]))
        assert r.status_code == 201
        u1 = r.json()
        assert u1["meta"]["location"].endswith(f"/Users/{u1['id']}")
        c.post(f"{BASE}/Users", headers=AUTH, json=user_body(2))
        for body, scim_type in [
            ({**user_body(3), "emails": [{"value": "x"}]}, "invalidValue"),
            ({**user_body(3), "roles": [{"value": "god"}]}, "invalidValue"),
            ({k: v for k, v in user_body(3).items() if k != "active"}, "invalidValue"),
            (user_body(1), "uniqueness"),
            ({**user_body(3), "externalId": "ext-1"}, "uniqueness"),
        ]:
            r = c.post(f"{BASE}/Users", headers=AUTH, json=body)
            assert r.json()["scimType"] == scim_type, body
        ok_uuid_role = c.post(f"{BASE}/Users", headers=AUTH,
                              json=user_body(4, roles=[{"value": "27d9891d-2c17-4f45-a262-781a0e55c80a"}]))
        assert ok_uuid_role.status_code == 201
        # list, filter, paging
        listing = c.get(f"{BASE}/Users", headers=AUTH, params={"count": 2, "startIndex": 2}).json()
        assert listing["totalResults"] == 3 and listing["itemsPerPage"] == 2 and listing["startIndex"] == 2
        for flt in (f'userName eq "USER1@x.example"', 'externalId eq "ext-2"', f'id eq "{u1["id"]}"'):
            assert c.get(f"{BASE}/Users", headers=AUTH, params={"filter": flt}).json()["totalResults"] == 1
        assert c.get(f"{BASE}/Users", headers=AUTH, params={"filter": "userName co x"}).json()["scimType"] == "invalidFilter"
        assert c.get(f"{BASE}/Users", headers=AUTH, params={"filter": 'title eq "x"'}).status_code == 400
        assert c.get(f"{BASE}/Users/{u1['id']}", headers=AUTH).json()["userName"] == "user1@x.example"
        assert c.get(f"{BASE}/Users/nope", headers=AUTH).status_code == 404
        assert c.get(f"/mock-github/scim/v2/enterprises/other/Users/{u1['id']}", headers=AUTH).status_code == 404
        # PUT
        r = c.put(f"{BASE}/Users/{u1['id']}", headers=AUTH, json=user_body(1, userName="renamed@x.example"))
        assert r.json()["userName"] == "renamed@x.example"
        # PATCH variants
        uid = u1["id"]
        r = c.patch(f"{BASE}/Users/{uid}", headers=AUTH, json=ops(
            {"op": "replace", "path": "active", "value": False},
            {"op": "add", "path": "name.givenName", "value": "Ren"},
            {"op": "replace", "value": {"displayName": "Patched"}},
            {"op": "remove", "path": "name.givenName"}))
        body = r.json()
        assert body["active"] is False and body["displayName"] == "Patched" and "givenName" not in body["name"]
        for bad in (ops({"op": "move", "path": "active", "value": 1}),
                    ops({"op": "replace", "path": 'emails[type eq "work"].value', "value": "z"}),
                    ops({"op": "replace", "value": "not-a-dict"}),
                    {"schemas": [PATCH], "Operations": []}):
            assert c.patch(f"{BASE}/Users/{uid}", headers=AUTH, json=bad).status_code == 400
        # snapshot shows the EMU login
        snap = c.get("/api/mock-scim").json()
        assert {"renamed_halcyon", "user2_halcyon"} <= {u["login"] for u in snap["users"]}
        assert c.delete(f"{BASE}/Users/{uid}", headers=AUTH).status_code == 204
        assert c.delete("/api/mock-scim").json() == {"reset": True}
        assert c.get("/api/mock-scim").json() == {"users": [], "groups": []}

    def test_groups(self, client):
        c = client
        ids = [c.post(f"{BASE}/Users", headers=AUTH, json=user_body(n)).json()["id"] for n in range(3)]
        assert c.post(f"{BASE}/Groups", headers=AUTH, json={"displayName": "G"}).status_code == 400
        assert c.post(f"{BASE}/Groups", headers=AUTH,
                      json={"externalId": "g", "displayName": "G", "members": [{"display": "x"}]}).status_code == 400
        g = c.post(f"{BASE}/Groups", headers=AUTH, json={"externalId": "g", "displayName": "G", "members": [
            {"value": ids[0]}, {"value": ids[0], "display": "a"}]}).json()   # duplicates collapse; last wins
        assert len(g["members"]) == 1 and g["members"][0]["displayName"] == "a"
        assert c.post(f"{BASE}/Groups", headers=AUTH, json={"externalId": "g2", "displayName": "g"}).status_code == 409
        gid = g["id"]
        listed = c.get(f"{BASE}/Groups", headers=AUTH, params={"excludedAttributes": "members"}).json()
        assert "members" not in listed["Resources"][0]
        assert c.get(f"{BASE}/Groups", headers=AUTH, params={"filter": 'displayName eq "g"'}).json()["totalResults"] == 1
        assert c.get(f"{BASE}/Groups/{gid}", headers=AUTH).json()["displayName"] == "G"
        r = c.put(f"{BASE}/Groups/{gid}", headers=AUTH, json={"externalId": "g", "displayName": "G2",
                                                              "members": [{"value": ids[1]}]})
        assert [x["value"] for x in r.json()["members"]] == [ids[1]]
        patch = lambda *o: c.patch(f"{BASE}/Groups/{gid}", headers=AUTH, json=ops(*o))
        assert len(patch({"op": "add", "path": "members", "value": [{"value": ids[0]}, {"value": ids[2]}]})
                   .json()["members"]) == 3
        assert len(patch({"op": "remove", "path": f'members[value eq "{ids[0]}"]'}).json()["members"]) == 2
        assert len(patch({"op": "remove", "path": "members", "value": [{"value": ids[1]}]}).json()["members"]) == 1
        assert len(patch({"op": "replace", "path": "members", "value": [{"value": ids[0]}]}).json()["members"]) == 1
        r = patch({"op": "replace", "path": "displayName", "value": "Renamed"},
                  {"op": "add", "value": {"externalId": "g-new", "members": [{"value": ids[1]}, {"value": ids[2]}]}})
        assert r.json()["displayName"] == "Renamed" and r.json()["externalId"] == "g-new" and len(r.json()["members"]) == 2
        assert patch({"op": "move", "path": "members", "value": []}).status_code == 400
        assert patch({"op": "replace", "path": "title", "value": "x"}).status_code == 400
        # deleting a user removes them from groups; the snapshot resolves member userNames
        c.delete(f"{BASE}/Users/{ids[1]}", headers=AUTH)
        snap = c.get("/api/mock-scim").json()
        assert snap["groups"][0]["members"] == ["user2@x.example"]
        assert c.delete(f"{BASE}/Groups/{gid}", headers=AUTH).status_code == 204
        assert c.get(f"{BASE}/Groups/{gid}", headers=AUTH).status_code == 404

    def test_snapshot_handles_dangling_member(self, client):
        from mockhris.store import get_store
        get_store().put("mock-scim", {"id": "g1", "resourceType": "Group", "enterprise": "halcyon",
                                      "data": {"displayName": "G", "externalId": "g", "members": [{"value": "gone"}]},
                                      "meta": {"created": "x", "lastModified": "x"}})
        assert client.get("/api/mock-scim").json()["groups"][0]["members"] == ["gone"]


# --------------------------------------------------------------- REST/app
class TestApiAndApp:
    def test_misc_endpoints(self, client):
        c = client
        assert c.get("/", follow_redirects=False).headers["location"] == "/ui/"
        assert c.get("/ui/").status_code == 200
        assert c.get("/api/health").json() == {"ok": True}
        assert c.get("/api/meta").json()["aliases"]["team"] == "work.custom.team"
        assert c.get("/api/org-chart", params={"depth": 1}).json()[0]["reports"]
        r = c.get("/avatars/does-not-exist.svg")
        assert r.status_code == 200 and r.headers["cache-control"] == "public, max-age=3600"
        assert c.get("/avatars/does-not-exist.svg").text == r.text            # served from the in-process cache
        import mockhris.app as app_mod
        original = app_mod.AVATAR_CACHE_SECONDS
        app_mod.AVATAR_CACHE_SECONDS = 0                                         # expired -> re-rendered
        try:
            assert c.get("/avatars/does-not-exist.svg").text == r.text
        finally:
            app_mod.AVATAR_CACHE_SECONDS = original
        users = c.get("/api/users", params={"limit": 2, "team": "Payments", "full": True}).json()
        assert users["total"] > 2 and users["items"][0]["work"]
        email = users["items"][0]["email"]
        assert c.put(f"/api/users/{email}", json={"work": {"title": "Via PUT"}}).json()["work"]["title"] == "Via PUT"
        assert c.get("/api/groups", params={"search": "copilot"}).json()[0]["name"] == "github-copilot"
        assert c.get("/api/groups/all-staff", params={"resolve": False}).json().get("resolvedMembers") is None
        r = c.post("/api/groups/all-staff/members", json={"emails": [email]})
        assert email in r.json()["members"]
        assert email not in c.post("/api/groups/all-staff/members/remove", json={"emails": [email]}).json()["members"]
        assert c.get("/api/users/nobody@x.example").status_code == 404
        seeded = c.post("/api/admin/seed", json={"seed": 3}).json()
        assert seeded["seed"] == 3 and seeded["users"] > 400
        bundle = c.get("/api/admin/export").json()
        assert c.post("/api/admin/import", params={"replace": True}, json=bundle).json()["users"] == len(bundle["users"])

    def test_integration_endpoints(self, client, monkeypatch):
        c = client
        # SCIM calls made by API endpoints go through the in-process app, never the network
        monkeypatch.setattr(services, "engine", lambda iid, http=None, draft=None:
                            services.Runner(services.get_store(), services.hr(), c).engine(iid, draft))
        ref = c.get("/api/integrations/reference").json()
        assert "github-emu" in ref["templates"]
        assert c.post("/api/integrations/github-emu/reset-mappings").status_code == 200
        assert c.post("/api/integrations/github-emu/scope-preview", json={"mode": "all"}).json()["count"] > 400
        assert c.post("/api/integrations/github-emu/groups-preview", json={}).json()
        draft = {"groups": {"patterns": ["team-payments"]}}
        assert [g["name"] for g in c.post("/api/integrations/github-emu/groups-preview",
                                          json={"draft": draft}).json()] == ["team-payments"]
        assert c.post("/api/integrations/github-emu/test").json()["target"].endswith("/halcyon")
        assert c.get("/api/integrations/github-emu/runs").json() == []
        assert c.get("/api/runs").json() == [] and c.get("/api/runs/nope").status_code == 404
        assert c.get("/api/integrations/github-emu/state").json() == {"users": [], "groups": []}
        services.runner(http=c).start("github-emu", background=False)
        st = c.get("/api/integrations/github-emu/state").json()
        assert st["users"] and st["groups"][0]["members"] > 0 and "payload" not in st["users"][0]
        assert c.get("/api/stats").json()["lastSync"]["integrationId"] == "github-emu"
        run_id = c.get("/api/runs").json()[0]["id"]
        assert c.get(f"/api/runs/{run_id}").json()["results"]
        assert c.post("/api/integrations/github-emu/verify").json()["users"]["expectedActive"] > 0
        assert c.post("/api/integrations/github-emu/sync", json={}).status_code == 202   # background (fails offline)
        assert c.delete("/api/integrations/github-emu").json() == {"deleted": "github-emu"}

    def test_api_key_guard(self, client, monkeypatch):
        monkeypatch.setenv("MOCKHRIS_API_KEY", "s3cret")
        settings.reset()
        try:
            assert client.get("/api/stats").status_code == 401
            assert client.get("/api/health").status_code == 200
            assert client.get("/api/stats", headers={"X-API-Key": "s3cret"}).status_code == 200
            assert client.get("/api/stats", headers={"Authorization": "Bearer s3cret"}).status_code == 200
            assert client.get("/api/stats", headers={"Authorization": "Bearer nope"}).status_code == 401
            assert client.post("/mcp", json={}).status_code == 401
            assert client.get("/ui/").status_code == 200
        finally:
            monkeypatch.delenv("MOCKHRIS_API_KEY")
            settings.reset()

    def test_extra_mcp_hosts_and_no_autoseed(self, monkeypatch):
        import boto3
        from moto import mock_aws

        from mockhris.app import create_app
        from mockhris.store import Store, set_store
        monkeypatch.setenv("MCP_ALLOWED_HOSTS", "mockhris.internal:*")
        monkeypatch.setenv("AUTO_SEED", "false")
        settings.reset()
        with mock_aws():
            store = Store(resource=boto3.resource("dynamodb", region_name="us-east-1"), prefix="noseed")
            store.ensure_tables()
            store.put("users", {"email": "legacy@x.example", "id": "77", "firstName": "L", "surname": "G",
                                "about": {"avatar": None}})       # record from before avatars existed
            set_store(store)
            with TestClient(create_app(), base_url="http://mockhris.internal:8000") as c:
                assert c.get("/api/stats").json()["users"] == 1
                assert c.get("/api/users/legacy@x.example").json()["about"]["avatar"].endswith("/avatars/77.svg")
                r = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                           headers={"Accept": "application/json, text/event-stream"})
                assert r.status_code != 421 and r.status_code != 403
            set_store(None)
        settings.reset()


# -------------------------------------------------------------------- MCP
class TestMcpTools:
    def test_every_tool(self, client, monkeypatch):
        monkeypatch.setattr(services, "runner", lambda http=None: services.Runner(
            services.get_store(), services.hr(), client))
        monkeypatch.setattr(services, "engine", lambda iid, http=None, draft=None: services.runner().engine(iid, draft))
        assert m.describe_schema()["aliases"] and m.hris_stats()["users"] > 400
        found = m.search_users(query='team == "Payments"', limit=3)
        email = found["items"][0]["email"]
        assert found["total"] > 3 and m.get_user(email)["email"] == email
        assert m.get_user_groups(email)
        new = m.create_user("mcp.user@halcyon.example", "Mcp", "User", title="Engineer", division="Engineering",
                            department="Product Engineering", team="Payments", site="Sydney",
                            manager_email=email, job_level="IC2", start_date="2026-01-05",
                            extra={"personal": {"pronouns": "They/Them"}})
        assert new["personal"]["pronouns"] == "They/Them" and new["work"]["reportsTo"]["email"] == email
        assert m.update_user("mcp.user@halcyon.example", {"work": {"title": "Senior"}})["work"]["title"] == "Senior"
        plain = m.create_user("mcp.plain@halcyon.example", "Plain", "User")
        assert plain["work"]["reportsTo"] is None and plain["payroll"]["employment"]["type"] == "Permanent"
        m.delete_user("mcp.plain@halcyon.example")
        assert m.rename_user("mcp.user@halcyon.example", "mcp.renamed@halcyon.example")["email"].startswith("mcp.renamed")
        assert m.terminate_user("mcp.renamed@halcyon.example")["internal"]["status"] == "Inactive"
        assert m.delete_user("mcp.renamed@halcyon.example") == {"deleted": "mcp.renamed@halcyon.example"}
        assert m.org_chart(depth=1)
        assert m.list_groups(search="copilot")[0]["name"] == "github-copilot"
        assert "resolvedMembers" not in m.get_group("all-staff", include_members=False)
        assert m.get_group("team-payments")["resolvedMembers"]
        assert m.evaluate_query('team == "Payments"', limit=1)["count"] > 1
        g = m.create_group("mcp-group", members=[email], query='team == "Mobile"')
        assert g["type"] == "mixed"
        assert m.update_group("mcp-group", description="d", query="")["type"] == "static"
        assert m.add_group_members("mcp-group", [found["items"][1]["email"]])["memberCount"] == 2
        assert m.remove_group_members("mcp-group", [email])["memberCount"] == 1
        assert m.delete_group("mcp-group") == {"deleted": "mcp-group"}
        # integrations
        assert {i["id"] for i in m.list_integrations()} >= {"github-emu", "scim-target"}
        assert m.integration_reference()["transforms"]
        assert m.get_integration("github-emu")["connection"]["token"] == "********"
        made = m.create_integration("scim2", "mcp-int", name="From MCP",
                                    base_url="http://localhost/mock-github/scim/v2/enterprises/mcp", token="mock-scim-token")
        assert made["name"] == "From MCP"
        assert m.update_integration("mcp-int", {"scope": {"mode": "query", "query": 'team == "Payments"'},
                                                "groups": {"enabled": False}})["scope"]["mode"] == "query"
        assert m.create_integration("scim2", "mcp-int2")["connection"]["baseUrl"]
        assert m.test_integration("mcp-int")["ok"]
        assert m.preview_integration_user("mcp-int", email)["inScope"]
        plan = m.plan_integration("mcp-int", max_operations=2)
        assert len(plan["operations"]) == 2 and plan["operationsTruncated"] > 0
        assert "payload" not in plan["operations"][0]
        run = m.sync_integration("mcp-int")
        assert run["status"] in ("succeeded", "partial") and "results" not in run
        assert m.verify_integration("mcp-int")["users"]["expectedActive"] > 0
        assert m.list_sync_runs("mcp-int")[0]["id"] == run["id"]
        assert m.get_sync_run(run["id"])["results"]
        snap = m.mock_github_snapshot(include_users=True)
        assert snap["userCount"] and snap["users"]
        assert "users" not in m.mock_github_snapshot()
        assert m.delete_integration("mcp-int2") == {"deleted": "mcp-int2"}

    def test_domain_errors_become_tool_errors(self, client):
        from mcp.server.mcpserver.exceptions import ToolError
        with pytest.raises(ToolError, match="not found"):
            m.get_user("ghost@x.example")
        with pytest.raises(ToolError, match="expected a value"):
            m.evaluate_query("team ==")

    def test_stdio_main(self, client, monkeypatch):
        calls = []
        monkeypatch.setattr(m.mcp, "run", lambda transport: calls.append(transport))
        m.main()
        assert calls == ["stdio"]


# -------------------------------------------------------------------- CLI
class TestCli:
    def test_commands(self, client, capsys, tmp_path, monkeypatch):
        monkeypatch.setattr(services, "runner", lambda http=None: services.Runner(
            services.get_store(), services.hr(), client))
        monkeypatch.setattr(services, "engine", lambda iid, http=None, draft=None: services.runner().engine(iid, draft))

        def run(*argv):
            code = cli.main(list(argv))
            return code, capsys.readouterr()

        code, out = run("check")
        assert code == 0 and "ok    all-staff" in out.out
        code, out = run("query", 'team == "Payments"')
        assert code == 0 and "@halcyon.example" in out.out
        export = tmp_path / "e.json"
        assert run("export", str(export))[0] == 0 and json.loads(export.read_text(encoding="utf-8"))["users"]
        code, out = run("import", str(export), "--merge")
        assert code == 0 and json.loads(out.out)["users"] > 400
        code, out = run("integrations")
        assert "github-emu" in out.out and "scim-target" in out.out
        code, out = run("plan", "github-emu")
        assert code == 0 and "create_user" in out.out
        code, out = run("sync", "github-emu")
        assert code == 0 and json.loads(out.out.splitlines()[-1])["status"] == "succeeded"
        code, out = run("verify", "github-emu")
        assert code == 0 and json.loads(out.out)["ok"] is True
        code, out = run("sync", "github-emu")                  # nothing to do -> still success
        assert code == 0
        code, out = run("query", "team ==")
        assert code == 1 and "error:" in out.err
        code, out = run("seed", "--seed", "5")
        assert code == 0 and json.loads(out.out)["seed"] == 5

    def test_failures_and_exit_codes(self, client, capsys, monkeypatch):
        from mockhris.store import get_store
        get_store().put("groups", {"name": "broken", "query": "((("})
        assert cli.main(["check"]) == 1 and "ERROR broken" in capsys.readouterr().out
        get_store().delete("groups", "broken")
        # a sync with failures exits 1 and prints them; verify drift exits 1
        monkeypatch.setattr(services, "runner", lambda http=None: services.Runner(
            services.get_store(), services.hr(), client))
        monkeypatch.setattr(services, "engine", lambda iid, http=None, draft=None: services.runner().engine(iid, draft))
        services.integrations().update("github-emu", {"connection": {"token": "wrong"}})
        assert cli.main(["sync", "github-emu"]) == 1
        assert "FAILED create_user" in capsys.readouterr().out
        assert cli.main(["verify", "github-emu"]) == 1

    def test_serve_and_mcp_dispatch(self, monkeypatch):
        import uvicorn
        seen = []
        monkeypatch.setattr(uvicorn, "run", lambda app, host, port: seen.append((app, host, port)))
        monkeypatch.setattr(m, "main", lambda: seen.append("mcp"))
        assert cli.main(["serve", "--port", "9999"]) == 0
        assert cli.main(["mcp"]) == 0
        assert seen == [("mockhris.app:app", "127.0.0.1", 9999), "mcp"]
