"""HR service: people, groups, org chart, meta, seeding, import/export (moto DynamoDB, no HTTP)."""
import boto3
import pytest
from moto import mock_aws

from mockhris import settings
from mockhris.hr import Conflict, HRService, Invalid, NotFound, deep_merge, user_summary
from mockhris.store import Store, set_store


@pytest.fixture()
def hr():
    settings.reset()
    with mock_aws():
        store = Store(resource=boto3.resource("dynamodb", region_name="us-east-1"), prefix="hrtest")
        store.ensure_tables()
        set_store(store)
        svc = HRService(store)
        svc.seed()
        yield svc
        set_store(None)


def one(hr, query):
    return hr.list_users(query=query, limit=1)["items"][0]["email"]


def test_deep_merge_and_summary():
    base = {"a": {"b": 1, "c": 2}, "work": {"reportsTo": {"email": "x"}}}
    merged = deep_merge(base, {"a": {"b": 9}, "work": {"reportsTo": "y"}, "n": None})
    assert merged == {"a": {"b": 9, "c": 2}, "work": {"reportsTo": "y"}, "n": None}
    assert base["a"]["b"] == 1
    s = user_summary({"email": "e@x", "id": "1"})
    assert s["email"] == "e@x" and s["avatar"].endswith("/avatars/1.svg") and s["isManager"] is False


class TestUsers:
    def test_listing_filters_search_paging(self, hr):
        everyone = hr.list_users()
        assert everyone["total"] > 400 and "work" not in everyone["items"][0]
        assert hr.list_users(full=True, limit=1)["items"][0]["work"]
        page = hr.list_users(limit=10, offset=10)
        assert len(page["items"]) == 10 and page["total"] == everyone["total"]
        emea = hr.list_users(filters={"region": "emea", "division": "", "status": None})
        assert emea["total"] and all(u["region"] == "EMEA" for u in emea["items"])
        email = everyone["items"][0]["email"]
        assert hr.list_users(search=email.split("@")[0])["total"] >= 1
        assert hr.list_users(search="HSG-10001")["total"] == 1
        with pytest.raises(Invalid):
            hr.list_users(query="nope == 1")

    def test_get_and_groups_not_found(self, hr):
        with pytest.raises(NotFound):
            hr.get_user("ghost@x.example")
        with pytest.raises(NotFound):
            hr.user_groups("ghost@x.example")

    def test_create_validation(self, hr):
        with pytest.raises(Invalid, match="valid 'email'"):
            hr.create_user({"email": "not-an-email", "firstName": "a", "surname": "b"})
        with pytest.raises(Invalid, match="firstName"):
            hr.create_user({"email": "a@b.example"})
        existing = hr.list_users(limit=1)["items"][0]["email"]
        with pytest.raises(Conflict):
            hr.create_user({"email": existing, "firstName": "a", "surname": "b"})
        for bad, msg in [({"internal": {"lifecycleStatus": "zombie"}}, "lifecycleStatus"),
                         ({"payroll": {"employment": {"type": "Volunteer"}}}, "employment type"),
                         ({"work": {"startDate": "yesterday"}}, "startDate"),
                         ({"work": {"reportsTo": "ghost@x.example"}}, "does not exist")]:
            with pytest.raises(Invalid, match=msg):
                hr.create_user({"email": "new@halcyon.example", "firstName": "N", "surname": "U", **bad})

    def test_create_minimal_and_explicit_overrides(self, hr):
        u = hr.create_user({"email": "Min@Halcyon.example", "firstName": "Min", "surname": "Imal"})
        assert u["email"] == "min@halcyon.example" and u["internal"]["status"] == "Active"
        assert u["work"]["employeeIdInCompany"].startswith("HSG-") and u["about"]["avatar"].endswith(".svg")
        assert u["work"]["reportsTo"] is None and u["displayName"] == "Min Imal"
        v = hr.create_user({"email": "sub@ferrocene.example", "firstName": "Sub", "surname": "Sid",
                            "address": {"country": "Custom"},
                            "work": {"site": "Berlin", "custom": {"division": "Ferrocene Labs", "region": "LATAM"}}})
        assert v["address"]["country"] == "Custom"                     # explicit address kept
        assert v["work"]["custom"]["region"] == "LATAM"                # explicit region kept
        assert v["work"]["custom"]["legalEntity"] == "Ferrocene Labs GmbH"
        assert v["payroll"]["employment"]["legalEntity"] == "Ferrocene Labs GmbH"
        hired = hr.create_user({"email": "future@halcyon.example", "firstName": "F", "surname": "S",
                                "internal": {"lifecycleStatus": "hired"}, "work": {"site": "Unknown Site"}})
        assert hired["internal"]["status"] == "Inactive" and hired["work"].get("siteId") is None

    def test_update_rules(self, hr):
        email = one(hr, 'team == "Payments" and isManager == false and status == "Active"')
        with pytest.raises(NotFound):
            hr.update_user("ghost@x.example", {})
        with pytest.raises(Invalid, match="rename"):
            hr.update_user(email, {"email": "other@x.example"})
        u = hr.update_user(email, {"id": "hacked", "firstName": "Renamed"})
        assert u["id"] != "hacked" and u["displayName"].startswith("Renamed")
        u = hr.update_user(email, {"displayName": "Custom Display", "surname": "Changed"})
        assert u["displayName"] == "Custom Display"
        with pytest.raises(Invalid, match="themselves"):
            hr.update_user(email, {"work": {"reportsTo": email}})
        with pytest.raises(Invalid, match="required"):
            hr.update_user(email, {"firstName": ""})
        u = hr.update_user(email, {"work": {"reportsTo": None}})
        assert u["work"]["reportsTo"] is None and u["work"]["secondLevelManager"] is None
        u = hr.update_user(email, {"internal": {"lifecycleStatus": "terminated"}})
        assert u["internal"]["terminationDate"]                       # defaulted to today
        u = hr.update_user(email, {"internal": {"lifecycleStatus": "employed"}})
        assert u["internal"]["terminationDate"] is None

    def test_terminate_rename_delete(self, hr):
        email = one(hr, 'isManager == false and status == "Active"')
        with pytest.raises(Invalid, match="YYYY-MM-DD"):
            hr.terminate_user(email, "14/11/2026")
        assert hr.terminate_user(email, "2026-01-31", "Redundancy")["internal"]["terminationReason"] == "Redundancy"
        with pytest.raises(Invalid):
            hr.rename_user(email, "bad")
        with pytest.raises(NotFound):
            hr.rename_user("ghost@x.example", "new@x.example")
        other = one(hr, 'isManager == true')
        with pytest.raises(Conflict):
            hr.rename_user(email, other)
        # a manager's rename updates the reportsTo email of their reports
        mgr = hr.get_user(other)
        report = mgr["work"]["directReports"][0]
        hr.rename_user(other, "renamed.manager@halcyon.example")
        assert hr.get_user(report)["work"]["reportsTo"]["email"] == "renamed.manager@halcyon.example"
        with pytest.raises(NotFound):
            hr.delete_user("ghost@x.example")
        with pytest.raises(Conflict, match="reassign"):
            hr.delete_user("renamed.manager@halcyon.example")
        hr.add_group_members("all-staff", [email])
        assert hr.delete_user(email) == {"deleted": email}
        assert email not in hr.get_group("all-staff", resolve=False)["members"]

    def test_rename_moves_scim_state(self, hr):
        email = one(hr, 'isManager == false and status == "Active"')
        hr.store.put("scim-state", {"key": f"user#{email}", "scimId": "x"})
        hr.rename_user(email, "moved@halcyon.example")
        assert hr.store.get("scim-state", "user#moved@halcyon.example")["scimId"] == "x"
        assert hr.store.get("scim-state", f"user#{email}") is None


