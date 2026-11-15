import json

from mockhris import services
from mockhris.store import get_store


def first_user(client, query):
    return client.get("/api/users", params={"query": query, "limit": 1}).json()["items"][0]


# ------------------------------------------------------------------ HR + groups
def test_seeded_and_all_groups_resolve(client):
    stats = client.get("/api/stats").json()
    assert stats["users"] > 400 and stats["groups"] > 100
    bad = [g for g in client.get("/api/groups-validate").json() if not g["ok"]]
    assert bad == []


def test_query_endpoint_and_errors(client):
    r = client.post("/api/query", json={"query": 'memberOf("engineering-org") and region == "EMEA"'})
    assert r.status_code == 200 and r.json()["count"] > 0
    r = client.post("/api/query", json={"query": 'tema == "x"'})
    assert r.status_code == 422 and "unknown field" in r.json()["detail"]


def test_user_lifecycle(client):
    mgr = first_user(client, 'title == "Engineering Manager, Payments"')
    body = {"email": "Test.Person@halcyon.example", "firstName": "Test", "surname": "Person",
            "work": {"title": "Software Engineer", "department": "Product Engineering", "site": "London",
                     "reportsTo": mgr["email"], "custom": {"division": "Engineering", "team": "Payments",
                                                          "jobLevel": "IC2"}}}
    r = client.post("/api/users", json=body)
    assert r.status_code == 201, r.text
    u = r.json()
    assert u["email"] == "test.person@halcyon.example"
    assert u["work"]["custom"]["region"] == "EMEA"
    assert u["work"]["custom"]["legalEntity"] == "Halcyon Systems UK Ltd"
    assert u["work"]["reportsTo"]["email"] == mgr["email"]
    assert u["email"] in client.get(f"/api/users/{mgr['email']}").json()["work"]["directReports"]
    assert client.post("/api/users", json=body).status_code == 409

    groups = {g["name"] for g in client.get(f"/api/users/{u['email']}/groups").json()}
    assert {"team-payments", "github-emu-users", "region-emea"} <= groups

    r = client.patch(f"/api/users/{u['email']}", json={"work": {"site": "Sydney"}})
    assert r.json()["work"]["custom"]["region"] == "APAC"

    r = client.post(f"/api/users/{u['email']}/terminate", json={})
    assert r.json()["internal"]["status"] == "Inactive"
    assert u["email"] not in client.get(f"/api/users/{mgr['email']}").json()["work"]["directReports"]

    # manager cannot be deleted while they have reports; cycles are rejected
    assert client.delete(f"/api/users/{mgr['email']}").status_code == 409
    r = client.patch(f"/api/users/{mgr['email']}", json={"work": {"reportsTo": u["email"]}})
    assert r.status_code == 422 and "cycle" in r.json()["detail"]


def test_rename_updates_static_membership(client):
    owners = client.get("/api/groups/github-enterprise-owners").json()
    static = owners["members"][0]
    r = client.post(f"/api/users/{static}/rename", json={"newEmail": "renamed.owner@halcyon.example"})
    assert r.status_code == 200, r.text
    owners = client.get("/api/groups/github-enterprise-owners").json()
    assert "renamed.owner@halcyon.example" in owners["members"] and static not in owners["members"]


def test_group_crud_nesting_and_cycles(client):
    r = client.post("/api/groups", json={"name": "test-a", "query": 'memberOf("team-payments")'})
    assert r.status_code == 201 and r.json()["memberCount"] > 0
    r = client.post("/api/groups", json={"name": "test-b", "query": 'memberOf("test-a") or team == "Mobile"'})
    assert r.status_code == 201
    r = client.patch("/api/groups/test-a", json={"query": 'memberOf("test-b")'})
    assert r.status_code == 422 and "circular" in r.json()["detail"]
    assert client.delete("/api/groups/test-a").status_code == 409  # used by test-b
    assert client.post("/api/groups", json={"name": "Bad Name"}).status_code == 422
    r = client.post("/api/groups", json={"name": "test-c", "members": ["nobody@halcyon.example"]})
    assert r.status_code == 422
    assert client.delete("/api/groups/test-b").status_code == 200
    assert client.delete("/api/groups/test-a").status_code == 200


# ------------------------------------------------------------------ integrations
def sync(client, iid="github-emu", **kw):
    return services.runner(http=client).start(iid, background=False, **kw)


def test_default_integrations_and_avatars(client):
    ids = {i["id"] for i in client.get("/api/integrations").json()}
    assert {"github-emu", "scim-target"} <= ids
    gh = client.get("/api/integrations/github-emu").json()
    assert gh["connection"]["token"] == "********" and gh["connection"]["usesBuiltInMock"]
    u = first_user(client, 'team == "Payments"')
    r = client.get(f"/avatars/{u['id']}.svg")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml") and "<svg" in r.text
    assert client.get(f"/api/users/{u['email']}").json()["about"]["avatar"].endswith(f"/avatars/{u['id']}.svg")
    chart = client.get("/api/org-chart?depth=1").json()
    assert len(chart) == 1 and chart[0]["avatar"] and chart[0]["reports"]


