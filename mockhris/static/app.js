/* MockHRIS web GUI — dependency-free single page app over the REST API. */
"use strict";

// ---------------------------------------------------------------- utilities
// Images (avatars) fade in once loaded; failed loads are shown too so nothing stays invisible.
for (const ev of ["load", "error"]) {
  document.addEventListener(ev, (e) => { if (e.target.tagName === "IMG") e.target.classList.add("loaded"); }, true);
}

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const enc = encodeURIComponent;
const main = () => $("#main");
const fmtDate = (s) => (s ? new Date(s).toLocaleString() : "—");

function storage(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key);
    if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, value);
  } catch { return null; }
}

async function api(path, { method = "GET", body, raw = false } = {}) {
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const key = storage("mockhris.apiKey");
  if (key) headers["X-API-Key"] = key;
  const res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  if (res.status === 401 && path.startsWith("/api")) {
    const k = prompt("This MockHRIS instance requires an API key (MOCKHRIS_API_KEY):");
    if (k) { storage("mockhris.apiKey", k); return api(path, { method, body, raw }); }
  }
  if (raw) return res;
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    let msg = data?.detail ?? res.statusText;
    if (Array.isArray(msg)) msg = msg.map((d) => `${(d.loc || []).join(".")}: ${d.msg}`).join("; ");
    throw new Error(msg);
  }
  return data;
}

function toast(msg, bad = false) {
  const el = document.createElement("div");
  el.className = "toast" + (bad ? " bad" : "");
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), bad ? 7000 : 3500);
}

// Latest-request-wins: for UI regions refreshed by overlapping requests (typing, filters), a response
// is applied only if no newer request for the same key started after it.
const latestSeq = {};
async function latest(key, promise) {
  const seq = (latestSeq[key] = (latestSeq[key] || 0) + 1);
  const result = await promise;
  if (seq !== latestSeq[key]) throw Object.assign(new Error("stale"), { stale: true });
  return result;
}

async function guard(fn, okMsg) {
  try { const r = await fn(); if (okMsg) toast(okMsg); return r; }
  catch (e) { toast(e.message, true); return undefined; }
}

const lifecycleBadge = (l) => {
  const cls = { employed: "ok", leave: "info", hired: "accent", terminated: "bad" }[l] || "";
  return `<span class="badge ${cls}">${esc(l || "—")}</span>`;
};
const typeBadge = (t) => `<span class="badge ${{ dynamic: "info", static: "", mixed: "accent" }[t] || ""}">${esc(t)}</span>`;
// Generated avatars live at /avatars/<id>.svg on this server; a custom about.avatar URL wins.
function avatarSrc(u) {
  return u.avatar && !u.avatar.includes("/avatars/") ? u.avatar : `/avatars/${enc(u.id)}.svg`;
}
const avatarImg = (u, size = 32) =>
  `<img class="avatar" src="${esc(avatarSrc(u))}" alt="" loading="lazy" width="${size}" height="${size}">`;
const userLink = (email) => `<a href="#/users/${enc(email)}">${esc(email)}</a>`;
const groupLink = (name) => `<a href="#/groups/${enc(name)}">${esc(name)}</a>`;
const options = (values, selected, blank) =>
  (blank !== undefined ? `<option value="">${esc(blank)}</option>` : "") +
  values.map((v) => `<option value="${esc(v)}"${v === selected ? " selected" : ""}>${esc(v)}</option>`).join("");

let META = null;
async function meta(force = false) {
  if (!META || force) META = await api("/api/meta");
  return META;
}
let EMAILS = null;
async function allEmails(force = false) {
  if (!EMAILS || force) EMAILS = (await api("/api/users?limit=5000")).items.map((u) => ({ email: u.email, name: u.displayName, id: u.id, avatar: u.avatar, title: u.title }));
  return EMAILS;
}
let GROUP_NAMES = null;
async function groupNames(force = false) {
  if (!GROUP_NAMES || force) GROUP_NAMES = (await api("/api/groups")).map((g) => g.name);
  return GROUP_NAMES;
}
function invalidateCaches() { META = null; EMAILS = null; GROUP_NAMES = null; }

// ------------------------------------------------------------------- router
const routes = {
  dashboard: renderDashboard, users: renderUsers, groups: renderGroups, org: renderOrg,
  integrations: renderIntegrations, mock: renderMock, admin: renderAdmin,
};
let pollTimer = null;

