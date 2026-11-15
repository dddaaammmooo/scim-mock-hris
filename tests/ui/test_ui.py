"""End-to-end browser tests for the MockHRIS web GUI (every page and workflow)."""
import json
import re

from playwright.sync_api import expect


def toast(page, text, timeout=5000):
    expect(page.locator(".toast", has_text=text).last).to_be_visible(timeout=timeout)


def switch(page, input_id, on):
    """Toggle a styled switch the way a user does: by clicking its visible label."""
    box = page.locator(f"#{input_id}")
    if box.is_checked() != on:
        page.locator(f"label.switch:has(#{input_id})").click()
    expect(box).to_be_checked(checked=on)


# ------------------------------------------------------------------ shell
def test_dashboard_and_navigation(ui):
    ui.go()
    expect(ui.locator("h1")).to_have_text("Dashboard")
    expect(ui.locator(".stat .value").first).to_have_text(re.compile(r"\d{3}"))
    expect(ui.locator("#nav a.active")).to_have_text("Dashboard")
    for route, title in [("users", "People"), ("groups", "Groups"), ("org", "Org chart"),
                         ("integrations", "Integrations"), ("mock", "Mock GitHub target"), ("admin", "Admin & API")]:
        ui.click(f"#nav a[data-route={route}]")
        expect(ui.locator("h1").first).to_have_text(title)
        expect(ui.locator(f"#nav a[data-route={route}]")).to_have_class(re.compile("active"))
    ui.go("#/nonexistent")
    expect(ui.locator("h1")).to_have_text("Dashboard")


def test_lazy_content_fades_in_and_respects_reduced_motion(ui):
    ui.go("#/users")
    first = ui.locator("#u-results > *").first
    expect(first).to_be_visible()
    assert first.evaluate("e => getComputedStyle(e).animationName") == "fade-in"
    avatar = ui.locator("#u-results img.avatar").first
    expect(avatar).to_have_class(re.compile("loaded"))
    ui.wait_for_function("e => getComputedStyle(e).opacity === '1'", arg=avatar.element_handle())  # fade completes
    ui.emulate_media(reduced_motion="reduce")
    ui.go("#/users")
    expect(ui.locator("#u-results > *").first).to_be_visible()
    assert ui.locator("#u-results > *").first.evaluate("e => getComputedStyle(e).animationName") == "none"


# ----------------------------------------------------------------- people
def test_people_search_filter_query_and_paging(ui):
    ui.go("#/users")
    expect(ui.locator(".pager .muted")).to_contain_text("page 1 of")
    ui.click("#pg-next")
    expect(ui.locator(".pager .muted")).to_contain_text("page 2 of")
    ui.click("#pg-prev")
    ui.select_option("#u-region", "EMEA")
    expect(ui.locator("#u-results tbody tr").first).to_contain_text("EMEA")
    ui.fill("#u-query", 'team == "Payments"')
    ui.click("#u-apply")
    expect(ui.locator("#u-results tbody tr td:nth-child(3)").first).to_contain_text("Payments")
    ui.fill("#u-query", "team ==")
    ui.press("#u-query", "Enter")
    expect(ui.locator("#u-results .notice.bad")).to_contain_text("expected a value")
    ui.click("#u-clear")
    ui.fill("#u-search", "HSG-10001")
    expect(ui.locator("#u-results tbody tr")).to_have_count(1)
    ui.locator("#u-results tbody tr").click()
    expect(ui.locator(".page-head .sub")).to_contain_text("HSG-10001")