class TestGroups:
    def test_list_get_search_and_types(self, hr):
        groups = hr.list_groups()
        assert {g["type"] for g in groups} == {"static", "dynamic", "mixed"}
        assert all("memberCount" in g for g in groups)
        assert [g["name"] for g in hr.list_groups(search="enterprise owners")] == ["github-enterprise-owners"]
        assert "memberCount" not in hr.list_groups(with_counts=False)[0]
        g = hr.get_group("github-emu-users")
        assert g["references"] and g["memberCount"] == len(g["resolvedMembers"])
        assert "resolvedMembers" not in hr.get_group("all-staff", resolve=False)
        with pytest.raises(NotFound):
            hr.get_group("ghost")

    def test_create_update_members_delete(self, hr):
        email = one(hr, 'status == "Active"')
        g = hr.create_group({"name": "t-static", "members": [email.upper(), " "], "description": " d "})
        assert g["type"] == "static" and g["members"] == [email] and g["description"] == "d"
        with pytest.raises(Conflict):
            hr.create_group({"name": "t-static"})
        with pytest.raises(NotFound):
            hr.update_group("ghost", {})
        with pytest.raises(Invalid, match="primary key"):
            hr.update_group("t-static", {"name": "other"})
        g = hr.update_group("t-static", {"query": 'team == "Payments"', "ignored": 1})
        assert g["type"] == "mixed"
        g = hr.update_group("t-static", {"query": "  "})
        assert "query" not in g
        assert hr.remove_group_members("t-static", [email])["members"] == []
        with pytest.raises(NotFound):
            hr.delete_group("ghost")
        assert hr.delete_group("t-static") == {"deleted": "t-static"}

    def test_used_by_ignores_broken_queries(self, hr):
        hr.store.put("groups", {"name": "broken", "query": "((("})
        assert "broken" not in hr.get_group("all-staff")["usedBy"]
        bad = [r for r in hr.validate_groups() if not r["ok"]]
        assert [r["name"] for r in bad] == ["broken"]
        listed = next(g for g in hr.list_groups() if g["name"] == "broken")
        assert listed["memberCount"] is None and listed["error"]
        with pytest.raises(Invalid):
            hr.get_group("broken")

    def test_evaluate_query(self, hr):
        r = hr.evaluate_query('team == "Payments"', limit=2)
        assert r["count"] > 2 and len(r["emails"]) == 2
        assert len(hr.evaluate_query('team == "Payments"')["emails"]) == r["count"]
        with pytest.raises(Invalid):
            hr.evaluate_query("team ==")