async function router() {
  clearInterval(pollTimer);
  const hash = location.hash.replace(/^#\/?/, "") || "dashboard";
  const [route, ...rest] = hash.split("/");
  const arg = rest.length ? decodeURIComponent(rest.join("/")) : null;
  $$("#nav a[data-route]").forEach((a) => a.classList.toggle("active", a.dataset.route === route));
  main().innerHTML = `<div class="loading">Loading…</div>`;
  try {
    if (route === "users" && arg) await renderUser(arg);
    else if (route === "groups" && arg) await renderGroup(arg);
    else if (route === "integrations" && arg) await renderIntegration(arg);
    else await (routes[route] || renderDashboard)();
  } catch (e) {
    main().innerHTML = `<div class="notice bad">${esc(e.message)}</div>`;
  }
  main().focus({ preventScroll: true });
}
window.addEventListener("hashchange", router);
window.addEventListener("DOMContentLoaded", router);

// ---------------------------------------------------------------- dashboard
async function renderDashboard() {
  const s = await api("/api/stats");
  const active = (s.byLifecycle.employed || 0) + (s.byLifecycle.leave || 0);
  const bars = (obj) => {
    const max = Math.max(1, ...Object.values(obj));
    return Object.entries(obj).sort((a, b) => b[1] - a[1]).map(([k, v]) =>
      `<div class="lr" style="grid-template-columns: 170px 1fr 40px; display:grid; gap:10px; align-items:center; padding:4px 0">
        <span>${esc(k)}</span><div class="bar"><span style="width:${(v / max) * 100}%"></span></div><span class="right">${v}</span></div>`).join("");
  };
  const ls = s.lastSync;
  main().innerHTML = `
    <div class="page-head"><div><h1>Dashboard</h1><div class="sub">Halcyon Systems Group — mock HRIS for GitHub EMU SCIM provisioning</div></div></div>
    <div class="grid cols-4">
      <div class="card stat"><div class="label">People</div><div class="value">${s.users}</div></div>
      <div class="card stat"><div class="label">Currently active</div><div class="value">${active}</div></div>
      <div class="card stat"><div class="label">Groups</div><div class="value">${s.groups}</div></div>
      <div class="card stat"><div class="label">Last provisioning sync</div><div class="value" style="font-size:16px; padding-top:8px">
        ${ls ? `<a href="#/integrations/${enc(ls.integrationId || "github-emu")}/activity">${esc(ls.status)}</a> <span class="muted small">${esc(ls.integrationName || "")} · ${fmtDate(ls.finishedAt || ls.startedAt)}</span>` : `<a href="#/integrations">never</a>`}</div></div>
    </div>
    <div class="grid cols-2" style="margin-top:16px">
      <div class="card"><h2>By division</h2>${bars(s.byDivision)}</div>
      <div class="card"><h2>By region</h2>${bars(s.byRegion)}
        <h2 style="margin-top:18px">Lifecycle</h2>${bars(s.byLifecycle)}
        <h2 style="margin-top:18px">Employment type</h2>${bars(s.byEmploymentType)}</div>
    </div>`;
}

// -------------------------------------------------------------------- users
const userState = { search: "", division: "", region: "", lifecycle: "", employmentType: "", query: "", page: 0 };

async function renderUsers() {
  const m = await meta();
  main().innerHTML = `
    <div class="page-head"><div><h1>People</h1><div class="sub">HiBob-style employee records, keyed by email</div></div>
      <button class="primary" id="new-user">New person</button></div>
    <div class="card">
      <div class="row">
        <input id="u-search" placeholder="Search name, email, title, team, employee ID" style="flex:1; min-width:220px" value="${esc(userState.search)}">
        <select id="u-division">${options(Object.keys(m.org), userState.division, "All divisions")}</select>
        <select id="u-region">${options(m.regions, userState.region, "All regions")}</select>
        <select id="u-lifecycle">${options(m.lifecycles, userState.lifecycle, "Any lifecycle")}</select>
        <select id="u-type">${options(m.employmentTypes, userState.employmentType, "Any employment")}</select>
      </div>
      <div class="row" style="margin-top:8px">
        <input id="u-query" class="mono" style="flex:1; min-width:220px" placeholder='Query, e.g. memberOf("engineering-org") and jobLevel in ["IC4","IC5"]' value="${esc(userState.query)}">
        <button id="u-apply">Apply query</button><button id="u-clear" class="link">Clear</button>
      </div>
    </div>
    <div id="u-results" style="margin-top:16px"><div class="loading">Loading…</div></div>`;
  $("#new-user").onclick = () => (location.hash = "#/users/new");
  let t;
  $("#u-search").oninput = (e) => { clearTimeout(t); t = setTimeout(() => { userState.search = e.target.value; userState.page = 0; loadUsers(); }, 200); };
  for (const [id, key] of [["u-division", "division"], ["u-region", "region"], ["u-lifecycle", "lifecycle"], ["u-type", "employmentType"]])
    $("#" + id).onchange = (e) => { userState[key] = e.target.value; userState.page = 0; loadUsers(); };
  $("#u-apply").onclick = () => { userState.query = $("#u-query").value.trim(); userState.page = 0; loadUsers(); };
  $("#u-query").onkeydown = (e) => { if (e.key === "Enter") $("#u-apply").click(); };
  $("#u-clear").onclick = () => { Object.assign(userState, { search: "", division: "", region: "", lifecycle: "", employmentType: "", query: "", page: 0 }); renderUsers(); };
  await loadUsers();
}

async function loadUsers() {
  const pageSize = 50;
  const p = new URLSearchParams({ limit: pageSize, offset: userState.page * pageSize });
  for (const k of ["search", "division", "region", "lifecycle", "employmentType", "query"]) if (userState[k]) p.set(k, userState[k]);
  let data;
  try { data = await latest("users", api("/api/users?" + p)); }
  catch (e) { if (!e.stale && $("#u-results")) $("#u-results").innerHTML = `<div class="notice bad">${esc(e.message)}</div>`; return; }
  const box = $("#u-results");
  if (!box) return;   // navigated away while loading
  const pages = Math.max(1, Math.ceil(data.total / pageSize));
  box.innerHTML = `
    <div class="table-wrap"><table>
      <thead><tr><th>Name</th><th>Title</th><th>Team</th><th>Site</th><th>Level</th><th>Lifecycle</th><th>Type</th></tr></thead>
      <tbody>${data.items.map((u) => `
        <tr class="clickable" data-email="${esc(u.email)}">
          <td><div class="who">${avatarImg(u, 34)}<div><strong>${esc(u.displayName)}</strong><span class="sub">${esc(u.email)}</span></div></div></td>
          <td>${esc(u.title)}<span class="sub">${esc(u.department)}</span></td>
          <td>${esc(u.team)}<span class="sub">${esc(u.division)}</span></td>
          <td>${esc(u.site)}<span class="sub">${esc(u.region)}</span></td>
          <td>${esc(u.jobLevel)}</td><td>${lifecycleBadge(u.lifecycle)}</td><td>${esc(u.employmentType)}</td>
        </tr>`).join("") || `<tr><td colspan="7" class="empty">No matching people</td></tr>`}</tbody>
    </table></div>
    <div class="pager"><span class="muted">${data.total} people · page ${userState.page + 1} of ${pages}</span>
      <button id="pg-prev" ${userState.page === 0 ? "disabled" : ""}>Previous</button>
      <button id="pg-next" ${userState.page + 1 >= pages ? "disabled" : ""}>Next</button></div>`;
  $$("tr[data-email]", box).forEach((tr) => (tr.onclick = () => (location.hash = "#/users/" + enc(tr.dataset.email))));
  $("#pg-prev").onclick = () => { userState.page--; loadUsers(); };
  $("#pg-next").onclick = () => { userState.page++; loadUsers(); };
}

function userForm(u, m, emails, isNew) {
  const w = u.work || {}, c = w.custom || {}, p = u.personal || {}, emp = (u.payroll || {}).employment || {}, i = u.internal || {};
  const divisions = Object.keys(m.org);
  const depts = c.division && m.org[c.division] ? Object.keys(m.org[c.division]) : [];
  const teams = c.division && w.department && m.org[c.division]?.[w.department] ? m.org[c.division][w.department] : [];
  return `
    <div class="form-grid">
      ${isNew ? `<label class="field">Email *<input name="email" type="email" required placeholder="first.last@halcyon.example"></label>` : ""}
      <label class="field">First name *<input name="firstName" value="${esc(u.firstName)}" required></label>
      <label class="field">Surname *<input name="surname" value="${esc(u.surname)}" required></label>
      <label class="field">Display name<input name="displayName" value="${esc(isNew ? "" : u.displayName)}" placeholder="defaults to full name"></label>
      <label class="field">Preferred name<input name="preferredName" value="${esc(p.preferredName)}"></label>
      <label class="field">Pronouns<input name="pronouns" value="${esc(p.pronouns)}" list="dl-pronouns"></label>
      <label class="field">Job title<input name="title" value="${esc(w.title)}"></label>
      <label class="field">Division<select name="division">${options(divisions, c.division, "—")}</select></label>
      <label class="field">Department<input name="department" value="${esc(w.department)}" list="dl-depts"></label>
      <label class="field">Team<input name="team" value="${esc(c.team)}" list="dl-teams"></label>
      <label class="field">Site<select name="site">${options(m.sites, w.site, "—")}</select></label>
      <label class="field">Manager (email)<input name="manager" value="${esc(w.reportsTo?.email)}" list="dl-emails" placeholder="none"></label>
      <label class="field">Job level<select name="jobLevel">${options(m.jobLevels, c.jobLevel, "—")}</select></label>
      <label class="field">Employment type<select name="employmentType">${options(m.employmentTypes, emp.type || "Permanent")}</select></label>
      <label class="field">Contract<select name="contract">${options(m.contracts, emp.contract || "Full time")}</select></label>
      <label class="field">Start date<input name="startDate" type="date" value="${esc(w.startDate)}"></label>
      <label class="field">Lifecycle<select name="lifecycle">${options(m.lifecycles, i.lifecycleStatus || "employed")}</select></label>
      <label class="field">Cost centre<input name="costCentre" value="${esc(c.costCentre)}"></label>
      <label class="field">Avatar URL <span>(blank = generated)</span><input name="avatar" value="${esc(isNew || (u.about?.avatar || "").includes("/avatars/") ? "" : u.about?.avatar)}" placeholder="https://…"></label>
    </div>
    <datalist id="dl-pronouns"><option>She/Her</option><option>He/Him</option><option>They/Them</option></datalist>
    <datalist id="dl-depts">${depts.map((d) => `<option value="${esc(d)}">`).join("")}</datalist>
    <datalist id="dl-teams">${teams.map((t) => `<option value="${esc(t)}">`).join("")}</datalist>
    <datalist id="dl-emails">${emails.map((e) => `<option value="${esc(e.email)}">${esc(e.name)}</option>`).join("")}</datalist>`;
}

function readUserForm(form) {
  const v = (n) => { const el = form.elements[n]; return el ? el.value.trim() : undefined; };
  const nz = (x) => (x === "" ? null : x);
  const body = {
    firstName: v("firstName"), surname: v("surname"),
    personal: { preferredName: nz(v("preferredName")), pronouns: nz(v("pronouns")) },
    about: { avatar: nz(v("avatar")) },
    work: {
      title: nz(v("title")), department: nz(v("department")), site: nz(v("site")), startDate: nz(v("startDate")),
      reportsTo: nz(v("manager")),
      custom: { division: nz(v("division")), team: nz(v("team")), jobLevel: nz(v("jobLevel")), costCentre: nz(v("costCentre")) },
    },
    payroll: { employment: { type: v("employmentType"), contract: v("contract") } },
    internal: { lifecycleStatus: v("lifecycle") },
  };
  if (v("displayName")) body.displayName = v("displayName");
  if (form.elements.email) body.email = v("email");
  return body;
}

function wireDivisionCascade(form, m) {
  const refresh = () => {
    const div = form.elements.division.value, dept = form.elements.department.value;
    $("#dl-depts").innerHTML = Object.keys(m.org[div] || {}).map((d) => `<option value="${esc(d)}">`).join("");
    $("#dl-teams").innerHTML = ((m.org[div] || {})[dept] || []).map((t) => `<option value="${esc(t)}">`).join("");
  };
  form.elements.division.onchange = refresh;
  form.elements.department.oninput = refresh;
}

async function renderUser(email) {
  const [m, emails] = await Promise.all([meta(), allEmails()]);
  if (email === "new") {
    main().innerHTML = `
      <div class="page-head"><div><a href="#/users">← People</a><h1>New person</h1></div></div>
      <form class="card" id="uf">${userForm({}, m, emails, true)}
        <div class="row" style="margin-top:16px"><button class="primary" type="submit">Create</button>
        <span class="muted small">Address, region and legal entity are derived from the site.</span></div></form>`;
    const form = $("#uf");
    wireDivisionCascade(form, m);
    form.onsubmit = async (e) => {
      e.preventDefault();
      const u = await guard(() => api("/api/users", { method: "POST", body: readUserForm(form) }), "Person created");
      if (u) { invalidateCaches(); location.hash = "#/users/" + enc(u.email); }
    };
    return;
  }
  const [u, groups] = await Promise.all([api("/api/users/" + enc(email)), api(`/api/users/${enc(email)}/groups`)]);
  const w = u.work, c = w.custom || {}, i = u.internal;
  main().innerHTML = `
    <div class="page-head">
      <div class="who big">${avatarImg({ id: u.id, avatar: u.about?.avatar }, 72)}
        <div><a href="#/users">← People</a><h1>${esc(u.displayName)} ${lifecycleBadge(i.lifecycleStatus)}</h1>
        <div class="sub">${esc(w.title || "")} · ${esc(u.email)} · ${esc(w.employeeIdInCompany || "")}</div></div></div>
      <div class="row">
        ${i.lifecycleStatus === "terminated" ? `<button id="rehire">Rehire</button>` : `<button id="terminate" class="danger">Terminate</button>`}
        <button id="rename">Change email</button><button id="delete" class="danger">Delete</button>
      </div>
    </div>
    <div class="tabs" role="tablist">
      <button class="active" data-tab="profile">Profile</button><button data-tab="groups">Groups (${groups.length})</button>
      <button data-tab="reports">Reports (${w.directReports.length})</button><button data-tab="json">Raw record</button>
    </div>
    <section data-panel="profile">
      <div class="grid cols-3" style="grid-template-columns: 2fr 1fr">
        <form class="card" id="uf">${userForm(u, m, emails, false)}
          <div class="row" style="margin-top:16px"><button class="primary" type="submit">Save changes</button></div></form>
        <div class="card"><h2>Derived</h2><dl class="kv">
          <dt>Status</dt><dd>${esc(i.status)}</dd>
          <dt>Region</dt><dd>${esc(c.region)}</dd><dt>Legal entity</dt><dd>${esc(c.legalEntity)}</dd>
          <dt>Location</dt><dd>${esc([u.address?.city, u.address?.state, u.address?.country].filter(Boolean).join(", "))}</dd>
          <dt>Timezone</dt><dd>${esc(u.address?.timezone)}</dd>
          <dt>Manager</dt><dd>${w.reportsTo ? `${userLink(w.reportsTo.email)}<br><span class="muted small">${esc(w.reportsTo.displayName)}</span>` : "—"}</dd>
          <dt>Tenure</dt><dd>${esc(w.tenureYears)} years</dd>
          ${i.terminationDate ? `<dt>Terminated</dt><dd>${esc(i.terminationDate)} (${esc(i.terminationReason)})</dd>` : ""}
          <dt>HiBob id</dt><dd class="mono">${esc(u.id)}</dd>
        </dl></div>
      </div>
    </section>
    <section data-panel="groups" hidden><div class="table-wrap"><table>
      <thead><tr><th>Group</th><th>Display name</th><th>Membership</th></tr></thead>
      <tbody>${groups.map((g) => `<tr><td>${groupLink(g.name)}</td><td>${esc(g.displayName)}</td>
        <td>${g.static ? `<span class="badge accent">static</span>` : `<span class="badge info">via query</span>`}</td></tr>`).join("")
        || `<tr><td colspan="3" class="empty">Not in any group</td></tr>`}</tbody></table></div></section>
    <section data-panel="reports" hidden><div class="card list-rows">${w.directReports.map((e) => `<div class="lr">${userLink(e)}</div>`).join("") || `<div class="empty">No direct reports</div>`}</div></section>
    <section data-panel="json" hidden><div class="card">
      <p class="muted small" style="margin-top:0">Edit the full HiBob-style record. Derived fields (manager links, reports, region, status) are recomputed on save.</p>
      <textarea id="raw" class="mono" rows="28">${esc(JSON.stringify(u, null, 2))}</textarea>
      <div class="row" style="margin-top:10px"><button class="primary" id="save-raw">Save record</button></div></div></section>`;

  $$(".tabs button").forEach((b) => (b.onclick = () => {
    $$(".tabs button").forEach((x) => x.classList.toggle("active", x === b));
    $$("section[data-panel]").forEach((s) => (s.hidden = s.dataset.panel !== b.dataset.tab));
  }));
  const form = $("#uf");
  wireDivisionCascade(form, m);
  const reload = () => { invalidateCaches(); router(); };
  form.onsubmit = async (e) => {
    e.preventDefault();
    if (await guard(() => api("/api/users/" + enc(email), { method: "PATCH", body: readUserForm(form) }), "Saved")) reload();
  };
  $("#save-raw").onclick = async () => {
    let body;
    try { body = JSON.parse($("#raw").value); } catch (e) { return toast("Invalid JSON: " + e.message, true); }
    if (await guard(() => api("/api/users/" + enc(email), { method: "PUT", body }), "Saved")) reload();
  };
  $("#terminate") && ($("#terminate").onclick = async () => {
    const date = prompt("Termination date (YYYY-MM-DD)", new Date().toISOString().slice(0, 10));
    if (!date) return;
    const reason = prompt("Reason", "Resignation") || "Resignation";
    if (await guard(() => api(`/api/users/${enc(email)}/terminate`, { method: "POST", body: { terminationDate: date, reason } }), "Terminated")) reload();
  });
  $("#rehire") && ($("#rehire").onclick = async () => {
    if (await guard(() => api("/api/users/" + enc(email), { method: "PATCH", body: { internal: { lifecycleStatus: "employed" } } }), "Rehired")) reload();
  });
  $("#rename").onclick = async () => {
    const ne = prompt("New email address", email);
    if (!ne || ne === email) return;
    const r = await guard(() => api(`/api/users/${enc(email)}/rename`, { method: "POST", body: { newEmail: ne } }), "Email changed");
    if (r) { invalidateCaches(); location.hash = "#/users/" + enc(r.email); }
  };
  $("#delete").onclick = async () => {
    if (!confirm(`Permanently delete ${email}? (Terminating is usually what you want.)`)) return;
    if (await guard(() => api("/api/users/" + enc(email), { method: "DELETE" }), "Deleted")) { invalidateCaches(); location.hash = "#/users"; }
  };
}

// ------------------------------------------------------------------- groups
const groupState = { search: "", kind: "", type: "" };

async function renderGroups() {
  const groups = await api("/api/groups");
  main().innerHTML = `
    <div class="page-head"><div><h1>Groups</h1><div class="sub">Static members, dynamic queries, or both. Use memberOf("…") to nest groups.</div></div>
      <button class="primary" id="new-group">New group</button></div>
    <div class="card row">
      <input id="g-search" placeholder="Search groups" style="flex:1; min-width:200px" value="${esc(groupState.search)}">
      <select id="g-kind">${options(["div", "dept", "team", "region", "entity", "other"], groupState.kind, "All kinds")}</select>
      <select id="g-type">${options(["static", "dynamic", "mixed"], groupState.type, "Any type")}</select>
    </div>
    <div id="g-results" style="margin-top:16px"></div>`;
  $("#new-group").onclick = () => (location.hash = "#/groups/new");
  const draw = () => {
    const kinds = ["div", "dept", "team", "region", "entity"];
    const s = groupState.search.toLowerCase();
    const rows = groups.filter((g) => {
      const prefix = g.name.split("-")[0];
      const kind = kinds.includes(prefix) ? prefix : "other";
      return (!groupState.kind || groupState.kind === kind) && (!groupState.type || g.type === groupState.type) &&
        (!s || `${g.name} ${g.displayName} ${g.description}`.toLowerCase().includes(s));
    });
    $("#g-results").innerHTML = `<div class="table-wrap"><table>
      <thead><tr><th>Group</th><th>Type</th><th class="right">Members</th><th>Definition</th></tr></thead>
      <tbody>${rows.map((g) => `<tr class="clickable" data-name="${esc(g.name)}">
        <td><strong>${esc(g.displayName)}</strong><span class="sub">${esc(g.name)}</span></td>
        <td>${typeBadge(g.type)}</td>
        <td class="right">${g.error ? `<span class="badge bad" title="${esc(g.error)}">error</span>` : g.memberCount}</td>
        <td><span class="mono small">${esc(g.query || "")}</span>${g.staticCount ? `<span class="sub">${g.staticCount} static member(s)</span>` : ""}</td>
      </tr>`).join("") || `<tr><td colspan="4" class="empty">No groups</td></tr>`}</tbody></table></div>
      <div class="pager muted">${rows.length} of ${groups.length} groups</div>`;
    $$("tr[data-name]").forEach((tr) => (tr.onclick = () => (location.hash = "#/groups/" + enc(tr.dataset.name))));
  };
  $("#g-search").oninput = (e) => { groupState.search = e.target.value; draw(); };
  $("#g-kind").onchange = (e) => { groupState.kind = e.target.value; draw(); };
  $("#g-type").onchange = (e) => { groupState.type = e.target.value; draw(); };
  draw();
}

async function renderGroup(name) {
  const isNew = name === "new";
  const [m, emails, names] = await Promise.all([meta(), allEmails(), groupNames()]);
  const g = isNew ? { name: "", displayName: "", description: "", members: [], query: "" } : await api("/api/groups/" + enc(name));
  g.members = g.members || [];
  const aliasList = Object.entries(m.aliases).map(([a, path]) => `<button type="button" class="chip insert" data-text="${esc(a)}" title="${esc(path)}">${esc(a)}</button>`).join("");
  main().innerHTML = `
    <div class="page-head">
      <div><a href="#/groups">← Groups</a><h1>${isNew ? "New group" : esc(g.displayName)} ${isNew ? "" : typeBadge(g.type)}</h1>
        ${isNew ? "" : `<div class="sub mono">${esc(g.name)}</div>`}</div>
      ${isNew ? "" : `<button id="g-delete" class="danger">Delete</button>`}
    </div>
    <div class="grid cols-2" style="grid-template-columns: minmax(0, 3fr) minmax(0, 2fr)">
      <form class="card" id="gf">
        <div class="form-grid">
          <label class="field">Name (slug) *<input name="name" value="${esc(g.name)}" ${isNew ? "required" : "readonly"} placeholder="team-payments-oncall" pattern="[a-z0-9][a-z0-9._\\-]*"></label>
          <label class="field">Display name<input name="displayName" value="${esc(g.displayName)}"></label>
        </div>
        <label class="field" style="margin-top:12px">Description<input name="description" value="${esc(g.description)}"></label>
        <label class="field" style="margin-top:12px">Query <span>(optional — users matching are members)</span>
          <textarea name="query" class="mono" rows="4" placeholder='team == "Payments" and status == "Active"'>${esc(g.query || "")}</textarea></label>
        <div class="row" style="margin-top:6px"><button type="button" id="q-test">Test query</button>
          <select id="q-group"><option value="">Insert memberOf(…)</option>${options(names)}</select>
          <span id="q-result" class="muted small"></span></div>
        <details style="margin-top:8px"><summary>Fields &amp; operators</summary>
          <div class="chips" style="margin-top:8px">${aliasList}</div>
          <p class="muted small">Any dotted path also works, e.g. <code>work.custom.costCentre</code>. Operators: ${m.operators.map((o) => `<code>${esc(o)}</code>`).join(" ")}</p>
        </details>
        <label class="field" style="margin-top:14px">Static members <span>(one email per line)</span>
          <textarea name="members" class="mono" rows="6">${esc((g.members || []).join("\n"))}</textarea></label>
        <div class="row" style="margin-top:6px"><input id="add-member" list="dl-emails" placeholder="Add member by email" style="flex:1"><button type="button" id="add-btn">Add</button></div>
        <datalist id="dl-emails">${emails.map((e) => `<option value="${esc(e.email)}">${esc(e.name)}</option>`).join("")}</datalist>
        <div class="row" style="margin-top:16px"><button class="primary" type="submit">${isNew ? "Create group" : "Save changes"}</button></div>
      </form>
      <div class="card">
        ${isNew ? `<h2>Preview</h2><div id="preview" class="muted">Test the query to preview members.</div>` : `
        <h2>Resolved members <span class="badge">${g.memberCount}</span></h2>
        ${g.references?.length ? `<div class="small muted">Includes groups</div><div class="chips" style="margin:4px 0 10px">${g.references.map(groupLink).join("")}</div>` : ""}
        ${g.usedBy?.length ? `<div class="small muted">Used by</div><div class="chips" style="margin:4px 0 10px">${g.usedBy.map((u) => {
          if (!u.startsWith("integration:")) return groupLink(u);
          const [, iid, part] = u.split(":");
          return `<a class="chip" href="#/integrations/${enc(iid)}/${part === "roles" ? "mapping" : "users"}">${esc(iid)} · ${esc(part)}</a>`;
        }).join("")}</div>` : ""}
        <input id="m-filter" placeholder="Filter members" style="width:100%; margin-bottom:8px">
        <div class="list-rows" id="m-list" style="max-height:520px; overflow:auto"></div>`}
      </div>
    </div>`;

  const form = $("#gf");
  const q = form.elements.query;
  const insert = (text) => { const s = q.selectionStart ?? q.value.length; q.value = q.value.slice(0, s) + text + q.value.slice(q.selectionEnd ?? s); q.focus(); };
  $$(".insert").forEach((b) => (b.onclick = () => insert(b.dataset.text + " ")));
  $("#q-group").onchange = (e) => { if (e.target.value) insert(`memberOf("${e.target.value}")`); e.target.value = ""; };
  $("#add-btn").onclick = () => {
    const v = $("#add-member").value.trim().toLowerCase();
    if (!v) return;
    const list = form.elements.members.value.split(/\s+/).filter(Boolean);
    if (!list.includes(v)) list.push(v);
    form.elements.members.value = list.join("\n");
    $("#add-member").value = "";
  };
  $("#q-test").onclick = async () => {
    const query = q.value.trim();
    if (!query) return ($("#q-result").textContent = "No query");
    try {
      const r = await api("/api/query", { method: "POST", body: { query, limit: 200 } });
      if (!$("#q-result")) return;
      $("#q-result").innerHTML = `<span class="badge ok">${r.count} match${r.count === 1 ? "" : "es"}</span>`;
      if (isNew) $("#preview").innerHTML = r.emails.map((e) => `<div>${userLink(e)}</div>`).join("") + (r.count > 200 ? `<div class="muted">…and ${r.count - 200} more</div>` : "");
    } catch (e) { if ($("#q-result")) $("#q-result").innerHTML = `<span class="badge bad">${esc(e.message)}</span>`; }
  };
  if (!isNew) {
    const people = new Map(emails.map((e) => [e.email, e]));
    const drawMembers = (f = "") => {
      const list = g.resolvedMembers.filter((e) => e.includes(f.toLowerCase()));
      $("#m-list").innerHTML = list.slice(0, 400).map((e) => {
        const p = people.get(e) || { email: e };
        return `<div class="lr"><div class="who">${p.id ? avatarImg(p, 26) : ""}<div>${userLink(e)}<span class="sub">${esc(p.title || "")}</span></div></div>${g.members.includes(e) ? `<span class="badge accent">static</span>` : ""}</div>`;
      }).join("") || `<div class="empty">No members</div>`;
    };
    drawMembers();
    $("#m-filter").oninput = (e) => drawMembers(e.target.value);
    $("#g-delete").onclick = async () => {
      if (!confirm(`Delete group ${g.name}?`)) return;
      if (await guard(() => api("/api/groups/" + enc(g.name), { method: "DELETE" }), "Group deleted")) { invalidateCaches(); location.hash = "#/groups"; }
    };
  }
  form.onsubmit = async (e) => {
    e.preventDefault();
    const body = {
      displayName: form.elements.displayName.value.trim() || null,
      description: form.elements.description.value.trim(),
      query: q.value.trim(),
      members: form.elements.members.value.split(/\s+/).map((x) => x.trim()).filter(Boolean),
    };
    const r = isNew
      ? await guard(() => api("/api/groups", { method: "POST", body: { name: form.elements.name.value.trim(), ...body } }), "Group created")
      : await guard(() => api("/api/groups/" + enc(g.name), { method: "PATCH", body }), "Group saved");
    if (r) { invalidateCaches(); if (isNew) location.hash = "#/groups/" + enc(r.name); else router(); }
  };
}

// ---------------------------------------------------------------- org chart
// Division colours match the shirt colour in the generated avatars (mockhris/avatar.py).
const DIVISION_COLOURS = ["#4f46e5", "#0e7490", "#b45309", "#be123c", "#15803d", "#7c3aed", "#1d4ed8", "#c2410c", "#0f766e",
  "#9d174d", "#4d7c0f", "#334155", "#a16207", "#6d28d9", "#0369a1"];
const divisionColour = (d) => (d ? DIVISION_COLOURS[[...d].reduce((a, c) => a + c.charCodeAt(0), 0) % DIVISION_COLOURS.length] : "#64748b");

const orgState = { view: "chart", expanded: null, focus: null, scale: 1, x: 0, y: 0, showTerm: false, data: null };

async function renderOrg() {
  orgState.showTerm = storage("mockhris.org.term") === "1";
  orgState.view = storage("mockhris.org.view") || "chart";
  orgState.data = await api("/api/org-chart" + (orgState.showTerm ? "?includeTerminated=true" : ""));
  const index = new Map(), parent = new Map();
  const walk = (n, p) => { index.set(n.email, n); if (p) parent.set(n.email, p.email); (n.reports || []).forEach((k) => walk(k, n)); };
  orgState.data.forEach((r) => walk(r, null));
  orgState.index = index; orgState.parent = parent;
  if (!orgState.expanded) orgState.expanded = new Set(orgState.data.map((r) => r.email));
  if (orgState.focus && !index.has(orgState.focus)) orgState.focus = null;

  main().innerHTML = `
    <div class="page-head"><div><h1>Org chart</h1><div class="sub">${index.size} people · reporting lines across every entity and subsidiary</div></div>
      <div class="row">
        <div class="seg"><button data-view="chart" class="${orgState.view === "chart" ? "on" : ""}">Chart</button><button data-view="list" class="${orgState.view === "list" ? "on" : ""}">List</button></div>
        <input id="o-find" placeholder="Find person…" list="o-dl" style="width:200px">
        <label class="check"><input type="checkbox" id="o-term" ${orgState.showTerm ? "checked" : ""}> Terminated</label>
      </div></div>
    <div id="o-body"></div>
    <datalist id="o-dl">${[...index.values()].map((n) => `<option value="${esc(n.email)}">${esc(n.displayName)} — ${esc(n.title || "")}</option>`).join("")}</datalist>`;
  $$(".page-head .seg button").forEach((b) => (b.onclick = () => { storage("mockhris.org.view", b.dataset.view); renderOrg(); }));
  $("#o-term").onchange = (e) => { storage("mockhris.org.term", e.target.checked ? "1" : "0"); renderOrg(); };
  $("#o-find").onchange = (e) => findInOrg(e.target.value.trim().toLowerCase());
  if (orgState.view === "list") renderOrgList(); else renderOrgChart(true);
}

function findInOrg(email) {
  if (!orgState.index.has(email)) return toast("Not found in chart", true);
  if (orgState.view === "list") {
    const li = $(`#tree li[data-email="${CSS.escape(email)}"]`);
    for (let p = li.parentElement; p && p.id !== "tree"; p = p.parentElement) if (p.tagName === "UL") { p.hidden = false; const t = p.parentElement.querySelector(":scope > .node .toggle"); if (t) t.textContent = "▾"; }
    const node = li.querySelector(".node");
    node.scrollIntoView({ block: "center", behavior: "smooth" });
    node.classList.add("hl"); setTimeout(() => node.classList.remove("hl"), 2500);
    return;
  }
  // make the person visible: leave focus mode if needed and expand every ancestor
  if (orgState.focus && !isDescendant(email, orgState.focus)) orgState.focus = null;
  for (let p = orgState.parent.get(email); p; p = orgState.parent.get(p)) orgState.expanded.add(p);
  drawChart();
  centerOn(email, true);
}

const isDescendant = (email, ancestor) => { for (let p = email; p; p = orgState.parent.get(p)) if (p === ancestor) return true; return false; };

// ---- chart view
function renderOrgChart(fit) {
  $("#o-body").innerHTML = `
    <div class="oc-toolbar row">
      <div id="oc-crumbs" class="row small"></div><span class="spacer"></span>
      <button id="oc-expand">Expand all</button><button id="oc-collapse">Collapse all</button>
      <div class="seg"><button id="oc-out" title="Zoom out">−</button><button id="oc-pct" title="Reset to 100%"></button><button id="oc-in" title="Zoom in">+</button></div>
      <button id="oc-fit">Fit</button>
    </div>
    <div class="oc-viewport" id="oc-vp"><div class="oc-canvas" id="oc-canvas"></div>
      <div class="oc-hint small muted">Drag to pan · scroll to zoom · ＋ expands a team · ⌖ focuses a branch</div></div>`;
  const vp = $("#oc-vp");
  $("#oc-expand").onclick = () => { orgState.index.forEach((n) => { if ((n.reports || []).length) orgState.expanded.add(n.email); }); drawChart(); fitChart(); };
  $("#oc-collapse").onclick = () => { orgState.expanded = new Set(orgState.focus ? [orgState.focus] : orgState.data.map((r) => r.email)); drawChart(); fitChart(); };
  $("#oc-in").onclick = () => zoomAt(1.2);
  $("#oc-out").onclick = () => zoomAt(1 / 1.2);
  $("#oc-pct").onclick = () => zoomAt(1 / orgState.scale);
  $("#oc-fit").onclick = fitChart;

  vp.addEventListener("wheel", (e) => {
    e.preventDefault();
    const r = vp.getBoundingClientRect();
    zoomAt(e.deltaY < 0 ? 1.1 : 1 / 1.1, e.clientX - r.left, e.clientY - r.top);
  }, { passive: false });
  let drag = null;
  vp.addEventListener("pointerdown", (e) => {
    if (e.button !== 0 || e.target.closest("a, button")) return;
    drag = { x: e.clientX, y: e.clientY, ox: orgState.x, oy: orgState.y };
    vp.setPointerCapture(e.pointerId); vp.classList.add("dragging");
  });
  vp.addEventListener("pointermove", (e) => {
    if (!drag) return;
    orgState.x = drag.ox + e.clientX - drag.x; orgState.y = drag.oy + e.clientY - drag.y; applyTransform();
  });
  const end = () => { drag = null; vp.classList.remove("dragging"); };
  vp.addEventListener("pointerup", end); vp.addEventListener("pointercancel", end);
  vp.addEventListener("click", (e) => {
    const t = e.target.closest("[data-toggle]");
    if (t) return toggleNode(t.dataset.toggle);
    const f = e.target.closest("[data-focus]");
    if (f) { orgState.focus = f.dataset.focus; orgState.expanded.add(f.dataset.focus); drawChart(); fitChart(); }
  });
  drawChart();
  if (fit) openChart(); else applyTransform();
}

// Initial view: fit if it is readable, otherwise a readable zoom anchored on the top of the tree.
function openChart() {
  fitChart();
  if (orgState.scale >= 0.75) return;
  const vp = $("#oc-vp"), c = $("#oc-canvas");
  orgState.scale = 0.8;
  orgState.x = (vp.clientWidth - c.scrollWidth * orgState.scale) / 2;
  orgState.y = 10;
  applyTransform();
}

function toggleNode(email) {
  const before = cardRect(email);
  if (orgState.expanded.has(email)) orgState.expanded.delete(email); else orgState.expanded.add(email);
  drawChart();
  const after = cardRect(email);  // keep the clicked card fixed on screen
  if (before && after) { orgState.x += before.left - after.left; orgState.y += before.top - after.top; applyTransform(); }
}

const cardRect = (email) => $(`#oc-canvas .oc-card[data-email="${CSS.escape(email)}"]`)?.getBoundingClientRect();

function ocCard(n, compact) {
  const kids = n.reports || [];
  const open = orgState.expanded.has(n.email);
  const size = compact ? 32 : 46;
  return `<div class="oc-card ${compact ? "compact" : ""} ${n.lifecycle === "terminated" ? "term" : ""}" data-email="${esc(n.email)}" style="--div:${divisionColour(n.division)}">
    ${avatarImg(n, size)}
    <div class="oc-text">
      <a class="oc-name" href="#/users/${enc(n.email)}" title="${esc(n.displayName)}">${esc(n.displayName)}</a>
      <div class="oc-title" title="${esc(n.title || "")}">${esc(n.title || "")}</div>
      ${compact ? "" : `<div class="oc-meta" title="${esc(n.team || "")}">${esc(n.team || "")}${n.site ? ` · ${esc(n.site)}` : ""}</div>`}
      ${n.lifecycle !== "employed" ? `<div style="margin-top:2px">${lifecycleBadge(n.lifecycle)}</div>` : ""}
    </div>
    ${kids.length ? `<button class="oc-focus" data-focus="${esc(n.email)}" title="Show only this branch">⌖</button>
      <button class="oc-toggle" data-toggle="${esc(n.email)}" aria-expanded="${open}" title="${open ? "Collapse" : "Expand"}">${open ? "−" : "+"} ${n.reportCount}</button>` : ""}
  </div>`;
}

function ocNode(n) {
  const kids = n.reports || [];
  let children = "";
  if (kids.length && orgState.expanded.has(n.email)) {
    const leaves = kids.filter((k) => !(k.reports || []).length);
    const managers = kids.filter((k) => (k.reports || []).length);
    const items = managers.map(ocNode);
    // Individual contributors are stacked in one column to keep the chart narrow.
    if (leaves.length) items.unshift(`<li><div class="oc-stack">${leaves.map((l) => ocCard(l, true)).join("")}</div></li>`);
    children = `<ul>${items.join("")}</ul>`;
  }
  return `<li>${ocCard(n, false)}${children}</li>`;
}

function drawChart() {
  const roots = orgState.focus ? [orgState.index.get(orgState.focus)] : orgState.data;
  $("#oc-canvas").innerHTML = `<div class="oc"><ul>${roots.map(ocNode).join("")}</ul></div>`;
  if (orgState.focus) {
    const crumbs = [];
    for (let p = orgState.parent.get(orgState.focus); p; p = orgState.parent.get(p)) crumbs.unshift(p);
    $("#oc-crumbs").innerHTML = `<button class="link" data-crumb="">Whole company</button>` +
      crumbs.map((e) => `<span class="muted">›</span><button class="link" data-crumb="${esc(e)}">${esc(orgState.index.get(e).displayName)}</button>`).join("") +
      `<span class="muted">›</span><strong>${esc(orgState.index.get(orgState.focus).displayName)}</strong>`;
    $$("#oc-crumbs [data-crumb]").forEach((b) => (b.onclick = () => {
      orgState.focus = b.dataset.crumb || null;
      if (orgState.focus) orgState.expanded.add(orgState.focus);
      drawChart(); fitChart();
    }));
  } else {
    $("#oc-crumbs").innerHTML = `<span class="muted">Whole company</span>`;
  }
}

function applyTransform() {
  const c = $("#oc-canvas");
  if (!c) return;
  c.style.transform = `translate(${orgState.x}px, ${orgState.y}px) scale(${orgState.scale})`;
  $("#oc-pct").textContent = Math.round(orgState.scale * 100) + "%";
}

function zoomAt(factor, cx, cy) {
  const vp = $("#oc-vp");
  if (cx === undefined) { cx = vp.clientWidth / 2; cy = vp.clientHeight / 2; }
  const s = Math.min(2, Math.max(0.15, orgState.scale * factor));
  orgState.x = cx - (cx - orgState.x) * (s / orgState.scale);
  orgState.y = cy - (cy - orgState.y) * (s / orgState.scale);
  orgState.scale = s;
  applyTransform();
}

function fitChart() {
  const vp = $("#oc-vp"), c = $("#oc-canvas");
  const w = c.scrollWidth, h = c.scrollHeight;
  const s = Math.min(1, Math.max(0.15, Math.min(vp.clientWidth / w, (vp.clientHeight - 30) / h)));
  orgState.scale = s;
  orgState.x = (vp.clientWidth - w * s) / 2;
  orgState.y = Math.max(0, (vp.clientHeight - 30 - h * s) / 2);
  applyTransform();
}

function centerOn(email, highlight) {
  const vp = $("#oc-vp"), card = $(`#oc-canvas .oc-card[data-email="${CSS.escape(email)}"]`);
  if (!card) return;
  if (orgState.scale < 0.7) orgState.scale = 0.9;
  applyTransform();
  const vr = vp.getBoundingClientRect(), cr = card.getBoundingClientRect();
  orgState.x += vr.left + vr.width / 2 - (cr.left + cr.width / 2);
  orgState.y += vr.top + vr.height / 2 - (cr.top + cr.height / 2);
  applyTransform();
  if (highlight) { card.classList.add("hl"); setTimeout(() => card.classList.remove("hl"), 2500); }
}

// ---- list view
function renderOrgList() {
  $("#o-body").innerHTML = `<div class="row" style="margin-bottom:10px"><button id="o-expand">Expand all</button><button id="o-collapse">Collapse</button></div>
    <div class="card tree" id="tree"></div>`;
  const nodeHtml = (n, depth) => {
    const kids = n.reports || [];
    return `<li data-email="${esc(n.email)}">
      <span class="node ${n.lifecycle === "terminated" ? "term" : ""}">
        <button class="toggle" ${kids.length ? "" : "disabled"} aria-label="toggle">${kids.length ? (depth < 1 ? "▾" : "▸") : "·"}</button>
        ${avatarImg(n, 24)}
        <a href="#/users/${enc(n.email)}"><strong>${esc(n.displayName)}</strong></a>
        <span class="muted">${esc(n.title || "")}</span>
        ${n.team ? `<span class="badge">${esc(n.team)}</span>` : ""}${n.lifecycle !== "employed" ? lifecycleBadge(n.lifecycle) : ""}
        ${kids.length ? `<span class="muted small">${n.reportCount} report${n.reportCount === 1 ? "" : "s"}</span>` : ""}
      </span>
      ${kids.length ? `<ul ${depth < 1 ? "" : "hidden"}>${kids.map((k) => nodeHtml(k, depth + 1)).join("")}</ul>` : ""}</li>`;
  };
  $("#tree").innerHTML = `<ul>${orgState.data.map((r) => nodeHtml(r, 0)).join("")}</ul>`;
  $("#tree").onclick = (e) => {
    const t = e.target.closest(".toggle");
    if (!t || t.disabled) return;
    const ul = t.closest("li").querySelector(":scope > ul");
    ul.hidden = !ul.hidden; t.textContent = ul.hidden ? "▸" : "▾";
  };
  const setAll = (open) => $$("#tree li > ul").forEach((ul) => { ul.hidden = !open; const t = ul.parentElement.querySelector(".toggle"); if (t) t.textContent = open ? "▾" : "▸"; });
  $("#o-expand").onclick = () => setAll(true);
  $("#o-collapse").onclick = () => setAll(false);
}

// ------------------------------------------------------------- integrations
// PingOne-style outbound provisioning: connection -> users in scope -> attribute mapping -> groups -> actions.
let REF = null;
async function reference() { if (!REF) REF = await api("/api/integrations/reference"); return REF; }

const INTEG_TABS = [
  ["overview", "Overview"], ["connection", "Connection"], ["users", "Users in scope"],
  ["mapping", "Attribute mapping"], ["groups", "Group provisioning"], ["actions", "Provisioning actions"], ["activity", "Activity"],
];
const runBadge = (s) => `<span class="badge ${{ succeeded: "ok", running: "info", partial: "warn", failed: "bad", error: "bad" }[s] || ""}">${esc(s)}</span>`;
const typeIcon = (t) => `<span class="int-icon ${t === "github-emu" ? "gh" : "scim"}">${t === "github-emu" ? "GH" : "S2"}</span>`;
const scopeLabel = (s) => (s.mode === "all" ? "All users" : s.mode === "query" ? "Users matching a query" : `Members of ${s.group}`);

async function renderIntegrations() {
  if (!integ.dirty) integ.draft = null;  // reload from the server next time an integration is opened
  const [list, ref] = await Promise.all([api("/api/integrations"), reference()]);
  main().innerHTML = `
    <div class="page-head"><div><h1>Integrations</h1><div class="sub">Provision HR identities to third-party applications over SCIM 2.0</div></div>
      <button class="primary" id="add-int">Add integration</button></div>
    <div id="add-panel"></div>
    <div class="int-grid">${list.map((i) => `
      <a class="card int-card" href="#/integrations/${enc(i.id)}">
        <div class="row">${typeIcon(i.type)}<div style="min-width:0"><strong>${esc(i.name)}</strong><div class="muted small">${esc(i.typeLabel)}</div></div>
          <span class="spacer"></span>${i.enabled ? `<span class="badge ok">enabled</span>` : `<span class="badge">disabled</span>`}</div>
        <div class="mono small int-url" title="${esc(i.connection.baseUrl)}">${esc(i.connection.baseUrl)}</div>
        <dl class="kv small">
          <dt>Users</dt><dd>${esc(scopeLabel(i.scope))}</dd>
          <dt>Mappings</dt><dd>${i.mappings.length} attributes</dd>
          <dt>Groups</dt><dd>${i.groups.enabled ? `${i.groups.patterns.length} pattern(s)` : "not pushed"}</dd>
          <dt>Last sync</dt><dd>${i.lastRun ? `${runBadge(i.lastRun.status)} <span class="muted">${fmtDate(i.lastRun.finishedAt || i.lastRun.startedAt)}</span>` : `<span class="muted">never</span>`}</dd>
        </dl>
      </a>`).join("") || `<div class="empty">No integrations yet</div>`}
    </div>`;
  $("#add-int").onclick = () => {
    const tpls = Object.values(ref.templates);
    $("#add-panel").innerHTML = `
      <form class="card" id="add-form" style="margin-bottom:16px">
        <h2>Add integration</h2>
        <div class="tiles">${tpls.map((t, n) => `<label class="tile"><input type="radio" name="type" value="${esc(t.type)}" ${n === 0 ? "checked" : ""}>
          ${typeIcon(t.type)}<div><strong>${esc(t.label)}</strong><div class="muted small">${esc(t.description)}</div></div></label>`).join("")}</div>
        <div class="form-grid" style="margin-top:14px">
          <label class="field">Name<input name="name" required placeholder="e.g. Atlassian Cloud"></label>
          <label class="field">ID (slug)<input name="id" required pattern="[a-z0-9][a-z0-9\\-]{1,40}" placeholder="atlassian"></label>
          <label class="field" style="grid-column: span 2">SCIM base URL<input name="baseUrl" placeholder="template default"></label>
          <label class="field">Bearer token<input name="token" type="password" autocomplete="off" placeholder="template default"></label>
        </div>
        <div class="row" style="margin-top:14px"><button class="primary" type="submit">Create</button><button type="button" id="add-cancel">Cancel</button>
          <span class="muted small">Starts with the template's scope, attribute mappings and group settings — adjust them next.</span></div>
      </form>`;
    const f = $("#add-form");
    f.elements.name.oninput = () => { if (!f.elements.id.dataset.touched) f.elements.id.value = f.elements.name.value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40); };
    f.elements.id.oninput = () => (f.elements.id.dataset.touched = "1");
    $("#add-cancel").onclick = () => ($("#add-panel").innerHTML = "");
    f.onsubmit = async (e) => {
      e.preventDefault();
      const conn = {};
      if (f.elements.baseUrl.value.trim()) conn.baseUrl = f.elements.baseUrl.value.trim();
      if (f.elements.token.value) conn.token = f.elements.token.value;
      const r = await guard(() => api("/api/integrations", { method: "POST", body: {
        type: f.elements.type.value, id: f.elements.id.value.trim(), name: f.elements.name.value.trim(), connection: conn } }), "Integration created");
      if (r) location.hash = `#/integrations/${enc(r.id)}/connection`;
    };
  };
}