def test_create_edit_and_lifecycle_of_a_person(ui):
    ui.go("#/users")
    ui.click("#new-user")
    form = ui.locator("#uf")
    form.locator("[name=email]").fill("ui.tester@halcyon.example")
    form.locator("[name=firstName]").fill("Ui")
    form.locator("[name=surname]").fill("Tester")
    form.locator("[name=title]").fill("QA Engineer")
    form.locator("[name=division]").select_option("Engineering")
    form.locator("[name=department]").fill("Platform Engineering")
    form.locator("[name=team]").fill("Observability")
    form.locator("[name=site]").select_option("London")
    form.locator("[name=jobLevel]").select_option("IC2")
    form.locator("button[type=submit]").click()
    toast(ui, "Person created")
    expect(ui.locator("h1")).to_contain_text("Ui Tester")
    expect(ui.locator(".kv")).to_contain_text("EMEA")
    expect(ui.locator(".page-head img.avatar")).to_have_class(re.compile("loaded"))
    # edit
    ui.locator("#uf [name=title]").fill("Senior QA Engineer")
    ui.locator("#uf button[type=submit]").click()
    toast(ui, "Saved")
    expect(ui.locator(".page-head .sub")).to_contain_text("Senior QA Engineer")
    # invalid manager -> error toast
    ui.locator("#uf [name=manager]").fill("ghost@halcyon.example")
    ui.locator("#uf button[type=submit]").click()
    toast(ui, "does not exist")
    # tabs
    ui.click(".tabs button[data-tab=groups]")
    expect(ui.locator("section[data-panel=groups]")).to_contain_text("team-observability")
    ui.click(".tabs button[data-tab=reports]")
    expect(ui.locator("section[data-panel=reports]")).to_contain_text("No direct reports")
    ui.click(".tabs button[data-tab=json]")
    raw = json.loads(ui.locator("#raw").input_value())
    raw["work"]["title"] = "Edited As JSON"
    ui.locator("#raw").fill(json.dumps(raw))
    ui.click("#save-raw")
    toast(ui, "Saved")
    expect(ui.locator(".page-head .sub")).to_contain_text("Edited As JSON")
    ui.click(".tabs button[data-tab=json]")
    ui.locator("#raw").fill("{not json")
    ui.click("#save-raw")
    toast(ui, "Invalid JSON")
    # terminate (date + reason prompts) and rehire
    ui.answers += ["2026-01-31", "Redundancy"]
    ui.click("#terminate")
    toast(ui, "Terminated")
    expect(ui.locator("h1 .badge")).to_have_text("terminated")
    ui.click("#rehire")
    toast(ui, "Rehired")
    expect(ui.locator("h1 .badge")).to_have_text("employed")
    # change email, then delete
    ui.answers += ["ui.renamed@halcyon.example"]
    ui.click("#rename")
    toast(ui, "Email changed")
    expect(ui.locator(".page-head .sub")).to_contain_text("ui.renamed@halcyon.example")
    ui.answers += [False]
    ui.click("#delete")                                # dismissed: still here
    expect(ui.locator("h1")).to_contain_text("Ui Tester")
    ui.click("#delete")
    toast(ui, "Deleted")
    expect(ui.locator("h1")).to_have_text("People")


# ----------------------------------------------------------------- groups
def test_groups_list_filters(ui):
    ui.go("#/groups")
    total = ui.locator("#g-results tbody tr").count()
    ui.select_option("#g-kind", "team")
    assert 0 < ui.locator("#g-results tbody tr").count() < total
    ui.select_option("#g-kind", "")
    ui.select_option("#g-type", "mixed")
    expect(ui.locator("#g-results tbody tr").first).to_contain_text("mixed")
    ui.fill("#g-search", "zzz-nothing")
    expect(ui.locator("#g-results")).to_contain_text("No groups")