def test_github_sync_against_mock_target(client):
    assert services.engine("github-emu", http=client).test_connection()["ok"]
    plan = client.post("/api/integrations/github-emu/plan", json={}).json()
    assert plan["counts"]["create_user"] == plan["activeInScope"]
    run = sync(client)
    assert run["status"] == "succeeded", [r for r in run["results"] if r["status"] != "ok"][:3]
    snap = client.get("/api/mock-scim").json()
    assert len(snap["users"]) == plan["activeInScope"]
    assert [u for u in snap["users"] if "enterprise_owner" in u["roles"]]
    assert client.post("/api/integrations/github-emu/plan", json={}).json()["counts"] == {}
    assert services.engine("github-emu", http=client).verify()["ok"]

    victim = first_user(client, 'memberOf("github-emu-users") and team == "Payments" and isManager == false')
    client.post(f"/api/users/{victim['email']}/terminate", json={})
    plan = client.post("/api/integrations/github-emu/plan", json={}).json()
    assert plan["counts"].get("deactivate_user") == 1
    assert any(o["op"] == "update_group" and o["key"] == "team-payments" and victim["email"] in o["removed"]
               for o in plan["operations"])
    # before syncing, verify detects the drift
    report = services.engine("github-emu", http=client).verify()
    assert not report["ok"] and any(w["email"] == victim["email"] for w in report["users"]["wronglyActive"])
    assert sync(client)["status"] == "succeeded"
    assert services.engine("github-emu", http=client).verify()["ok"]

    client.patch(f"/api/users/{victim['email']}", json={"internal": {"lifecycleStatus": "employed"}})
    assert client.post("/api/integrations/github-emu/plan", json={}).json()["counts"].get("reactivate_user") == 1

    client.delete("/api/integrations/github-emu/state")
    run = sync(client)
    assert run["status"] == "succeeded" and any(r.get("detail") == "adopted existing" for r in run["results"])

    client.put("/api/integrations/github-emu", json={"actions": {"deprovision": "delete"}})
    client.post(f"/api/users/{victim['email']}/terminate", json={})
    assert sync(client)["status"] == "succeeded"
    assert all(u["userName"] != victim["email"] for u in client.get("/api/mock-scim").json()["users"])


def test_verify_detects_remote_tampering(client):
    assert sync(client)["status"] == "succeeded"
    base = "/mock-github/scim/v2/enterprises/halcyon"
    h = {"Authorization": "Bearer mock-scim-token"}
    remote = client.get(f"{base}/Users", headers=h, params={"count": 1}).json()["Resources"][0]
    client.patch(f"{base}/Users/{remote['id']}", headers=h, json={
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        "Operations": [{"op": "replace", "path": "displayName", "value": "Tampered"}]})
    report = services.engine("github-emu", http=client).verify()
    assert not report["ok"]
    diff = report["users"]["mismatched"][0]["differences"][0]
    assert diff == {"attribute": "displayName", "expected": remote["displayName"], "actual": "Tampered"}