// ---- editor
const integ = { id: null, saved: null, draft: null, tab: "overview", ref: null, dirty: false };

async function renderIntegration(arg) {
  const [id, tab] = arg.split("/");
  if (integ.id !== id || !integ.draft) {
    const [i, ref] = await Promise.all([api("/api/integrations/" + enc(id)), reference()]);
    Object.assign(integ, { id, saved: i, draft: structuredClone(i), ref, dirty: false });
  }
  integ.tab = tab || "overview";
  const i = integ.draft;
  main().innerHTML = `
    <div class="page-head">
      <div class="who">${typeIcon(i.type)}<div><a href="#/integrations">← Integrations</a><h1>${esc(i.name)}</h1>
        <div class="sub">${esc(i.typeLabel)} · <span class="mono">${esc(i.id)}</span></div></div></div>
      <div class="row">
        <label class="switch" title="Enable or disable syncing"><input type="checkbox" id="int-enabled" ${i.enabled ? "checked" : ""}><span></span> ${i.enabled ? "Enabled" : "Disabled"}</label>
        <button id="hdr-plan">Plan</button><button id="hdr-sync" class="primary">Sync now</button><button id="hdr-verify">Verify</button>
      </div>
    </div>
    <div class="tabs int-tabs">${INTEG_TABS.map(([k, label]) => `<a href="#/integrations/${enc(id)}/${k}" class="${k === integ.tab ? "active" : ""}">${label}</a>`).join("")}</div>
    <div id="int-body"></div>
    <div class="savebar" id="savebar" hidden><span>Unsaved changes</span><span class="spacer"></span>
      <button id="discard">Discard</button><button class="primary" id="save">Save changes</button></div>`;
  $("#int-enabled").onchange = (e) => { i.enabled = e.target.checked; markDirty(); e.target.parentElement.lastChild.textContent = i.enabled ? " Enabled" : " Disabled"; };
  $("#save").onclick = saveIntegration;
  $("#discard").onclick = () => { integ.draft = structuredClone(integ.saved); integ.dirty = false; router(); };
  $("#hdr-plan").onclick = () => runAction("plan");
  $("#hdr-sync").onclick = () => runAction("sync");
  $("#hdr-verify").onclick = () => runAction("verify");
  if (integ.dirty) $("#savebar").hidden = false;
  const body = $("#int-body");
  await ({ overview: tabOverview, connection: tabConnection, users: tabUsers, mapping: tabMapping,
    groups: tabGroups, actions: tabActions, activity: tabActivity }[integ.tab] || tabOverview)(body, i);
}