def test_create_query_group_and_manage_it(ui):
    ui.go("#/groups")
    ui.click("#new-group")
    form = ui.locator("#gf")
    form.locator("[name=name]").fill("ui-payments-emea")
    form.locator("[name=displayName]").fill("UI Payments EMEA")
    ui.select_option("#q-group", "team-payments")
    expect(form.locator("[name=query]")).to_have_value('memberOf("team-payments")')
    ui.locator("#gf details summary").click()
    ui.locator(".insert[data-text=region]").click()
    expect(form.locator("[name=query]")).to_have_value(re.compile("region"))
    form.locator("[name=query]").fill('memberOf("team-payments") and region == "EMEA"')
    ui.click("#q-test")
    expect(ui.locator("#q-result .badge")).to_contain_text("match")
    expect(ui.locator("#preview a").first).to_be_visible()
    form.locator("[name=query]").fill("team ==")
    ui.click("#q-test")
    expect(ui.locator("#q-result .badge.bad")).to_be_visible()
    form.locator("[name=query]").fill("")
    ui.click("#q-test")
    expect(ui.locator("#q-result")).to_have_text("No query")
    form.locator("[name=query]").fill('memberOf("team-payments") and region == "EMEA"')
    form.locator("button[type=submit]").click()
    toast(ui, "Group created")
    expect(ui.locator("h1")).to_contain_text("UI Payments EMEA")
    expect(ui.locator("h1 .badge")).to_have_text("dynamic")
    # add a static member -> mixed
    email = ui.server.api.get("/api/users", params={"team": "Mobile", "limit": 1}).json()["items"][0]["email"]
    ui.fill("#add-member", email)
    ui.click("#add-btn")
    ui.click("#add-btn")                                  # empty input is ignored
    ui.locator("#gf button[type=submit]").click()
    toast(ui, "Group saved")
    expect(ui.locator("h1 .badge")).to_have_text("mixed")
    ui.fill("#m-filter", email.split("@")[0])
    expect(ui.locator("#m-list .lr")).to_have_count(1)
    expect(ui.locator("#m-list .badge")).to_have_text("static")
    ui.fill("#m-filter", "zzz")
    expect(ui.locator("#m-list")).to_contain_text("No members")
    ui.click("#g-delete")
    toast(ui, "Group deleted")
    expect(ui.locator("h1")).to_have_text("Groups")


def test_group_used_by_integration_links(ui):
    ui.go("#/groups/github-enterprise-owners")
    chip = ui.locator(".chip", has_text="github-emu · roles")
    expect(chip).to_be_visible()
    chip.click()
    expect(ui.locator(".int-tabs a.active")).to_have_text("Attribute mapping")
    ui.go("#/groups/all-staff")
    ui.locator(".chip", has_text="scim-target · scope").click()
    expect(ui.locator(".int-tabs a.active")).to_have_text("Users in scope")


# -------------------------------------------------------------- org chart
def test_org_chart_visual(ui):
    ui.go("#/org")
    cards = ui.locator("#oc-canvas .oc-card")
    expect(cards.first).to_be_visible()
    before = cards.count()
    pct = ui.locator("#oc-pct").inner_text()
    ui.click("#oc-in")
    assert ui.locator("#oc-pct").inner_text() != pct
    ui.click("#oc-out")
    ui.click("#oc-pct")
    expect(ui.locator("#oc-pct")).to_have_text("100%")
    ui.click("#oc-fit")
    # expand one executive's branch
    toggle = ui.locator("#oc-canvas .oc-toggle").nth(1)
    toggle.click()
    expect(cards).not_to_have_count(before)
    # pan by dragging the background, and zoom with the wheel
    box = ui.locator("#oc-vp").bounding_box()
    ui.mouse.move(box["x"] + 20, box["y"] + box["height"] - 20)
    ui.mouse.down()
    ui.mouse.move(box["x"] + 120, box["y"] + box["height"] - 60)
    ui.mouse.up()
    ui.mouse.wheel(0, -200)
    # focus a branch, then go back to the whole company via breadcrumbs
    ui.locator("#oc-canvas .oc-focus").nth(1).click(force=True)
    expect(ui.locator("#oc-crumbs")).to_contain_text("Whole company")
    ui.locator("#oc-crumbs [data-crumb='']").click()
    expect(ui.locator("#oc-crumbs")).to_have_text("Whole company")
    ui.click("#oc-expand")
    assert cards.count() > 300
    ui.click("#oc-collapse")
    # find someone deep in the tree
    deep = ui.server.api.get("/api/users", params={"team": "Compiler Toolchain", "limit": 1}).json()["items"][0]
    ui.fill("#o-find", deep["email"])
    ui.dispatch_event("#o-find", "change")
    expect(ui.locator(f'#oc-canvas .oc-card[data-email="{deep["email"]}"]')).to_have_class(re.compile("hl"))
    ui.fill("#o-find", "ghost@nowhere.example")
    ui.dispatch_event("#o-find", "change")
    toast(ui, "Not found")


