"""Regenerate the screenshots in docs/images from a running MockHRIS (and, optionally, scim-target).

    python scripts/screenshots.py [--base http://localhost:8000] [--target http://localhost:8100]
    PLAYWRIGHT_CHANNEL=msedge python scripts/screenshots.py        # use an installed Edge/Chrome

Most captures are read-only. The integration captures run Plan, Sync and Verify against the
`scim-target` integration. If scim-target is reachable, one user's title is briefly changed
directly on the target to capture Verify's drift report, then a full resync repairs it.
"""
import argparse
import os
from pathlib import Path

import httpx
from playwright.sync_api import expect, sync_playwright

OUT = Path(__file__).resolve().parent.parent / "docs" / "images"
FADE_MS = 450   # let fade-in animations finish before capturing


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--target", default="http://localhost:8100")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    api = httpx.Client(base_url=args.base, timeout=300)
    payments = api.get("/api/users", params={"team": "Payments", "status": "Active", "limit": 1}).json()["items"][0]
    manager = api.get("/api/users", params={"query": 'title == "VP, Platform Engineering"', "limit": 1}).json()["items"][0]

    with sync_playwright() as p:
        channel = os.environ.get("PLAYWRIGHT_CHANNEL")
        browser = p.chromium.launch(**({"channel": channel} if channel else {}))
        page = browser.new_page(viewport={"width": 1440, "height": 900}, color_scheme="dark")
        page.set_default_timeout(30000)
        page.on("dialog", lambda d: d.accept())   # e.g. "Push changes to <target>?" before syncing

        def go(hash_, wait="#main > :not(.loading)"):
            page.goto(f"{args.base}/ui/{hash_}")
            page.wait_for_selector(wait)
            page.wait_for_timeout(FADE_MS)

        def shot(name):
            page.wait_for_timeout(FADE_MS)
            page.screenshot(path=str(OUT / f"{name}.png"))
            print("wrote", name)

        # ---- people
        go("#/dashboard", ".stat"); shot("dashboard")
        go("#/users", "#u-results tbody tr"); shot("people")
        page.fill("#u-query", 'memberOf("engineering-org") and region == "EMEA" and jobLevel in ["IC4", "IC5"]')
        page.click("#u-apply")
        page.wait_for_selector("#u-results tbody tr")
        shot("people-query")
        go(f"#/users/{manager['email']}", "#uf"); shot("profile")
        page.click(".tabs button[data-tab=json]"); shot("profile-raw")
        go("#/users/new", "#uf"); shot("new-person")

        # ---- groups
        go("#/groups", "#g-results tbody tr"); shot("groups")
        go("#/groups/github-enterprise-owners", "#m-list .lr"); shot("group-editor")
        go("#/groups/new", "#gf")
        page.fill("#gf [name=name]", "payments-oncall")
        page.fill("#gf [name=displayName]", "Payments On-call")
        page.fill("#gf [name=query]", 'memberOf("team-payments") and jobLevel in ["IC3", "IC4", "IC5"]')
        page.locator("#gf details summary").click()
        page.click("#q-test")
        page.wait_for_selector("#q-result .badge")
        shot("group-new-query")

        # ---- org chart
        page.evaluate("() => { try { localStorage.setItem('mockhris.org.view', 'chart') } catch (e) {} }")
        go("#/org", "#oc-canvas .oc-card"); shot("org-chart")
        page.locator(f'#oc-canvas .oc-card:has-text("Chief Technology Officer") .oc-toggle').click()
        page.locator(f'#oc-canvas .oc-card[data-email="{manager["email"]}"] .oc-toggle').click()
        page.click("#oc-fit")
        shot("org-chart-expanded")
        page.locator(f'#oc-canvas .oc-card[data-email="{manager["email"]}"] .oc-focus').click(force=True)
        first_team = page.locator("#oc-canvas .oc-card .oc-toggle").nth(1)
        first_team.click()
        page.click("#oc-fit")
        shot("org-chart-focus")
        page.click(".page-head .seg button[data-view=list]")
        page.wait_for_selector("#tree li")
        page.click("#o-expand")
        shot("org-list")
        page.evaluate("() => { try { localStorage.setItem('mockhris.org.view', 'chart') } catch (e) {} }")

        # ---- integrations
        go("#/integrations", ".int-card"); shot("integrations")
        page.click("#add-int")
        page.fill("#add-form [name=name]", "Atlassian Cloud")
        shot("add-integration")
        go("#/integrations/scim-target/connection", "#c-url"); shot("integration-connection")
        go("#/integrations/scim-target/users", "#s-preview .badge"); shot("users-in-scope")
        go("#/integrations/scim-target/mapping", "#m-rows tr")
        page.fill("#p-user", payments["email"])
        page.click("#p-go")
        page.wait_for_selector("#p-out pre")
        shot("attribute-mapping")
        go("#/integrations/scim-target/groups", "#g-prev .lr"); shot("group-provisioning")
        go("#/integrations/github-emu/mapping", "#r-rows")
        page.locator("#r-rows").scroll_into_view_if_needed()
        shot("role-rules")
        go("#/integrations/scim-target/actions", "input[name=dep]"); shot("provisioning-actions")

        # a joiner, a mover and a leaver, so Plan / Sync / Verify show real work
        joiner = "sam.newstarter@halcyon.example"
        if api.get(f"/api/users/{joiner}").status_code == 404:
            api.post("/api/users", json={
                "email": joiner, "firstName": "Sam", "surname": "Newstarter",
                "work": {"title": "Software Engineer", "department": "Product Engineering", "site": "Melbourne",
                         "reportsTo": payments["manager"],
                         "custom": {"division": "Engineering", "team": "Payments", "jobLevel": "IC2"}}})
        mover = api.get("/api/users", params={"team": "Core API", "status": "Active", "limit": 1}).json()["items"][0]
        api.patch(f"/api/users/{mover['email']}", json={"work": {"title": "Staff Software Engineer",
                                                                 "custom": {"jobLevel": "IC4"}}})
        leaver = api.get("/api/users", params={"query": 'team == "Mobile" and isManager == false',
                                               "status": "Active", "limit": 1}).json()["items"][0]
        api.post(f"/api/users/{leaver['email']}/terminate", json={"reason": "Resignation"})

        go("#/integrations/scim-target/overview", ".flow-step")
        shot("integration-overview")
        page.click("#ov-plan")
        page.wait_for_selector("#run-box .chips")
        shot("plan")
        page.click("#ov-sync")
        expect(page.locator("#run-box .badge", has_text="succeeded")).to_be_visible(timeout=600_000)
        shot("sync-run")
        page.click("#ov-verify")
        page.wait_for_selector("#run-box .notice")
        shot("verify-ok")

        tampered = None
        try:
            target = httpx.Client(base_url=args.target, timeout=30)
            remote = target.get("/api/tenants/hris/users").json()
            victim = next(u for u in remote if u.get("active") and u.get("title"))
            target.patch(f"/t/hris/scim/v2/Users/{victim['id']}",
                         headers={"Authorization": "Bearer target-token"},
                         json={"schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
                               "Operations": [{"op": "replace", "path": "title", "value": "Chief Tampering Officer"}]})
            tampered = victim
        except (httpx.HTTPError, StopIteration, ValueError):
            print("scim-target not reachable; skipping the drift screenshot")
        if tampered:
            go("#/integrations/scim-target/overview", ".flow-step")
            page.click("#ov-verify")
            page.wait_for_selector("#run-box .notice.bad")
            shot("verify-drift")
            api.post("/api/integrations/scim-target/sync", json={"full": True})
            for _ in range(600):
                runs = api.get("/api/integrations/scim-target/runs", params={"limit": 1}).json()
                if runs and runs[0]["status"] != "running":
                    break
                page.wait_for_timeout(500)
            assert api.post("/api/integrations/scim-target/verify").json()["ok"], "resync did not repair drift"

        go("#/integrations/scim-target/activity", "tr[data-run]"); shot("activity")

        # ---- other pages
        go("#/mock", ".stat"); shot("mock-github")
        go("#/admin", "#validate"); page.click("#validate"); page.wait_for_selector("#val .notice"); shot("admin")
        browser.close()


if __name__ == "__main__":
    main()