function markDirty() { integ.dirty = true; const b = $("#savebar"); if (b) b.hidden = false; }

function draftPayload() {
  const d = integ.draft;
  return { name: d.name, description: d.description, enabled: d.enabled, connection: d.connection, scope: d.scope,
    mappings: d.mappings, roles: d.roles, groups: d.groups, actions: d.actions };
}

async function saveIntegration() {
  const r = await guard(() => api("/api/integrations/" + enc(integ.id), { method: "PUT", body: draftPayload() }), "Integration saved");
  if (r) { integ.saved = r; integ.draft = structuredClone(r); integ.dirty = false; router(); }
}

async function runAction(kind) {
  clearInterval(pollTimer);
  $("#run-box")?.classList.remove("no-fade");
  if (integ.dirty && !confirm("You have unsaved changes. Plan, sync and verify use the saved configuration. Continue?")) return;
  const box = $("#run-box");
  if (!box || integ.tab === "activity") {
    integ.pendingAction = kind;  // run once the overview tab has rendered
    if (integ.tab === "overview") return;
    location.hash = `#/integrations/${enc(integ.id)}/overview`;
    return;
  }
  if (kind === "plan") {
    box.innerHTML = `<div class="loading">Planning…</div>`;
    const p = await guard(() => api(`/api/integrations/${enc(integ.id)}/plan`, { method: "POST", body: {} }));
    if (p) drawPlan(box, p);
  } else if (kind === "sync") {
    if (!integ.saved.connection.usesBuiltInMock && !confirm(`Push changes to ${integ.saved.connection.baseUrl}?`)) return;
    const run = await guard(() => api(`/api/integrations/${enc(integ.id)}/sync`, { method: "POST", body: {} }));
    if (run) watchRun(box, run.id);
  } else {
    box.innerHTML = `<div class="loading">Reading users and groups back from the target…</div>`;
    const v = await guard(() => api(`/api/integrations/${enc(integ.id)}/verify`, { method: "POST" }));
    if (v) drawVerify(box, v);
  }
}