def test_org_chart_list_view_and_terminated(ui):
    ui.go("#/org")
    ui.click(".page-head .seg button[data-view=list]")
    expect(ui.locator("#tree li").first).to_be_visible()
    ui.click("#o-expand")
    ui.click("#o-collapse")
    ui.locator("#tree .toggle").first.click()
    someone = ui.server.api.get("/api/users", params={"team": "Payments", "limit": 1}).json()["items"][0]["email"]
    ui.fill("#o-find", someone)
    ui.dispatch_event("#o-find", "change")
    expect(ui.locator(f'#tree li[data-email="{someone}"] > .node')).to_have_class(re.compile("hl"))
    count = ui.locator("#tree li").count()
    ui.check("#o-term")
    expect(ui.locator("#tree li")).not_to_have_count(count)
    ui.uncheck("#o-term")
    ui.click(".page-head .seg button[data-view=chart]")
    expect(ui.locator("#oc-vp")).to_be_visible()


# ----------------------------------------------------------- integrations
def test_add_integration_and_edit_connection(ui):
    ui.go("#/integrations")
    expect(ui.locator(".int-card")).to_have_count(2)
    ui.click("#add-int")
    ui.click("#add-cancel")
    expect(ui.locator("#add-form")).to_have_count(0)
    ui.click("#add-int")
    ui.locator(".tile", has_text="Generic SCIM 2.0").click()
    ui.fill("#add-form [name=name]", "UI Wiki")
    expect(ui.locator("#add-form [name=id]")).to_have_value("ui-wiki")
    ui.fill("#add-form [name=baseUrl]", f"{ui.server.base}/mock-github/scim/v2/enterprises/uiwiki")
    ui.fill("#add-form [name=token]", "mock-scim-token")
    ui.click("#add-form button[type=submit]")
    toast(ui, "Integration created")
    expect(ui.locator(".int-tabs a.active")).to_have_text("Connection")
    expect(ui.locator("#savebar")).to_be_hidden()
    ui.fill("#c-name", "UI Wiki Renamed")
    expect(ui.locator("#savebar")).to_be_visible()
    ui.select_option("#c-auth", "basic")
    expect(ui.locator("#c-user")).to_be_visible()
    expect(ui.locator("#c-token")).to_be_hidden()
    ui.select_option("#c-auth", "bearer")
    ui.click("#c-addh")
    ui.locator("[data-hk]").fill("X-Team")
    ui.locator("[data-hk]").dispatch_event("change")
    ui.locator("[data-hv]").fill("platform")
    ui.locator("[data-hv]").dispatch_event("change")
    ui.fill("#c-timeout", "20")
    ui.uncheck("#c-tls")
    ui.click("#save")
    toast(ui, "Integration saved")
    expect(ui.locator("#savebar")).to_be_hidden()
    expect(ui.locator("h1")).to_have_text("UI Wiki Renamed")
    ui.click("#c-test")
    expect(ui.locator("#c-result .badge.ok")).to_contain_text("connected")
    ui.locator("[data-hdel]").click()
    ui.click("#discard")
    expect(ui.locator("[data-hk]")).to_have_value("X-Team")
    # discard/enable toggle
    switch(ui, "int-enabled", False)
    expect(ui.locator("#savebar")).to_be_visible()
    ui.click("#discard")
    expect(ui.locator("#int-enabled")).to_be_checked()