class TestOrgAndAdmin:
    def test_meta_stats_org_chart(self, hr):
        m = hr.meta()
        assert "Engineering" in m["org"] and "Sydney" in m["sites"] and "team" in m["aliases"]
        s = hr.stats()
        assert s["users"] > 400 and sum(s["byRegion"].values()) == s["users"]
        roots = hr.org_chart()
        assert len(roots) == 1 and roots[0]["reports"]
        shallow = hr.org_chart(depth=0)[0]
        assert "reports" not in shallow and shallow["reportCount"] > 0
        ceo = roots[0]["email"]
        assert hr.org_chart(root=ceo.upper(), depth=1)["email"] == ceo
        with pytest.raises(NotFound):
            hr.org_chart(root="ghost@x.example")
        total = lambda nodes: sum(1 + total(n.get("reports", [])) for n in nodes)
        assert total(hr.org_chart(include_terminated=True)) > total(hr.org_chart())

    def test_export_import_backfill(self, hr):
        bundle = hr.export()
        assert len(bundle["users"]) > 400 and bundle["groups"]
        email = bundle["users"][0]["email"]
        hr.store.put("users", {**hr.get_user(email), "about": {"avatar": None}})
        assert hr.backfill_avatars() == 1 and hr.backfill_avatars() == 0
        assert hr.user_by_id(bundle["users"][0]["id"])["email"] == email
        assert hr.user_by_id("nope") is None
        small = {"users": [{**u, "email": u["email"].upper(), "about": {}} for u in bundle["users"][:3]],
                 "groups": [{"name": "imported", "members": [bundle["users"][0]["email"]]}]}
        for u in small["users"]:
            u["work"]["reportsTo"] = None
        assert hr.import_bundle(small, replace=True) == {"users": 3, "groups": 1}
        assert hr.stats()["users"] == 3 and hr.get_user(email)["about"]["avatar"]
        assert hr.import_bundle({"users": [], "groups": [{"name": "extra"}]}, replace=False)["users"] == 3
        with pytest.raises(Invalid):
            hr.import_bundle({"users": [], "groups": [{"name": "bad", "members": ["ghost@x.example"]}]})


def test_site_change_overrides_and_sparse_records(hr):
    email = one(hr, 'division == "Engineering" and isManager == false and status == "Active"')
    u = hr.update_user(email, {"work": {"site": "London"}})
    assert u["address"]["city"] == "London" and u["work"]["custom"]["region"] == "EMEA"
    u = hr.update_user(email, {"work": {"site": "Tokyo", "custom": {"region": "Custom", "legalEntity": "Custom Ltd"}}})
    assert u["work"]["custom"]["region"] == "Custom" and u["work"]["custom"]["legalEntity"] == "Custom Ltd"
    assert u["address"]["city"] == "Tokyo" and u["payroll"]["employment"]["legalEntity"] == "Custom Ltd"
    u = hr.update_user(email, {"work": {"startDate": None}})
    assert u["work"]["startDate"] is None
    # sparse records (no division/department/team/site) don't break meta
    hr.create_user({"email": "sparse@halcyon.example", "firstName": "S", "surname": "P",
                    "work": {"department": "Lonely", "custom": {"division": "Solo"}}})
    hr.create_user({"email": "sparse2@halcyon.example", "firstName": "S", "surname": "Q"})
    m = hr.meta()
    assert m["org"]["Solo"] == {"Lonely": []}


def test_import_keeps_custom_avatar(hr):
    u = hr.export()["users"][0]
    u["about"]["avatar"] = "https://example.com/me.png"
    u["work"]["reportsTo"] = None
    hr.import_bundle({"users": [u], "groups": []}, replace=True)
    assert hr.get_user(u["email"])["about"]["avatar"] == "https://example.com/me.png"