// ---- tab: overview
async function tabOverview(body, i) {
  const [scope, state, runs] = await Promise.all([
    api(`/api/integrations/${enc(i.id)}/scope-preview`, { method: "POST", body: integ.saved.scope }),
    api(`/api/integrations/${enc(i.id)}/state`), api(`/api/integrations/${enc(i.id)}/runs?limit=5`)]);
  const provisioned = state.users.filter((u) => u.active).length;
  body.innerHTML = `
    <div class="flow">
      <div class="flow-step"><div class="label">Source</div><strong>MockHRIS</strong><div class="muted small">HiBob-style records</div></div>
      <div class="flow-arrow">→</div>
      <a class="flow-step" href="#/integrations/${enc(i.id)}/users"><div class="label">Users in scope</div><strong>${scope.active} active</strong><div class="muted small">${esc(scopeLabel(integ.saved.scope))}</div></a>
      <div class="flow-arrow">→</div>
      <a class="flow-step" href="#/integrations/${enc(i.id)}/mapping"><div class="label">Attribute mapping</div><strong>${integ.saved.mappings.length} attributes</strong><div class="muted small">${integ.saved.roles.enabled ? `role rules: ${integ.saved.roles.rules.length}` : "no role rules"}</div></a>
      <div class="flow-arrow">→</div>
      <a class="flow-step" href="#/integrations/${enc(i.id)}/connection"><div class="label">Target</div><strong>${esc(i.typeLabel)}</strong><div class="muted small mono int-url">${esc(i.connection.baseUrl)}</div></a>
    </div>
    <div class="grid cols-4" style="margin-top:16px">
      <div class="card stat"><div class="label">In scope (active)</div><div class="value">${scope.active}</div></div>
      <div class="card stat"><div class="label">Provisioned (active)</div><div class="value">${provisioned}</div></div>
      <div class="card stat"><div class="label">Groups pushed</div><div class="value">${state.groups.length}</div></div>
      <div class="card stat"><div class="label">Last sync</div><div class="value" style="font-size:15px;padding-top:8px">${runs[0] ? `${runBadge(runs[0].status)} <span class="muted small">${fmtDate(runs[0].finishedAt || runs[0].startedAt)}</span>` : "never"}</div></div>
    </div>
    <div class="card" style="margin-top:16px"><div class="row"><h2 style="margin:0">Run</h2><span class="spacer"></span>
      <button id="ov-plan">Plan (dry run)</button><button id="ov-sync" class="primary">Sync now</button><button id="ov-verify">Verify target</button></div>
      <p class="muted small">Plan previews operations without sending anything. Verify reads users and groups back from the target and reconciles them against the HRIS — the end-to-end proof.</p>
      <div id="run-box"></div></div>`;
  $("#ov-plan").onclick = () => runAction("plan");
  $("#ov-sync").onclick = () => runAction("sync");
  $("#ov-verify").onclick = () => runAction("verify");
  if (integ.pendingAction) { const k = integ.pendingAction; integ.pendingAction = null; runAction(k); }
}

// ---- tab: connection
async function tabConnection(body, i) {
  const c = i.connection;
  const gh = i.type === "github-emu";
  body.innerHTML = `
    <div class="grid cols-2">
      <div class="card"><h2>General</h2>
        <label class="field">Name<input id="c-name" value="${esc(i.name)}"></label>
        <label class="field" style="margin-top:10px">Description<textarea id="c-desc" rows="2">${esc(i.description || "")}</textarea></label>
      </div>
      <div class="card"><h2>Target</h2>
        <label class="field">SCIM base URL<input id="c-url" class="mono" value="${esc(c.baseUrl)}"></label>
        <div class="row small" style="margin-top:6px; gap:10px">
          ${gh ? `<button type="button" class="link" data-url="${esc(location.origin)}/mock-github/scim/v2/enterprises/halcyon">Built-in mock</button>
          <button type="button" class="link" data-url="https://api.github.com/scim/v2/enterprises/YOUR-ENTERPRISE">GitHub.com</button>
          <button type="button" class="link" data-url="https://api.YOUR-SUBDOMAIN.ghe.com/scim/v2/enterprises/YOUR-ENTERPRISE">GHE.com</button>` : ""}
          <button type="button" class="link" data-url="http://host.docker.internal:8100/t/hris/scim/v2">MockSCIMTarget (from Docker)</button>
          <button type="button" class="link" data-url="http://localhost:8100/t/hris/scim/v2">MockSCIMTarget (local dev)</button>
        </div>
      </div>
      <div class="card"><h2>Authentication</h2>
        <div class="form-grid">
          <label class="field">Method<select id="c-auth">${options(integ.ref.authTypes, c.authType)}</select></label>
          <span></span>
          <label class="field auth-bearer">Bearer token<input id="c-token" type="password" autocomplete="off" ${c.tokenSource === "env" ? "disabled" : ""}
            placeholder="${c.tokenSource === "env" ? "set by environment variable" : c.tokenSet ? "saved — leave blank to keep" : gh ? "PAT with scim:enterprise scope" : "token"}"></label>
          <label class="field auth-basic">Username<input id="c-user" value="${esc(c.username || "")}"></label>
          <label class="field auth-basic">Password<input id="c-pass" type="password" autocomplete="off" placeholder="${c.passwordSet ? "saved — leave blank to keep" : ""}"></label>
        </div>
        ${gh ? `<label class="field" style="margin-top:10px; max-width:240px">X-GitHub-Api-Version<input id="c-apiv" value="${esc(c.apiVersion || "")}"></label>` : ""}
      </div>
      <div class="card"><h2>Request options</h2>
        <div class="form-grid">
          <label class="field">Timeout (s)<input id="c-timeout" type="number" min="1" value="${esc(c.timeoutSeconds)}"></label>
          <label class="field">Delay between requests (ms)<input id="c-delay" type="number" min="0" value="${esc(c.requestDelayMs)}"></label>
        </div>
        <label class="check" style="margin-top:10px"><input type="checkbox" id="c-tls" ${c.verifyTls ? "checked" : ""}> Verify TLS certificates</label>
        <h3 style="margin-top:14px">Custom headers</h3><div id="c-headers"></div><button type="button" id="c-addh">Add header</button>
      </div>
    </div>
    <div class="card" style="margin-top:16px"><div class="row"><button id="c-test">Test connection</button><span id="c-result" class="small"></span>
      <span class="spacer"></span><span class="muted small">Tests use the saved configuration — save first.</span></div></div>`;
  const bind = (sel, fn) => { const el = $(sel); if (el) el.oninput = el.onchange = () => { fn(el); markDirty(); }; };
  bind("#c-name", (el) => (i.name = el.value));
  bind("#c-desc", (el) => (i.description = el.value));
  bind("#c-url", (el) => (c.baseUrl = el.value.trim()));
  bind("#c-auth", (el) => { c.authType = el.value; showAuth(); });
  bind("#c-token", (el) => (c.token = el.value || "********"));
  bind("#c-user", (el) => (c.username = el.value));
  bind("#c-pass", (el) => (c.password = el.value || "********"));
  bind("#c-apiv", (el) => (c.apiVersion = el.value.trim()));
  bind("#c-timeout", (el) => (c.timeoutSeconds = Number(el.value)));
  bind("#c-delay", (el) => (c.requestDelayMs = Number(el.value)));
  bind("#c-tls", (el) => (c.verifyTls = el.checked));
  $$("[data-url]").forEach((b) => (b.onclick = () => { $("#c-url").value = b.dataset.url; c.baseUrl = b.dataset.url; markDirty(); }));
  const showAuth = () => {
    $$(".auth-bearer").forEach((el) => (el.hidden = c.authType !== "bearer"));
    $$(".auth-basic").forEach((el) => (el.hidden = c.authType !== "basic"));
  };
  showAuth();
  const drawHeaders = () => {
    const entries = Object.entries(c.headers || {});
    $("#c-headers").innerHTML = entries.map(([k, v], n) => `<div class="row" style="margin-bottom:6px">
      <input data-hk="${n}" value="${esc(k)}" placeholder="Header" style="flex:1"><input data-hv="${n}" value="${esc(v)}" placeholder="Value" style="flex:2">
      <button type="button" class="link" data-hdel="${n}">Remove</button></div>`).join("") || `<div class="muted small" style="margin-bottom:6px">None</div>`;
    const sync = () => { const out = {}; $$("[data-hk]").forEach((el) => { if (el.value.trim()) out[el.value.trim()] = $(`[data-hv="${el.dataset.hk}"]`).value; }); c.headers = out; markDirty(); };
    $$("[data-hk], [data-hv]").forEach((el) => (el.onchange = sync));
    $$("[data-hdel]").forEach((b) => (b.onclick = () => { const k = entries[b.dataset.hdel][0]; delete c.headers[k]; markDirty(); drawHeaders(); }));
  };
  drawHeaders();
  $("#c-addh").onclick = () => { c.headers = { ...(c.headers || {}), [`X-Header-${Object.keys(c.headers || {}).length + 1}`]: "" }; markDirty(); drawHeaders(); };
  $("#c-test").onclick = async () => {
    $("#c-result").textContent = "Testing…";
    const r = await guard(() => api(`/api/integrations/${enc(i.id)}/test`, { method: "POST" }));
    if (r && $("#c-result")) $("#c-result").innerHTML = r.ok ? `<span class="badge ok">connected · ${r.totalUsers ?? "?"} users · ${r.latencyMs} ms</span>`
      : `<span class="badge bad">${esc(r.httpStatus ? "HTTP " + r.httpStatus + ": " : "")}${esc(r.error)}</span>`;
  };
}