def test_scope_mapping_groups_actions_tabs(ui):
    ui.go("#/integrations/scim-target/users")
    expect(ui.locator("#s-preview .badge.ok")).to_contain_text("active")
    ui.locator(".tile", has_text="Attribute filter").click()
    ui.fill("#s-query", 'team == "Payments"')
    expect(ui.locator("#s-preview .badge.ok")).to_have_text(re.compile(r"^\d+ active$"))
    ui.fill("#s-query", "team ==")
    expect(ui.locator("#s-preview .notice.bad")).to_be_visible()
    ui.locator(".tile", has_text="All users").click()
    ui.locator(".tile", has_text="Group membership").click()
    ui.select_option("#s-group", "team-payments")
    expect(ui.locator("#savebar")).to_be_visible()
    # mapping tab keeps the draft
    ui.click(".int-tabs a:has-text('Attribute mapping')")
    expect(ui.locator("#p-out pre")).to_contain_text('"userName"')
    rows = ui.locator("#m-rows tr")
    n = rows.count()
    ui.click("#m-add")
    expect(rows).to_have_count(n + 1)
    last = rows.last
    last.locator("[data-f=target]").fill("nickName2")
    last.locator("[data-f=target]").dispatch_event("change")
    ui.locator("#m-rows tr").last.locator("[data-f=source]").fill("{firstName}.{surname}")
    ui.locator("#m-rows tr").last.locator("[data-f=source]").dispatch_event("change")
    ui.locator("#m-rows tr").last.locator("[data-tadd]").select_option("lowercase")
    ui.locator("#m-rows tr").last.locator("[data-f=default]").fill("fallback")
    ui.locator("#m-rows tr").last.locator("[data-f=default]").dispatch_event("change")
    ui.locator("#m-rows tr").last.locator("[data-f=applyOn]").select_option("create")
    expect(ui.locator("#p-out pre")).to_contain_text('"nickName2"')
    ui.locator("#m-rows tr").last.locator("[data-tdel]").click()
    ui.locator("#m-rows tr").last.locator("[data-f=source]").fill("nope.field")
    ui.locator("#m-rows tr").last.locator("[data-f=source]").dispatch_event("change")
    expect(ui.locator("#p-out .notice.bad")).to_contain_text("unknown source")
    ui.locator("#m-rows tr").last.locator("[data-del]").click()
    expect(rows).to_have_count(n)
    expect(ui.locator("#m-rows [data-del][disabled]").first).to_be_visible()     # required rows can't be removed
    ui.fill("#p-user", ui.server.api.get("/api/users", params={"team": "Payments", "limit": 1}).json()["items"][0]["email"])
    ui.click("#p-go")
    expect(ui.locator("#p-out .badge.ok").first).to_have_text("in scope")
    ui.answers += [False, True]
    ui.click("#m-reset")                               # dismissed
    ui.click("#m-reset")
    # role rules
    switch(ui, "r-on", True)
    ui.click("#r-add")
    ui.locator("[data-rg]").last.select_option("people-managers")
    ui.locator("[data-rv]").last.fill("manager")
    ui.locator("[data-rv]").last.dispatch_event("change")
    ui.fill("#r-default", "member")
    ui.locator("#r-default").dispatch_event("change")
    ui.locator("[data-rdel]").last.click()
    switch(ui, "r-on", False)
    # groups tab
    ui.click(".int-tabs a:has-text('Group provisioning')")
    expect(ui.locator("#g-prev .lr").first).to_be_visible()
    ui.fill("#g-pat", "team-payments")
    expect(ui.locator("#g-prev .lr")).to_have_count(1)
    ui.fill("#g-exc", "team-*")
    expect(ui.locator("#g-prev")).to_contain_text("No groups match")
    ui.fill("#g-exc", "")
    ui.select_option("#g-name", "name")
    ui.uncheck("#g-skip")
    switch(ui, "g-on", False)
    expect(ui.locator("#g-prev")).to_contain_text("Group push is off")
    switch(ui, "g-on", True)
    # actions tab
    ui.click(".int-tabs a:has-text('Provisioning actions')")
    ui.uncheck("#a-update")
    ui.check("#a-update")
    ui.locator(".tile", has_text="Delete").click()
    ui.locator(".tile", has_text="Disable").click()
    ui.click("#save")
    toast(ui, "Integration saved")
    saved = ui.server.api.get("/api/integrations/scim-target").json()
    assert saved["scope"]["mode"] == "group" and saved["scope"]["group"] == "team-payments"
    assert saved["groups"]["patterns"] == ["team-payments"]