def test_custom_scim2_integration_mapping(client):
    r = client.post("/api/integrations", json={
        "type": "scim2", "id": "wiki", "name": "Team wiki",
        "connection": {"baseUrl": "http://localhost/mock-github/scim/v2/enterprises/wiki", "token": "mock-scim-token"}})
    assert r.status_code == 201, r.text
    mappings = r.json()["mappings"]
    mappings.append({"target": "nickName", "source": "{firstName}.{surname}", "transforms": ["asciiFold", "lowercase"]})
    mappings = [m for m in mappings[:-1] if m["target"] != "nickName"] + [mappings[-1]]
    r = client.put("/api/integrations/wiki", json={
        "mappings": mappings,
        "scope": {"mode": "query", "query": 'team == "Payments" and status == "Active"'},
        "groups": {"patterns": ["team-payments"]}})
    assert r.status_code == 200, r.text
    u = first_user(client, 'team == "Payments" and status == "Active"')
    pv = client.post("/api/integrations/wiki/preview", json={"email": u["email"]}).json()
    assert pv["inScope"] and pv["nextAction"] == "create"
    ent = pv["payload"]["urn:ietf:params:scim:schemas:extension:enterprise:2.0:User"]
    assert ent["department"] == "Product Engineering" and ent["division"] == "Engineering"
    assert pv["payload"]["emails"][0] == {"type": "work", "primary": True, "value": u["email"]}
    assert pv["payload"]["nickName"] == pv["payload"]["nickName"].lower() and "." in pv["payload"]["nickName"]
    run = sync(client, "wiki")
    assert run["status"] == "succeeded", run["results"][:3]
    # The built-in mock behaves like GitHub and drops the enterprise extension: verify must flag it.
    report = services.engine("wiki", http=client).verify()
    assert not report["ok"] and not report["users"]["missing"]
    flagged = {d["attribute"].rsplit(":", 1)[-1] for m in report["users"]["mismatched"] for d in m["differences"]}
    assert {"costCenter", "department", "division", "employeeNumber", "organization", "title"} <= flagged
    # keep only attributes a GitHub-style target stores -> everything now reconciles
    supported = {"externalId", "userName", "active", "displayName", 'emails[type eq "work"].value'}
    keep = [m for m in client.get("/api/integrations/wiki").json()["mappings"]
            if m["target"] in supported or m["target"].startswith("name.")]
    assert client.put("/api/integrations/wiki", json={"mappings": keep}).status_code == 200
    assert sync(client, "wiki")["status"] == "succeeded"
    report = services.engine("wiki", http=client).verify()
    assert report["ok"], json.dumps({k: report[k] for k in ("users", "groups")})[:1500]
    assert client.post("/api/integrations/wiki/plan", json={}).json()["counts"] == {}

    # validation: unknown source field, missing required target, bad transform, bad id
    bad = client.put("/api/integrations/wiki", json={"mappings": [{"target": "userName", "source": "nope"}]})
    assert bad.status_code == 422 and "unknown source" in bad.json()["detail"]
    bad = client.put("/api/integrations/wiki", json={"mappings": [{"target": "title", "source": "title"}]})
    assert bad.status_code == 422 and "userName" in bad.json()["detail"]
    bad = client.put("/api/integrations/wiki", json={"mappings": [
        {"target": "userName", "source": "email", "transforms": ["shout"]}]})
    assert bad.status_code == 422
    assert client.post("/api/integrations", json={"type": "scim2", "id": "Bad Id"}).status_code == 422
    assert client.delete("/api/integrations/wiki").status_code == 200


def test_integration_validation_and_group_refs(client):
    assert client.put("/api/integrations/github-emu", json={"scope": {"group": "nope"}}).status_code == 422
    r = client.put("/api/integrations/github-emu", json={"roles": {"rules": [{"group": "all-staff", "value": "admin"}]}})
    assert r.status_code == 422
    r = client.put("/api/integrations/github-emu", json={"connection": {"token": "abc123"}})
    assert r.json()["connection"]["token"] == "********"
    assert get_store().get("integrations", "github-emu")["connection"]["token"] == "abc123"
    client.put("/api/integrations/github-emu", json={"connection": {"token": "********"}})
    assert get_store().get("integrations", "github-emu")["connection"]["token"] == "abc123"
    r = client.delete("/api/groups/github-enterprise-owners")
    assert r.status_code == 409 and "integration:github-emu:roles" in r.json()["detail"]


def test_mock_scim_rejects_bad_requests(client):
    base = "/mock-github/scim/v2/enterprises/halcyon"
    assert client.get(f"{base}/Users").status_code == 401
    h = {"Authorization": "Bearer mock-scim-token"}
    r = client.post(f"{base}/Users", headers=h, json={"userName": "x"})
    assert r.status_code == 400 and r.json()["scimType"] == "invalidValue"
    r = client.post(f"{base}/Groups", headers=h, json={"externalId": "g", "displayName": "G",
                                                        "members": [{"value": "not-a-user"}]})
    assert r.status_code == 400


# -------------------------------------------------------------------------- MCP
def _mcp(client, method, params=None, id_=1):
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}},
                    headers={"Accept": "application/json, text/event-stream",
                             "MCP-Protocol-Version": "2025-06-18"})
    assert r.status_code == 200, r.text
    if r.headers["content-type"].startswith("text/event-stream"):
        data = [line[5:].strip() for line in r.text.splitlines() if line.startswith("data:")]
        return json.loads(data[-1])
    return r.json()


def test_mcp_endpoint(client):
    init = _mcp(client, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                       "clientInfo": {"name": "pytest", "version": "1"}})
    assert init["result"]["serverInfo"]["name"] == "MockHRIS"
    tools = {t["name"] for t in _mcp(client, "tools/list", id_=2)["result"]["tools"]}
    assert {"search_users", "evaluate_query", "create_group", "plan_integration", "sync_integration", "verify_integration"} <= tools
    res = _mcp(client, "tools/call", {"name": "evaluate_query",
                                      "arguments": {"query": 'team == "Payments"', "limit": 3}}, id_=3)["result"]
    assert not res.get("isError")
    payload = res.get("structuredContent") or json.loads(res["content"][0]["text"])
    assert payload["count"] > 0 and len(payload["emails"]) == 3
    res = _mcp(client, "tools/call", {"name": "get_user", "arguments": {"email": "nobody@x.example"}}, id_=4)
    assert res["result"]["isError"]