// ---- tab: users in scope
async function tabUsers(body, i) {
  const names = await groupNames();
  const s = i.scope;
  body.innerHTML = `
    <div class="grid cols-2" style="grid-template-columns: minmax(0,3fr) minmax(0,2fr)">
      <div class="card"><h2>Which users are provisioned?</h2>
        <div class="tiles">
          <label class="tile"><input type="radio" name="mode" value="group" ${s.mode === "group" ? "checked" : ""}><div><strong>Group membership</strong><div class="muted small">Members of an HR group (static or dynamic, nested groups flattened)</div></div></label>
          <label class="tile"><input type="radio" name="mode" value="query" ${s.mode === "query" ? "checked" : ""}><div><strong>Attribute filter</strong><div class="muted small">Users matching a query, e.g. department == "Sales"</div></div></label>
          <label class="tile"><input type="radio" name="mode" value="all" ${s.mode === "all" ? "checked" : ""}><div><strong>All users</strong><div class="muted small">Everyone in the HRIS</div></div></label>
        </div>
        <label class="field scope-group" style="margin-top:14px">Group<select id="s-group">${options(names, s.group)}</select></label>
        <label class="field scope-query" style="margin-top:14px">Query<textarea id="s-query" class="mono" rows="3" placeholder='memberOf("all-staff") and region == "EMEA"'>${esc(s.query || "")}</textarea></label>
        <div class="notice" style="margin-top:14px">Only users whose HR status is <strong>Active</strong> are provisioned. Users who become inactive or fall out of scope
          are handled by <a href="#/integrations/${enc(i.id)}/actions">provisioning actions</a> (currently: <strong>${esc(i.actions.deprovision)}</strong>).</div>
      </div>
      <div class="card"><h2>Preview</h2><div id="s-preview" class="muted">…</div></div>
    </div>`;
  const show = () => { $$(".scope-group").forEach((e) => (e.hidden = s.mode !== "group")); $$(".scope-query").forEach((e) => (e.hidden = s.mode !== "query")); };
  let t;
  const refresh = () => {
    clearTimeout(t);
    t = setTimeout(async () => {
      try {
        const r = await latest("scope", api(`/api/integrations/${enc(i.id)}/scope-preview`, { method: "POST", body: s }));
        if (!$("#s-preview")) return;
        $("#s-preview").innerHTML = `<div class="row" style="margin-bottom:10px"><span class="badge ok">${r.active} active</span>
          <span class="badge">${r.inactive} inactive (not provisioned)</span></div>
          ${r.sample.map((u) => `<div class="who" style="margin:6px 0">${avatarImg(u, 28)}<div>${userLink(u.email)}<span class="sub">${esc(u.title || "")}</span></div></div>`).join("")}
          ${r.active > r.sample.length ? `<div class="muted small">…and ${r.active - r.sample.length} more</div>` : ""}`;
      } catch (e) { if (!e.stale && $("#s-preview")) $("#s-preview").innerHTML = `<div class="notice bad">${esc(e.message)}</div>`; }
    }, 250);
  };
  $$("input[name=mode]").forEach((r) => (r.onchange = () => { s.mode = r.value; markDirty(); show(); refresh(); }));
  $("#s-group").onchange = (e) => { s.group = e.target.value; markDirty(); refresh(); };
  $("#s-query").oninput = (e) => { s.query = e.target.value; markDirty(); refresh(); };
  show(); refresh();
}

// ---- tab: attribute mapping
async function tabMapping(body, i) {
  const [m, emails, names] = await Promise.all([meta(), allEmails(), groupNames()]);
  const ref = integ.ref;
  const sources = [...Object.keys(ref.specialSources), ...Object.keys(ref.aliases), ...m.fields.filter((f) => !Object.values(ref.aliases).includes(f))];
  body.innerHTML = `
    <div class="grid map-layout" style="align-items:start">
      <div>
        <div class="card"><div class="row"><h2 style="margin:0">Attribute mapping</h2><span class="spacer"></span>
          <button id="m-add">Add mapping</button><button id="m-reset" class="link">Reset to template</button></div>
          <p class="muted small">Each row sets a SCIM attribute on the target from an HRIS source. Sources can be a field or alias (<code>email</code>, <code>work.title</code>),
          a template (<code>{firstName}.{surname}</code>), a quoted literal (<code>"Halcyon"</code>) or a computed value (${Object.keys(ref.specialSources).map((k) => `<code title="${esc(ref.specialSources[k])}">${esc(k)}</code>`).join(" ")}).</p>
          <div class="table-wrap"><table class="map-table"><colgroup><col class="c-target"><col class="c-arrow"><col class="c-source"><col class="c-tr"><col class="c-def"><col class="c-apply"><col class="c-del"></colgroup><thead><tr><th>Target attribute</th><th></th><th>Source</th><th>Transforms</th><th>Default</th><th>Apply on</th><th></th></tr></thead>
            <tbody id="m-rows"></tbody></table></div>
        </div>
        <div class="card"><div class="row"><h2 style="margin:0">Role rules</h2><span class="spacer"></span>
          <label class="switch"><input type="checkbox" id="r-on" ${i.roles.enabled ? "checked" : ""}><span></span> Enabled</label></div>
          <p class="muted small">The first matching group wins; the value is available to mappings as <code>$role</code>${i.roleValues ? ` (allowed: ${i.roleValues.map((v) => `<code>${esc(v)}</code>`).join(" ")})` : ""}.</p>
          <div id="r-rows"></div>
          <div class="row" style="margin-top:8px"><button id="r-add">Add rule</button><span class="spacer"></span>
            <label class="field" style="width:220px">Default value${i.roleValues ? `<select id="r-default">${options(i.roleValues, i.roles.defaultValue)}</select>` : `<input id="r-default" value="${esc(i.roles.defaultValue || "")}">`}</label></div>
        </div>
      </div>
      <div class="card sticky"><h2>Preview</h2>
        <div class="row"><input id="p-user" list="dl-emails" placeholder="Pick a person…" style="flex:1"><button id="p-go">Preview</button></div>
        <div class="muted small" style="margin-top:6px">Uses your unsaved edits.</div>
        <div id="p-out" style="margin-top:12px"></div>
      </div>
    </div>
    <datalist id="dl-emails">${emails.map((e) => `<option value="${esc(e.email)}">${esc(e.name)}</option>`).join("")}</datalist>
    <datalist id="dl-targets">${ref.targetAttributes.map((t) => `<option value="${esc(t)}">`).join("")}</datalist>
    <datalist id="dl-sources">${sources.map((s) => `<option value="${esc(s)}">`).join("")}</datalist>`;

  const required = new Set(i.required);
  const drawRows = () => {
    $("#m-rows").innerHTML = i.mappings.map((mp, n) => `<tr>
      <td><input class="mono" list="dl-targets" data-f="target" data-n="${n}" value="${esc(mp.target)}">${required.has(mp.target) ? `<span class="badge accent small-badge">required</span>` : ""}</td>
      <td class="muted">←</td>
      <td><input class="mono" list="dl-sources" data-f="source" data-n="${n}" value="${esc(mp.source || "")}"></td>
      <td><div class="chips">${(mp.transforms || []).map((t, k) => `<span class="chip">${esc(t)}<button data-tdel="${n}:${k}" aria-label="remove">×</button></span>`).join("")}
        <select data-tadd="${n}" class="tadd"><option value="">+</option>${ref.transforms.map((t) => `<option>${esc(t)}</option>`).join("")}</select></div></td>
      <td><input data-f="default" data-n="${n}" value="${esc(mp.default ?? "")}"></td>
      <td><select data-f="applyOn" data-n="${n}">${options(["always", "create"], mp.applyOn || "always")}</select></td>
      <td><button class="link danger-link" data-del="${n}" aria-label="Remove mapping" ${required.has(mp.target) ? "disabled title='required by this target'" : "title='Remove mapping'"}>✕</button></td></tr>`).join("");
    $$("#m-rows [data-f]").forEach((el) => (el.onchange = () => {
      const mp = i.mappings[el.dataset.n];
      mp[el.dataset.f] = el.dataset.f === "default" ? (el.value === "" ? null : el.value) : el.value.trim();
      markDirty(); if (el.dataset.f === "target") drawRows(); preview();
    }));
    $$("#m-rows [data-tadd]").forEach((el) => (el.onchange = () => { if (!el.value) return; const mp = i.mappings[el.dataset.tadd]; mp.transforms = [...(mp.transforms || []), el.value]; markDirty(); drawRows(); preview(); }));
    $$("#m-rows [data-tdel]").forEach((b) => (b.onclick = () => { const [n, k] = b.dataset.tdel.split(":").map(Number); i.mappings[n].transforms.splice(k, 1); markDirty(); drawRows(); preview(); }));
    $$("#m-rows [data-del]").forEach((b) => (b.onclick = () => { i.mappings.splice(Number(b.dataset.del), 1); markDirty(); drawRows(); preview(); }));
  };
  const drawRoles = () => {
    $("#r-rows").innerHTML = i.roles.rules.map((r, n) => `<div class="row" style="margin-bottom:6px">
      <span class="muted small">If member of</span><select data-rg="${n}" style="flex:1">${options(names, r.group, "— group —")}</select>
      <span class="muted small">then</span>${i.roleValues ? `<select data-rv="${n}">${options(i.roleValues, r.value)}</select>` : `<input data-rv="${n}" value="${esc(r.value)}">`}
      <button class="link" data-rdel="${n}">Remove</button></div>`).join("") || `<div class="muted small">No rules — everyone gets the default value.</div>`;
    $$("[data-rg]").forEach((el) => (el.onchange = () => { i.roles.rules[el.dataset.rg].group = el.value; markDirty(); preview(); }));
    $$("[data-rv]").forEach((el) => (el.onchange = () => { i.roles.rules[el.dataset.rv].value = el.value; markDirty(); preview(); }));
    $$("[data-rdel]").forEach((b) => (b.onclick = () => { i.roles.rules.splice(Number(b.dataset.rdel), 1); markDirty(); drawRoles(); preview(); }));
  };
  drawRows(); drawRoles();
  $("#m-add").onclick = () => { i.mappings.push({ target: "", source: "", transforms: [], default: null, applyOn: "always" }); markDirty(); drawRows(); $$("#m-rows tr:last-child input")[0]?.focus(); };
  $("#m-reset").onclick = () => { if (!confirm("Replace all mappings with the template defaults?")) return; i.mappings = structuredClone(ref.templates[i.type].defaults.mappings); markDirty(); drawRows(); preview(); };
  $("#r-on").onchange = (e) => { i.roles.enabled = e.target.checked; markDirty(); preview(); };
  $("#r-add").onclick = () => { i.roles.rules.push({ group: "", value: i.roleValues ? i.roleValues[0] : "" }); markDirty(); drawRoles(); };
  $("#r-default").onchange = (e) => { i.roles.defaultValue = e.target.value; markDirty(); preview(); };

  let t;
  const preview = () => {
    const email = $("#p-user").value.trim().toLowerCase();
    if (!email) return;
    storage("mockhris.preview.user", email);
    clearTimeout(t);
    t = setTimeout(async () => {
      try {
        const r = await latest("preview", api(`/api/integrations/${enc(i.id)}/preview`, { method: "POST", body: { email, draft: draftPayload() } }));
        if (!$("#p-out")) return;
        $("#p-out").innerHTML = `
          <div class="row" style="margin-bottom:10px">${r.inScope ? `<span class="badge ok">in scope</span>` : `<span class="badge warn">out of scope</span>`}
            ${r.active ? `<span class="badge ok">active</span>` : `<span class="badge bad">inactive</span>`}
            ${r.role ? `<span class="badge accent">role: ${esc(r.role)}</span>` : ""}<span class="badge info">next sync: ${esc(r.nextAction)}</span></div>
          <div class="table-wrap"><table class="small"><thead><tr><th>Target</th><th>Value</th></tr></thead><tbody>
            ${r.trace.map((x) => `<tr><td class="mono">${esc(x.target.replace("urn:ietf:params:scim:schemas:extension:enterprise:2.0:User:", "enterprise:"))}</td>
              <td>${x.value === null || x.value === undefined || x.value === "" ? `<span class="muted">— omitted</span>` : esc(JSON.stringify(x.value))}
              ${JSON.stringify(x.raw) !== JSON.stringify(x.value) && x.raw !== null && x.raw !== undefined ? `<span class="sub">from ${esc(JSON.stringify(x.raw))}</span>` : ""}</td></tr>`).join("")}
          </tbody></table></div>
          <details style="margin-top:10px" open><summary>SCIM payload</summary><pre class="code">${esc(JSON.stringify(r.payload, null, 2))}</pre></details>`;
      } catch (e) { if (!e.stale && $("#p-out")) $("#p-out").innerHTML = `<div class="notice bad">${esc(e.message)}</div>`; }
    }, 250);
  };
  $("#p-user").value = storage("mockhris.preview.user") || emails[0]?.email || "";
  $("#p-user").onchange = preview;
  $("#p-go").onclick = preview;
  preview();
}