def test_plan_sync_verify_activity_and_mock_target(ui):
    ui.go("#/integrations/github-emu/overview")
    expect(ui.locator(".flow-step").nth(1)).to_contain_text("active")
    ui.click("#ov-plan")
    expect(ui.locator("#run-box .chip").first).to_contain_text("create_user")
    ui.fill("#op-filter", "create_group")
    expect(ui.locator("#op-rows tr").first).to_contain_text("create_group")
    ui.click("#ov-sync")
    expect(ui.locator("#run-box .badge", has_text="succeeded")).to_be_visible(timeout=180_000)
    expect(ui.locator("#run-box")).to_have_class(re.compile("no-fade"))           # polling doesn't re-fade
    ui.click("#after-verify")
    expect(ui.locator("#run-box .notice.ok")).to_contain_text("Verified", timeout=60_000)
    # header buttons from another tab switch to the overview and run there
    ui.click(".int-tabs a:has-text('Connection')")
    ui.click("#hdr-plan")
    expect(ui.locator("#run-box")).to_contain_text("In sync", timeout=30_000)
    # drift is reported by verify
    victim = ui.server.api.get("/api/users", params={"query": 'memberOf("github-emu-users") and isManager == false',
                                                      "limit": 1}).json()["items"][0]["email"]
    ui.server.api.post(f"/api/users/{victim}/terminate", json={})
    ui.click("#hdr-verify")
    expect(ui.locator("#run-box .notice.bad")).to_contain_text("Drift detected", timeout=60_000)
    expect(ui.locator("#run-box")).to_contain_text(victim)
    # activity tab
    ui.click(".int-tabs a:has-text('Activity')")
    expect(ui.locator("#st-rows tr").first).to_be_visible()
    ui.fill("#st-filter", victim.split("@")[0])
    expect(ui.locator("#st-rows tr")).to_have_count(1)
    ui.locator("tr[data-run]").first.click()
    expect(ui.locator("#run-box .badge", has_text="succeeded")).to_be_visible()
    # the mock GitHub target shows what was provisioned
    ui.go("#/mock")
    expect(ui.locator("#mu-rows tr").first).to_contain_text("_halcyon")
    ui.fill("#mock-filter", victim.split("@")[0])
    expect(ui.locator("#mu-rows tr")).to_have_count(1)
    ui.click(".tabs button[data-tab=mg]")
    ui.fill("#mock-filter", "")
    ui.locator("#mg-rows details summary").first.click()
    ui.answers += [False]
    ui.click("#mock-reset")                          # dismissed
    ui.click("#mock-reset")
    toast(ui, "reset")
    expect(ui.locator(".stat .value").first).to_have_text("0")


def test_actions_tab_forget_state_and_delete(ui):
    ui.server.api.post("/api/integrations", json={"type": "scim2", "id": "ui-temp", "name": "UI Temp",
                                                  "connection": {"baseUrl": "http://127.0.0.1:9/scim/v2"}})
    ui.go("#/integrations/ui-temp/actions")
    ui.click("#a-forget")
    toast(ui, "State cleared")
    ui.answers += [False]
    ui.click("#a-delete")
    expect(ui.locator("h1")).to_have_text("UI Temp")
    ui.click("#a-delete")
    toast(ui, "Integration deleted")
    expect(ui.locator("h1")).to_have_text("Integrations")
    # unreachable target reports a readable connection error
    ui.server.api.post("/api/integrations", json={"type": "scim2", "id": "ui-down", "name": "UI Down",
                                                  "connection": {"baseUrl": "http://127.0.0.1:9/scim/v2"}})
    ui.go("#/integrations/ui-down/connection")
    ui.click("#c-test")
    expect(ui.locator("#c-result .badge.bad")).to_contain_text("Error")
    ui.go("#/integrations/ui-down/overview")
    ui.click("#ov-verify")
    expect(ui.locator("#run-box .notice.bad")).to_contain_text("Could not read the target")
    ui.answers += [True]
    ui.click("#ov-sync")                              # non-mock target asks for confirmation
    expect(ui.locator("#run-box .badge", has_text="failed")).to_be_visible(timeout=60_000)
    expect(ui.locator("#run-box")).to_contain_text("skipped: target unreachable")
    ui.server.api.delete("/api/integrations/ui-down")


# ------------------------------------------------------------------ admin
def test_admin_page(ui):
    ui.go("#/admin")
    ui.click("#validate")
    expect(ui.locator("#val .notice.ok")).to_contain_text("groups resolve")
    with ui.expect_download() as dl:
        ui.click("#export")
    exported = json.loads(open(dl.value.path(), encoding="utf-8").read())
    assert len(exported["users"]) > 400
    ui.answers += [False]
    ui.click("#reseed")                              # dismissed: nothing happens
    # import (merge) a small bundle
    path = dl.value.path()
    ui.check("#merge")
    ui.set_input_files("#import", path)
    toast(ui, "Imported", timeout=120_000)            # merging ~440 people takes a while on moto
    expect(ui.locator("pre.code").first).to_contain_text("/api/users")