// ---- tab: groups
async function tabGroups(body, i) {
  const g = i.groups;
  body.innerHTML = `
    <div class="grid cols-2" style="grid-template-columns: minmax(0,3fr) minmax(0,2fr)">
      <div class="card"><div class="row"><h2 style="margin:0">Group provisioning</h2><span class="spacer"></span>
        <label class="switch"><input type="checkbox" id="g-on" ${g.enabled ? "checked" : ""}><span></span> Push groups</label></div>
        <p class="muted small">Selected HR groups are created on the target with their resolved members (nested groups flattened, limited to provisioned active users).</p>
        <div class="form-grid">
          <label class="field">Include groups (glob patterns, one per line)<textarea id="g-pat" class="mono" rows="8">${esc(g.patterns.join("\n"))}</textarea></label>
          <label class="field">Exclude patterns<textarea id="g-exc" class="mono" rows="8">${esc((g.exclude || []).join("\n"))}</textarea></label>
        </div>
        <div class="row" style="margin-top:10px">
          <label class="field" style="width:220px">Target group name from<select id="g-name">${options(["displayName", "name"], g.nameSource)}</select></label>
          <label class="check" style="align-self:end"><input type="checkbox" id="g-skip" ${g.skipEmpty ? "checked" : ""}> Skip groups with no provisioned members</label>
        </div>
      </div>
      <div class="card"><h2>Groups that will be pushed</h2><div id="g-prev" class="muted">…</div></div>
    </div>`;
  const lines = (v) => v.split("\n").map((x) => x.trim()).filter(Boolean);
  let t;
  const refresh = () => {
    clearTimeout(t);
    t = setTimeout(async () => {
      try {
        const r = await latest("groups", api(`/api/integrations/${enc(i.id)}/groups-preview`, { method: "POST", body: { draft: draftPayload() } }));
        if (!$("#g-prev")) return;
        $("#g-prev").innerHTML = !g.enabled ? `<div class="muted">Group push is off.</div>` :
          `<div class="small muted" style="margin-bottom:6px">${r.length} group(s)</div><div class="list-rows" style="max-height:480px;overflow:auto">${r.map((x) =>
            `<div class="lr"><div>${groupLink(x.name)}<span class="sub">${esc(x.displayName)}</span></div><span class="badge">${x.members}</span></div>`).join("") || `<div class="empty">No groups match</div>`}</div>`;
      } catch (e) { if (!e.stale && $("#g-prev")) $("#g-prev").innerHTML = `<div class="notice bad">${esc(e.message)}</div>`; }
    }, 300);
  };
  $("#g-on").onchange = (e) => { g.enabled = e.target.checked; markDirty(); refresh(); };
  $("#g-pat").oninput = (e) => { g.patterns = lines(e.target.value); markDirty(); refresh(); };
  $("#g-exc").oninput = (e) => { g.exclude = lines(e.target.value); markDirty(); refresh(); };
  $("#g-name").onchange = (e) => { g.nameSource = e.target.value; markDirty(); refresh(); };
  $("#g-skip").onchange = (e) => { g.skipEmpty = e.target.checked; markDirty(); refresh(); };
  refresh();
}

// ---- tab: provisioning actions
async function tabActions(body, i) {
  const a = i.actions;
  const dep = [["disable", "Disable", "PATCH active=false (GitHub EMU: suspends the account). Re-enabled automatically if they return."],
    ["delete", "Delete", "DELETE the user on the target. Irreversible on most platforms."],
    ["none", "Do nothing", "Leave leavers untouched on the target (not recommended)."]];
  body.innerHTML = `
    <div class="grid cols-2">
      <div class="card"><h2>Allowed operations</h2>
        <label class="check"><input type="checkbox" id="a-create" ${a.create ? "checked" : ""}> Create users that are in scope but not yet on the target</label>
        <label class="check" style="margin-top:8px"><input type="checkbox" id="a-update" ${a.update ? "checked" : ""}> Update users when mapped attributes change</label>
        <h2 style="margin-top:20px">When a user leaves scope or becomes inactive</h2>
        <div class="tiles">${dep.map(([v, label, d]) => `<label class="tile"><input type="radio" name="dep" value="${v}" ${a.deprovision === v ? "checked" : ""}>
          <div><strong>${label}</strong><div class="muted small">${d}</div></div></label>`).join("")}</div>
      </div>
      <div class="card"><h2>Danger zone</h2>
        <p class="muted">Forgetting state makes the next sync re-create users and groups (existing ones are matched by userName / externalId and adopted).</p>
        <button id="a-forget" class="danger">Forget provisioning state</button>
        <p class="muted" style="margin-top:18px">Deleting the integration removes its configuration and state. Nothing is deleted on the target.</p>
        <button id="a-delete" class="danger">Delete integration</button>
      </div>
    </div>`;
  $("#a-create").onchange = (e) => { a.create = e.target.checked; markDirty(); };
  $("#a-update").onchange = (e) => { a.update = e.target.checked; markDirty(); };
  $$("input[name=dep]").forEach((r) => (r.onchange = () => { a.deprovision = r.value; markDirty(); }));
  $("#a-forget").onclick = async () => { if (confirm("Forget all provisioning state for this integration?")) await guard(() => api(`/api/integrations/${enc(i.id)}/state`, { method: "DELETE" }), "State cleared"); };
  $("#a-delete").onclick = async () => {
    if (!confirm(`Delete integration ${i.name}?`)) return;
    if (await guard(() => api(`/api/integrations/${enc(i.id)}`, { method: "DELETE" }), "Integration deleted")) { integ.id = null; location.hash = "#/integrations"; }
  };
}

// ---- tab: activity
async function tabActivity(body, i) {
  const [runs, state] = await Promise.all([api(`/api/integrations/${enc(i.id)}/runs?limit=25`), api(`/api/integrations/${enc(i.id)}/state`)]);
  body.innerHTML = `
    <div class="grid cols-2" style="grid-template-columns: minmax(0,2fr) minmax(0,3fr)">
      <div class="card"><h2>Sync history</h2><div class="table-wrap tall"><table>
        <thead><tr><th>Run</th><th>Status</th><th class="right">Ops</th><th class="right">Failed</th></tr></thead>
        <tbody>${runs.map((r) => `<tr class="clickable" data-run="${esc(r.id)}"><td class="mono small">${esc(r.id)}<span class="sub">${fmtDate(r.startedAt)}${r.full ? " · full" : ""}</span></td>
          <td>${runBadge(r.status)}</td><td class="right">${r.total}</td><td class="right">${r.failed || 0}</td></tr>`).join("") || `<tr><td colspan="4" class="empty">No runs yet</td></tr>`}</tbody></table></div></div>
      <div class="card"><div id="run-box"><h2>Provisioned identities</h2>
        <div class="row" style="margin-bottom:8px"><span class="badge ok">${state.users.filter((u) => u.active).length} active</span>
          <span class="badge warn">${state.users.filter((u) => !u.active).length} disabled</span><span class="badge">${state.groups.length} groups</span></div>
        <input id="st-filter" placeholder="Filter" style="width:100%; margin-bottom:8px">
        <div class="table-wrap tall"><table><thead><tr><th>User</th><th>Target id</th><th>Status</th><th>Last synced</th></tr></thead><tbody id="st-rows"></tbody></table></div></div></div>
    </div>`;
  const draw = (f = "") => {
    $("#st-rows").innerHTML = state.users.filter((u) => u.email.includes(f.toLowerCase())).slice(0, 500).map((u) => `<tr><td>${userLink(u.email)}</td>
      <td class="mono small">${esc(u.scimId)}</td><td>${u.active ? `<span class="badge ok">active</span>` : `<span class="badge warn">disabled</span>`}</td>
      <td class="small muted">${fmtDate(u.lastSyncedAt)}</td></tr>`).join("") || `<tr><td colspan="4" class="empty">Nothing provisioned yet</td></tr>`;
  };
  draw();
  $("#st-filter").oninput = (e) => draw(e.target.value);
  $$("tr[data-run]").forEach((tr) => (tr.onclick = () => watchRun($("#run-box"), tr.dataset.run)));
}

// ---- shared renderers: plan, run progress, verify
function drawPlan(box, p) {
  const counts = Object.entries(p.counts);
  box.innerHTML = `
    <dl class="kv small"><dt>Users in scope</dt><dd>${p.activeInScope} active of ${p.inScope}</dd><dt>Groups selected</dt><dd>${p.groupsSelected}</dd></dl>
    <div class="chips" style="margin:10px 0">${counts.length ? counts.map(([k, v]) => `<span class="chip op-${k}">${esc(k)} ${v}</span>`).join("") : `<span class="badge ok">In sync — nothing to do</span>`}</div>
    ${p.operations.length ? `<input id="op-filter" placeholder="Filter operations" style="width:100%; margin-bottom:8px">
    <div class="table-wrap tall"><table><thead><tr><th>Operation</th><th>Target</th><th>Detail</th></tr></thead><tbody id="op-rows"></tbody></table></div>` : ""}`;
  if (!p.operations.length) return;
  const draw = (f = "") => {
    $("#op-rows").innerHTML = p.operations.filter((o) => `${o.op} ${o.key} ${o.summary}`.toLowerCase().includes(f.toLowerCase())).slice(0, 500).map((o) => `
      <tr><td class="op-${o.op}">${esc(o.op)}</td><td>${o.kind === "user" ? userLink(o.key) : groupLink(o.key)}</td>
      <td>${esc(o.summary)}${o.added?.length || o.removed?.length ? `<details><summary>members</summary>
        ${o.added.map((e) => `<div class="op-create_user">+ ${esc(e)}</div>`).join("")}${o.removed.map((e) => `<div class="op-delete_user">− ${esc(e)}</div>`).join("")}</details>` : ""}</td></tr>`).join("");
  };
  draw();
  $("#op-filter").oninput = (e) => draw(e.target.value);
}

async function watchRun(box, id) {
  clearInterval(pollTimer);
  const draw = (r) => {
    const pct = r.total ? Math.round((r.done / r.total) * 100) : 100;
    const results = (r.results || []).slice().sort((a, b) => (a.status === "ok") - (b.status === "ok"));
    box.innerHTML = `
      <div class="row"><strong class="mono small">${esc(r.id)}</strong>${runBadge(r.status)}<span class="spacer"></span>
        <span class="muted small">${r.done}/${r.total} · ${r.succeeded} ok · ${r.failed} failed</span></div>
      <div class="bar" style="margin:10px 0"><span style="width:${pct}%"></span></div>
      ${r.error ? `<div class="notice bad">${esc(r.error)}</div>` : ""}
      ${r.status !== "running" && !r.total ? `<div class="notice ok">Already in sync — nothing to send.</div>` : ""}
      ${results.length ? `<div class="table-wrap tall"><table><thead><tr><th>Operation</th><th>Target</th><th>Result</th></tr></thead><tbody>
        ${results.slice(0, 500).map((x) => `<tr><td class="op-${x.op}">${esc(x.op)}</td><td>${x.op.endsWith("_user") ? userLink(x.key) : groupLink(x.key)}</td>
        <td>${x.status === "ok" ? `<span class="badge ok">ok</span> <span class="muted small">${esc(x.detail || x.summary || "")}${x.skippedMembers ? ` · skipped ${x.skippedMembers.length}` : ""}</span>`
          : `<span class="badge bad">${esc(x.httpStatus || "error")}</span> <span class="small">${esc(x.error)}</span>`}</td></tr>`).join("")}</tbody></table></div>` : ""}
      ${r.status !== "running" && r.total ? `<div class="row" style="margin-top:10px"><button id="after-verify">Verify target now</button></div>` : ""}`;
    const v = $("#after-verify", box);
    if (v) v.onclick = () => runAction("verify");
  };
  box.classList.remove("no-fade");
  let first = true;
  const tick = async () => {
    const r = await guard(() => api("/api/runs/" + enc(id)));
    if (!r) return clearInterval(pollTimer);
    draw(r);
    if (first) { first = false; setTimeout(() => box.classList.add("no-fade"), 300); }  // after the first fade completes
    if (r.status !== "running") clearInterval(pollTimer);
  };
  await tick();
  pollTimer = setInterval(tick, 1000);
}

function drawVerify(box, v) {
  if (v.error) { box.innerHTML = `<div class="notice bad">Could not read the target: ${esc(v.error)}</div>`; return; }
  const u = v.users, g = v.groups;
  const section = (title, items, fn) => items.length ? `<details open><summary><strong>${esc(title)}</strong> <span class="badge bad">${items.length}</span></summary>
    <div class="list-rows" style="margin:6px 0 12px">${items.slice(0, 100).map(fn).join("")}</div></details>` : "";
  box.innerHTML = `
    <div class="notice ${v.ok ? "ok" : "bad"}"><strong>${v.ok ? "Verified — the target matches the HRIS." : "Drift detected — the target does not match the HRIS."}</strong>
      <span class="small">Checked ${fmtDate(v.checkedAt)} in ${v.durationMs} ms</span></div>
    <div class="grid cols-4" style="margin:12px 0">
      <div class="stat"><div class="label">Expected active</div><div class="value">${u.expectedActive}</div></div>
      <div class="stat"><div class="label">Matched exactly</div><div class="value">${u.matched}</div></div>
      <div class="stat"><div class="label">Active on target</div><div class="value">${u.remoteActive}</div></div>
      <div class="stat"><div class="label">Groups matched</div><div class="value">${g.matched}/${g.expected}</div></div>
    </div>
    <div class="muted small" style="margin-bottom:8px">Attributes compared: ${v.fieldsCompared.map((f) => `<code>${esc(f.replace("urn:ietf:params:scim:schemas:extension:enterprise:2.0:User:", "enterprise:"))}</code>`).join(" ")}</div>
    ${section("Missing on target", u.missing, (x) => `<div class="lr">${userLink(x.email)}</div>`)}
    ${section("Attribute mismatches", u.mismatched, (x) => `<div>${userLink(x.email)}<table class="small" style="margin:4px 0 8px"><tbody>${x.differences.map((d) =>
      `<tr><td class="mono">${esc(d.attribute.replace("urn:ietf:params:scim:schemas:extension:enterprise:2.0:User:", "enterprise:"))}</td><td>expected <code>${esc(JSON.stringify(d.expected))}</code></td><td>actual <code>${esc(JSON.stringify(d.actual))}</code></td></tr>`).join("")}</tbody></table></div>`)}
    ${section("Should be disabled but still active", u.wronglyActive, (x) => `<div class="lr">${userLink(x.email)}</div>`)}
    ${section("Unexpected active users on target", u.unexpected, (x) => `<div class="lr"><span class="mono">${esc(x.userName)}</span><span class="muted small">${esc(x.id)}</span></div>`)}
    ${section("Groups missing on target", g.missing, (x) => `<div class="lr">${groupLink(x)}</div>`)}
    ${section("Group membership differences", g.memberDiffs, (x) => `<div>${groupLink(x.group)} <span class="muted small">−${x.missingCount} missing, +${x.extraCount} extra</span>
      <div class="small">${x.missing.map((e) => `<div class="op-delete_user">missing ${esc(e)}</div>`).join("")}${x.extra.map((e) => `<div class="op-update_user">extra ${esc(e)}</div>`).join("")}</div></div>`)}`;
}

// --------------------------------------------------------------- mock github
async function renderMock() {
  const snap = await api("/api/mock-scim");
  const active = snap.users.filter((u) => u.active).length;
  main().innerHTML = `
    <div class="page-head"><div><h1>Mock GitHub target</h1><div class="sub">What the built-in GitHub EMU SCIM mock currently holds. Logins follow EMU's <span class="mono">handle_shortcode</span> format.</div></div>
      <button id="mock-reset" class="danger">Reset mock</button></div>
    <div class="grid cols-4">
      <div class="card stat"><div class="label">Users</div><div class="value">${snap.users.length}</div></div>
      <div class="card stat"><div class="label">Active</div><div class="value">${active}</div></div>
      <div class="card stat"><div class="label">Suspended</div><div class="value">${snap.users.length - active}</div></div>
      <div class="card stat"><div class="label">Groups</div><div class="value">${snap.groups.length}</div></div>
    </div>
    <div class="tabs" style="margin-top:16px"><button class="active" data-tab="mu">Users</button><button data-tab="mg">Groups</button>
      <span class="spacer"></span><input id="mock-filter" placeholder="Filter" style="margin-bottom:6px"></div>
    <section data-panel="mu"><div class="table-wrap tall"><table><thead><tr><th>Login</th><th>userName</th><th>Status</th><th>Roles</th><th>externalId</th><th>Modified</th></tr></thead><tbody id="mu-rows"></tbody></table></div></section>
    <section data-panel="mg" hidden><div class="table-wrap tall"><table><thead><tr><th>Group</th><th class="right">Members</th><th>Modified</th></tr></thead><tbody id="mg-rows"></tbody></table></div></section>`;
  const draw = (f = "") => {
    f = f.toLowerCase();
    $("#mu-rows").innerHTML = snap.users.filter((u) => `${u.login} ${u.userName} ${u.displayName}`.toLowerCase().includes(f)).map((u) => `
      <tr><td class="mono">${esc(u.login)}</td><td>${esc(u.userName)}<span class="sub">${esc(u.displayName)}</span></td>
      <td>${u.active ? `<span class="badge ok">active</span>` : `<span class="badge warn">suspended</span>`}</td>
      <td>${u.roles.map((r) => `<span class="badge ${r === "user" ? "" : "accent"}">${esc(r)}</span>`).join(" ")}</td>
      <td class="mono small">${esc(u.externalId)}</td><td class="small muted">${fmtDate(u.lastModified)}</td></tr>`).join("") || `<tr><td colspan="6" class="empty">No users provisioned yet — run a sync</td></tr>`;
    $("#mg-rows").innerHTML = snap.groups.filter((g) => g.displayName.toLowerCase().includes(f)).map((g) => `
      <tr><td><details><summary><strong>${esc(g.displayName)}</strong> <span class="mono small">${esc(g.externalId)}</span></summary>
        <div class="small" style="margin-top:6px">${g.members.map(esc).join("<br>") || "—"}</div></details></td>
      <td class="right">${g.members.length}</td><td class="small muted">${fmtDate(g.lastModified)}</td></tr>`).join("") || `<tr><td colspan="3" class="empty">No groups</td></tr>`;
  };
  draw();
  $("#mock-filter").oninput = (e) => draw(e.target.value);
  $$(".tabs button").forEach((b) => (b.onclick = () => {
    $$(".tabs button").forEach((x) => x.classList.toggle("active", x === b));
    $$("section[data-panel]").forEach((s) => (s.hidden = s.dataset.panel !== b.dataset.tab));
  }));
  $("#mock-reset").onclick = async () => {
    if (!confirm("Delete everything in the mock GitHub target? Also forget the GitHub EMU integration's provisioning state so the next sync starts fresh?")) return;
    await guard(() => api("/api/mock-scim", { method: "DELETE" }));
    if (await guard(() => api("/api/integrations/github-emu/state", { method: "DELETE" }), "Mock target and state reset")) renderMock();
  };
}

// -------------------------------------------------------------------- admin
async function renderAdmin() {
  const origin = location.origin;
  main().innerHTML = `
    <div class="page-head"><div><h1>Admin &amp; API</h1><div class="sub">Data management, REST API and MCP server</div></div></div>
    <div class="grid cols-2">
      <div class="card"><h2>Synthetic data</h2>
        <p class="muted">Regenerate Halcyon Systems Group: ~440 people across 13 legal entities, 3 subsidiaries and 150+ groups. <strong>Wipes all users and groups.</strong></p>
        <div class="row"><label class="field" style="width:160px">Seed<input id="seed" type="number" value="20261114"></label>
          <button id="reseed" class="danger" style="align-self:end">Regenerate</button></div>
        <h2 style="margin-top:22px">Backup</h2>
        <div class="row"><button id="export">Export JSON</button>
          <label class="btn">Import JSON<input id="import" type="file" accept="application/json" hidden></label>
          <label class="check"><input type="checkbox" id="merge"> Merge instead of replace</label></div>
        <h2 style="margin-top:22px">Validate groups</h2>
        <button id="validate">Resolve every group</button><div id="val" style="margin-top:10px"></div>
      </div>
      <div class="card"><h2>REST API</h2>
        <p class="muted">OpenAPI docs: <a href="/docs" target="_blank" rel="noopener">${esc(origin)}/docs</a></p>
        <pre class="code">curl ${esc(origin)}/api/users?query=team%20%3D%3D%20%22Payments%22
curl -X POST ${esc(origin)}/api/query -H 'Content-Type: application/json' \\
  -d '{"query": "memberOf(\\"engineering-org\\") and region == \\"EMEA\\""}'
curl -X POST ${esc(origin)}/api/integrations/github-emu/plan
curl -X POST ${esc(origin)}/api/integrations/scim-target/verify</pre>
        <h2 style="margin-top:22px">MCP server</h2>
        <p class="muted">Streamable HTTP endpoint at <span class="mono">${esc(origin)}/mcp</span>. Add it to Claude Code:</p>
        <pre class="code">claude mcp add --transport http mockhris ${esc(origin)}/mcp</pre>
        <p class="muted" style="margin-top:12px">Or run over stdio (DynamoDB must be reachable from the host):</p>
        <pre class="code">{
  "mcpServers": {
    "mockhris": {
      "command": "python",
      "args": ["-m", "mockhris", "mcp"],
      "env": { "DYNAMODB_ENDPOINT": "http://localhost:8001" }
    }
  }
}</pre>
        <p class="muted small">If <span class="mono">MOCKHRIS_API_KEY</span> is set, send it as <span class="mono">X-API-Key</span> or a Bearer token.</p>
      </div>
    </div>`;
  $("#reseed").onclick = async () => {
    if (!confirm("Wipe all users and groups and regenerate the company?")) return;
    const r = await guard(() => api("/api/admin/seed", { method: "POST", body: { seed: Number($("#seed").value) } }));
    if (r) { invalidateCaches(); toast(`Generated ${r.users} people and ${r.groups} groups`); }
  };
  $("#export").onclick = async () => {
    const data = await guard(() => api("/api/admin/export"));
    if (!data) return;
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
    a.download = `mockhris-export-${new Date().toISOString().slice(0, 10)}.json`;
    a.click(); URL.revokeObjectURL(a.href);
  };
  $("#import").onchange = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    let bundle;
    try { bundle = JSON.parse(await file.text()); } catch (err) { return toast("Invalid JSON: " + err.message, true); }
    const merge = $("#merge").checked;
    if (!merge && !confirm(`Replace all data with ${bundle.users?.length ?? 0} users and ${bundle.groups?.length ?? 0} groups?`)) return;
    const r = await guard(() => api("/api/admin/import?replace=" + !merge, { method: "POST", body: bundle }));
    if (r) { invalidateCaches(); toast(`Imported ${r.users} users and ${r.groups} groups`); }
  };
  $("#validate").onclick = async () => {
    const r = await guard(() => api("/api/groups-validate"));
    if (!r || !$("#val")) return;
    const bad = r.filter((x) => !x.ok);
    $("#val").innerHTML = bad.length
      ? bad.map((x) => `<div class="notice bad" style="margin-bottom:6px">${groupLink(x.name)}: ${esc(x.error)}</div>`).join("")
      : `<div class="notice ok">All ${r.length} groups resolve.</div>`;
  };
}
