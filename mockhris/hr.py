"""HR domain service: users, groups, queries, org structure, seeding.

Shared by the REST API, the web GUI (via the API), the MCP server and the CLI.
"""
import copy
import re
from datetime import date, datetime, timezone

from . import generator, settings
from .query import ALIASES, QueryError, Resolver, get_path, parse, referenced_groups

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
GROUP_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}$")
LIFECYCLES = ["hired", "employed", "leave", "terminated"]
ACTIVE_LIFECYCLES = {"employed", "leave"}
EMPLOYMENT_TYPES = ["Permanent", "Fixed Term", "Contractor", "Intern"]
CONTRACTS = ["Full time", "Part time", "Fixed term"]
JOB_LEVELS = ["E1", "E2", "E3", "M5", "M4", "M3", "M2", "IC5", "IC4", "IC3", "IC2", "IC1"]


class ServiceError(Exception):
    status = 400


class NotFound(ServiceError):
    status = 404


class Conflict(ServiceError):
    status = 409


class Invalid(ServiceError):
    status = 422


def deep_merge(base, patch):
    """Recursively merge patch into a copy of base. None values in patch overwrite."""
    out = copy.deepcopy(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k != "reportsTo":
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def avatar_url(user_id):
    """HiBob stores an absolute avatar URL; ours points at the locally generated SVG."""
    return f"{settings.get().public_base_url.rstrip('/')}/avatars/{user_id}.svg"


def user_summary(u):
    w, c, i = u.get("work", {}), u.get("work", {}).get("custom", {}), u.get("internal", {})
    return {
        "email": u["email"],
        "id": u.get("id"),
        "displayName": u.get("displayName"),
        "title": w.get("title"),
        "division": c.get("division"),
        "department": w.get("department"),
        "team": c.get("team"),
        "site": w.get("site"),
        "region": c.get("region"),
        "legalEntity": c.get("legalEntity"),
        "jobLevel": c.get("jobLevel"),
        "manager": (w.get("reportsTo") or {}).get("email"),
        "isManager": w.get("isManager", False),
        "status": i.get("status"),
        "lifecycle": i.get("lifecycleStatus"),
        "employmentType": u.get("payroll", {}).get("employment", {}).get("type"),
        "startDate": w.get("startDate"),
        "avatar": (u.get("about") or {}).get("avatar") or avatar_url(u.get("id")),
    }


class HRService:
    def __init__(self, store):
        self.store = store

    # ------------------------------------------------------------------ loading
    def users_map(self):
        return {u["email"]: u for u in self.store.scan("users")}

    def groups_map(self):
        return {g["name"]: g for g in self.store.scan("groups")}

    def resolver(self, users=None, groups=None):
        return Resolver(users if users is not None else self.users_map(),
                        groups if groups is not None else self.groups_map())

    # -------------------------------------------------------------------- users
    def list_users(self, *, search=None, query=None, filters=None, limit=None, offset=0, full=False):
        users = self.users_map()
        selected = list(users.values())
        if query:
            r = self.resolver(users)
            try:
                matches = r.run_query(r.compile(query, "query"))
            except QueryError as e:
                raise Invalid(str(e))
            selected = [u for u in selected if u["email"] in matches]
        for field, value in (filters or {}).items():
            if value in (None, ""):
                continue
            path = ALIASES.get(field, field)
            selected = [u for u in selected if str(get_path(u, path)).casefold() == str(value).casefold()]
        if search:
            s = search.casefold()
            selected = [u for u in selected if any(
                s in str(v).casefold() for v in (u["email"], u.get("displayName"), u.get("work", {}).get("title"),
                                                  u.get("work", {}).get("custom", {}).get("team"),
                                                  u.get("work", {}).get("employeeIdInCompany")))]
        selected.sort(key=lambda u: (u.get("displayName") or "").casefold())
        total = len(selected)
        page = selected[offset: offset + limit if limit else None]
        return {"total": total, "items": page if full else [user_summary(u) for u in page]}

    def get_user(self, email):
        u = self.store.get("users", email.lower())
        if not u:
            raise NotFound(f"user {email!r} not found")
        return u

    def user_groups(self, email):
        email = email.lower()
        users = self.users_map()
        if email not in users:
            raise NotFound(f"user {email!r} not found")
        groups = self.groups_map()
        r = self.resolver(users, groups)
        out = []
        for name in sorted(groups):
            if email in r.resolve(name):
                g = groups[name]
                out.append({"name": name, "displayName": g.get("displayName"),
                            "static": email in (g.get("members") or [])})
        return out

    def create_user(self, data):
        data = copy.deepcopy(data)
        email = str(data.get("email", "")).strip().lower()
        if not EMAIL_RE.match(email):
            raise Invalid("a valid 'email' is required")
        if not data.get("firstName") or not data.get("surname"):
            raise Invalid("'firstName' and 'surname' are required")
        users = self.users_map()
        if email in users:
            raise Conflict(f"user {email!r} already exists")
        data["email"] = email
        user = self._normalize(self._skeleton(data, users), users, previous=None, patch=data)
        users[email] = user
        self.store.put("users", user, must_not_exist=True)
        self._recompute_hierarchy(users, changed={email})
        return self.store.get("users", email)

    def update_user(self, email, patch):
        email = email.lower()
        users = self.users_map()
        if email not in users:
            raise NotFound(f"user {email!r} not found")
        patch = copy.deepcopy(patch)
        new_email = patch.pop("email", email)
        if new_email and str(new_email).lower() != email:
            raise Invalid("email is the primary key; use the rename operation to change it")
        for key in ("id", "companyId", "creationDateTime"):
            patch.pop(key, None)
        previous = users[email]
        merged = deep_merge(previous, patch)
        user = self._normalize(merged, users, previous=previous, patch=patch)
        users[email] = user
        self.store.put("users", user)
        self._recompute_hierarchy(users, changed={email})
        return self.store.get("users", email)

    def rename_user(self, email, new_email):
        email, new_email = email.lower(), str(new_email).strip().lower()
        if not EMAIL_RE.match(new_email):
            raise Invalid("invalid new email")
        users = self.users_map()
        if email not in users:
            raise NotFound(f"user {email!r} not found")
        if new_email in users:
            raise Conflict(f"user {new_email!r} already exists")
        user = users.pop(email)
        user["email"] = new_email
        user.setdefault("personal", {}).setdefault("communication", {})["slackUsername"] = new_email.split("@")[0]
        users[new_email] = user
        for u in users.values():
            mgr = u.get("work", {}).get("reportsTo")
            if mgr and mgr.get("email") == email:
                mgr["email"] = new_email
        self.store.put("users", user)
        self.store.delete("users", email)
        for g in self.store.scan("groups"):
            members = g.get("members") or []
            if email in members:
                g["members"] = sorted(new_email if m == email else m for m in members)
                self.store.put("groups", g)
        state = self.store.get("scim-state", f"user#{email}")
        if state:
            state["key"] = f"user#{new_email}"
            self.store.put("scim-state", state)
            self.store.delete("scim-state", f"user#{email}")
        self._recompute_hierarchy(users, changed=set(users), force_write=True)
        return self.store.get("users", new_email)

    def terminate_user(self, email, termination_date=None, reason="Resignation"):
        termination_date = termination_date or date.today().isoformat()
        try:
            date.fromisoformat(termination_date)
        except ValueError:
            raise Invalid("terminationDate must be YYYY-MM-DD")
        return self.update_user(email, {"internal": {
            "lifecycleStatus": "terminated", "terminationDate": termination_date, "terminationReason": reason}})

    def delete_user(self, email):
        email = email.lower()
        users = self.users_map()
        if email not in users:
            raise NotFound(f"user {email!r} not found")
        reports = [u["email"] for u in users.values()
                   if (u.get("work", {}).get("reportsTo") or {}).get("email") == email]
        if reports:
            raise Conflict(f"{email} still manages {len(reports)} people; reassign them first: "
                           + ", ".join(sorted(reports)[:10]))
        self.store.delete("users", email)
        for g in self.store.scan("groups"):
            if email in (g.get("members") or []):
                g["members"] = [m for m in g["members"] if m != email]
                self.store.put("groups", g)
        del users[email]
        self._recompute_hierarchy(users, changed=set())
        return {"deleted": email}

    # -- user normalisation ----------------------------------------------------
    def _skeleton(self, data, users):
        ids = [int(u["id"]) for u in users.values() if str(u.get("id", "")).isdigit()]
        today = date.today().isoformat()
        nums = [int(m.group(1)) for u in users.values()
                if (m := re.match(r"HSG-(\d+)$", str(u.get("work", {}).get("employeeIdInCompany", ""))))]
        base = {
            "id": str((max(ids) if ids else 3332883884017713000) + 7919),
            "companyId": generator.COMPANY_ID,
            "email": data["email"],
            "firstName": data["firstName"],
            "surname": data["surname"],
            "creationDateTime": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f"),
            "personal": {"honorific": None, "preferredName": None, "gender": None, "pronouns": None,
                         "shortBirthDate": None, "nationality": [],
                         "communication": {"slackUsername": data["email"].split("@")[0], "skypeUsername": None}},
            "about": {"avatar": None, "hobbies": [], "about": None,
                      "socialData": {"linkedin": None, "twitter": None, "facebook": None}},
            "work": {"employeeIdInCompany": f"HSG-{(max(nums) + 1) if nums else 10001}", "title": None,
                     "department": None, "site": None, "siteId": None, "startDate": today, "isManager": False,
                     "reportsTo": None, "secondLevelManager": None, "directReports": [], "tenureYears": 0,
                     "workPhone": None,
                     "custom": {"division": None, "team": None, "jobLevel": None, "costCentre": None,
                                "legalEntity": None, "region": None}},
            "payroll": {"employment": {"type": "Permanent", "contract": "Full time", "weeklyHours": 40,
                                       "legalEntity": None}},
            "address": {"country": None, "state": None, "city": None, "timezone": None},
            "internal": {"status": "Active", "lifecycleStatus": "employed", "terminationDate": None,
                         "terminationReason": None, "notice": None},
        }
        return deep_merge(base, data)

    def _normalize(self, u, users, previous, patch=None):
        patch = patch or {}
        u["email"] = u["email"].lower()
        if not u.get("firstName") or not u.get("surname"):
            raise Invalid("'firstName' and 'surname' are required")
        u["fullName"] = f"{u['firstName']} {u['surname']}"
        about = u.setdefault("about", {})
        if not about.get("avatar"):
            about["avatar"] = avatar_url(u["id"])
        name_changed = previous and (previous["firstName"], previous["surname"]) != (u["firstName"], u["surname"])
        if not u.get("displayName") or (name_changed and "displayName" not in patch):
            u["displayName"] = u["fullName"]
        work = u.setdefault("work", {})
        custom = work.setdefault("custom", {})
        internal = u.setdefault("internal", {})
        employment = u.setdefault("payroll", {}).setdefault("employment", {})

        # site -> address / region / entity
        site = work.get("site")
        site_changed = previous is None or site != previous.get("work", {}).get("site")
        if site and site in generator.SITES and site_changed:
            cc, state, city, tz = generator.SITES[site]
            country, region, entity = generator.COUNTRIES[cc]
            work["siteId"] = generator.SITE_IDS[site]
            if "address" not in patch:
                u["address"] = {"country": country, "state": state, "city": city, "timezone": tz}
            pc = patch.get("work", {}).get("custom", {})
            if "region" not in pc or not custom.get("region"):
                custom["region"] = region
            division = custom.get("division")
            if division in generator.SUBSIDIARIES:
                entity = generator.SUBSIDIARIES[division][0]
            if "legalEntity" not in pc or not custom.get("legalEntity"):
                custom["legalEntity"] = entity
        if custom.get("legalEntity"):
            employment["legalEntity"] = custom["legalEntity"]

        # lifecycle drives status
        lifecycle = internal.get("lifecycleStatus") or "employed"
        if lifecycle not in LIFECYCLES:
            raise Invalid(f"lifecycleStatus must be one of {LIFECYCLES}")
        internal["lifecycleStatus"] = lifecycle
        internal["status"] = "Active" if lifecycle in ACTIVE_LIFECYCLES else "Inactive"
        if lifecycle != "terminated":
            internal["terminationDate"] = None
            internal["terminationReason"] = None
        elif not internal.get("terminationDate"):
            internal["terminationDate"] = date.today().isoformat()
        if employment.get("type") and employment["type"] not in EMPLOYMENT_TYPES:
            raise Invalid(f"employment type must be one of {EMPLOYMENT_TYPES}")
        start = work.get("startDate")
        if start:
            try:
                d = date.fromisoformat(start)
            except ValueError:
                raise Invalid("work.startDate must be YYYY-MM-DD")
            work["tenureYears"] = max(0, (date.today() - d).days // 365)

        # manager reference (accepts an email string or {"email": ...})
        mgr = work.get("reportsTo")
        if isinstance(mgr, str):
            mgr = {"email": mgr}
        if mgr and mgr.get("email"):
            m_email = mgr["email"].lower()
            if m_email == u["email"]:
                raise Invalid("a user cannot report to themselves")
            if m_email not in users:
                raise Invalid(f"manager {m_email!r} does not exist")
            seen, cur = {u["email"]}, m_email
            while cur:
                if cur in seen:
                    raise Invalid("manager assignment would create a reporting cycle")
                seen.add(cur)
                cur = ((users.get(cur) or {}).get("work", {}).get("reportsTo") or {}).get("email")
            m = users[m_email]
            work["reportsTo"] = {"id": m["id"], "displayName": m.get("displayName"), "email": m_email}
        else:
            work["reportsTo"] = None
        return u

    def _recompute_hierarchy(self, users, changed, force_write=False):
        """Refresh derived manager fields across all users; persist the ones that changed."""
        reports = {}
        for u in users.values():
            mgr = (u.get("work", {}).get("reportsTo") or {}).get("email")
            if mgr and u.get("internal", {}).get("lifecycleStatus") != "terminated":
                reports.setdefault(mgr, []).append(u["email"])
        dirty = []
        for email, u in users.items():
            w = u.setdefault("work", {})
            before = (w.get("directReports"), w.get("isManager"), w.get("secondLevelManager"),
                      copy.deepcopy(w.get("reportsTo")))
            direct = sorted(reports.get(email, []))
            w["directReports"] = direct
            w["isManager"] = bool(direct)
            mgr_ref = w.get("reportsTo")
            if mgr_ref and mgr_ref.get("email") in users:
                m = users[mgr_ref["email"]]
                w["reportsTo"] = {"id": m["id"], "displayName": m.get("displayName"), "email": m["email"]}
                second = (m.get("work", {}).get("reportsTo") or {}).get("email")
                w["secondLevelManager"] = users[second]["id"] if second in users else None
            else:
                w["reportsTo"] = None
                w["secondLevelManager"] = None
            after = (w["directReports"], w["isManager"], w["secondLevelManager"], w["reportsTo"])
            if force_write or before != after or email in changed:
                dirty.append(u)
        if dirty:
            self.store.put_many("users", dirty)

    # ------------------------------------------------------------------- groups
    def list_groups(self, *, search=None, with_counts=True):
        users, groups = self.users_map(), self.groups_map()
        r = self.resolver(users, groups)
        out = []
        for name in sorted(groups):
            g = groups[name]
            if search and search.casefold() not in f"{name} {g.get('displayName', '')} {g.get('description', '')}".casefold():
                continue
            item = {
                "name": name,
                "displayName": g.get("displayName"),
                "description": g.get("description"),
                "type": self._group_type(g),
                "staticCount": len(g.get("members") or []),
                "query": g.get("query"),
            }
            if with_counts:
                try:
                    item["memberCount"] = len(r.resolve(name))
                except QueryError as e:
                    item["memberCount"], item["error"] = None, str(e)
            out.append(item)
        return out

    @staticmethod
    def _group_type(g):
        has_static, has_query = bool(g.get("members")), bool(g.get("query"))
        return "mixed" if has_static and has_query else "dynamic" if has_query else "static"

    def get_group(self, name, resolve=True):
        users, groups = self.users_map(), self.groups_map()
        if name not in groups:
            raise NotFound(f"group {name!r} not found")
        g = dict(groups[name])
        g.setdefault("members", [])
        g["type"] = self._group_type(g)
        g["usedBy"] = self._used_by(name, groups)
        if resolve:
            r = self.resolver(users, groups)
            try:
                members = sorted(r.resolve(name))
            except QueryError as e:
                raise Invalid(str(e))
            g["resolvedMembers"] = members
            g["memberCount"] = len(members)
            if g.get("query"):
                g["references"] = sorted(referenced_groups(parse(g["query"])))
        return g

    def _used_by(self, name, groups):
        used = []
        for other in groups.values():
            q = other.get("query")
            if other["name"] != name and q:
                try:
                    if name in referenced_groups(parse(q)):
                        used.append(other["name"])
                except QueryError:
                    pass
        from .integrations import Integrations  # local import: integrations depends on hr
        used.extend(Integrations(self.store, self).group_references(name))
        return sorted(used)

    def _validate_group(self, g, users, groups):
        name = g.get("name", "")
        if not GROUP_NAME_RE.match(name):
            raise Invalid("group name must be lowercase letters, digits, '.', '_' or '-' (max 100)")
        members = sorted({str(m).strip().lower() for m in (g.get("members") or []) if str(m).strip()})
        missing = [m for m in members if m not in users]
        if missing:
            raise Invalid(f"unknown member(s): {', '.join(missing[:10])}")
        query = (g.get("query") or "").strip() or None
        clean = {
            "name": name,
            "displayName": (g.get("displayName") or name).strip(),
            "description": (g.get("description") or "").strip(),
            "members": members,
        }
        if query:
            clean["query"] = query
        candidate = dict(groups)
        candidate[name] = clean
        r = Resolver(users, candidate)
        try:
            for n in candidate:  # resolves everything: catches cycles introduced by this change
                r.resolve(n)
        except QueryError as e:
            raise Invalid(str(e))
        return clean, len(r.resolve(name))

    def create_group(self, data):
        users, groups = self.users_map(), self.groups_map()
        if data.get("name") in groups:
            raise Conflict(f"group {data['name']!r} already exists")
        clean, _ = self._validate_group(data, users, groups)
        self.store.put("groups", clean, must_not_exist=True)
        return self.get_group(clean["name"])

    def update_group(self, name, patch):
        users, groups = self.users_map(), self.groups_map()
        if name not in groups:
            raise NotFound(f"group {name!r} not found")
        if patch.get("name", name) != name:
            raise Invalid("group name is the primary key and cannot be changed")
        merged = {**groups[name], **{k: v for k, v in patch.items()
                                     if k in ("displayName", "description", "members", "query")}}
        clean, _ = self._validate_group(merged, users, groups)
        self.store.put("groups", clean)
        return self.get_group(name)

    def add_group_members(self, name, emails):
        g = self.get_group(name, resolve=False)
        return self.update_group(name, {"members": sorted(set(g.get("members") or []) | {e.lower() for e in emails})})

    def remove_group_members(self, name, emails):
        g = self.get_group(name, resolve=False)
        drop = {e.lower() for e in emails}
        return self.update_group(name, {"members": [m for m in g.get("members") or [] if m not in drop]})

    def delete_group(self, name):
        groups = self.groups_map()
        if name not in groups:
            raise NotFound(f"group {name!r} not found")
        used = self._used_by(name, groups)
        if used:
            raise Conflict(f"group {name!r} is referenced by: {', '.join(used)}")
        self.store.delete("groups", name)
        return {"deleted": name}

    def evaluate_query(self, query, limit=None):
        r = self.resolver()
        try:
            emails = sorted(r.run_query(r.compile(query, "query")))
        except QueryError as e:
            raise Invalid(str(e))
        return {"query": query, "count": len(emails), "emails": emails[:limit] if limit else emails}

    def validate_groups(self):
        users, groups = self.users_map(), self.groups_map()
        r = self.resolver(users, groups)
        out = []
        for name in sorted(groups):
            try:
                out.append({"name": name, "ok": True, "memberCount": len(r.resolve(name))})
            except QueryError as e:
                out.append({"name": name, "ok": False, "error": str(e)})
        return out

    # --------------------------------------------------------------------- meta
    def meta(self):
        users = self.users_map()
        org, sites = {}, set()
        fields = set()
        r = Resolver(users, {})
        fields = sorted(r.known_paths)
        for u in users.values():
            c = u.get("work", {}).get("custom", {})
            div, dept, team = c.get("division"), u.get("work", {}).get("department"), c.get("team")
            if div and dept:
                org.setdefault(div, {}).setdefault(dept, set())
                if team:
                    org[div][dept].add(team)
            if u.get("work", {}).get("site"):
                sites.add(u["work"]["site"])
        distinct = lambda path: sorted({str(v) for u in users.values() if (v := get_path(u, path)) not in (None, "")})
        return {
            "aliases": ALIASES,
            "fields": fields,
            "org": {d: {dep: sorted(t) for dep, t in sorted(depts.items())} for d, depts in sorted(org.items())},
            "sites": sorted(set(generator.SITES) | sites),
            "regions": distinct("work.custom.region"),
            "legalEntities": distinct("work.custom.legalEntity"),
            "lifecycles": LIFECYCLES,
            "employmentTypes": EMPLOYMENT_TYPES,
            "contracts": CONTRACTS,
            "jobLevels": JOB_LEVELS,
            "operators": ["==", "!=", "<", "<=", ">", ">=", "in", "contains", "startsWith", "endsWith",
                          "matches", "exists", "memberOf(\"group\")", "and", "or", "not"],
        }

    def org_chart(self, root=None, include_terminated=False, depth=None):
        users = self.users_map()
        children = {}
        roots = []
        for u in users.values():
            if not include_terminated and u.get("internal", {}).get("lifecycleStatus") == "terminated":
                continue
            mgr = (u.get("work", {}).get("reportsTo") or {}).get("email")
            (children.setdefault(mgr, []) if mgr in users else roots).append(u)

        def node(u, d):
            kids = sorted(children.get(u["email"], []), key=lambda x: (not x["work"].get("isManager"), x["displayName"]))
            s = user_summary(u)
            n = {k: s[k] for k in ("email", "id", "displayName", "title", "division", "team", "site", "status",
                                   "lifecycle", "jobLevel", "avatar")}
            n["reportCount"] = len(kids)
            if depth is None or d < depth:
                n["reports"] = [node(k, d + 1) for k in kids]
            return n

        if root:
            if root.lower() not in users:
                raise NotFound(f"user {root!r} not found")
            return node(users[root.lower()], 0)
        return [node(u, 0) for u in roots]

    def stats(self):
        users = list(self.users_map().values())
        count = lambda f: dict(sorted(_counter(f(u) for u in users).items()))
        return {
            "users": len(users),
            "groups": self.store.count("groups"),
            "byLifecycle": count(lambda u: u.get("internal", {}).get("lifecycleStatus")),
            "byEmploymentType": count(lambda u: u.get("payroll", {}).get("employment", {}).get("type")),
            "byRegion": count(lambda u: u.get("work", {}).get("custom", {}).get("region")),
            "byDivision": count(lambda u: u.get("work", {}).get("custom", {}).get("division")),
        }

    def user_by_id(self, user_id):
        return self.store.find("users", lambda u: str(u.get("id")) == str(user_id))

    def backfill_avatars(self):
        """Give records created before avatars existed their generated avatar URL."""
        missing = [u for u in self.store.scan("users") if not (u.get("about") or {}).get("avatar")]
        for u in missing:
            u.setdefault("about", {})["avatar"] = avatar_url(u["id"])
        if missing:
            self.store.put_many("users", missing)
        return len(missing)

    # -------------------------------------------------------------------- admin
    def seed(self, seed=generator.DEFAULT_SEED):
        users, groups = generator.generate_dataset(seed)
        for u in users:
            u["about"]["avatar"] = avatar_url(u["id"])
        self.store.clear("users")
        self.store.clear("groups")
        self.store.put_many("users", users)
        self.store.put_many("groups", groups)
        return {"users": len(users), "groups": len(groups), "seed": seed}

    def export(self):
        return {
            "exportedAt": datetime.now(timezone.utc).isoformat(),
            "users": sorted(self.store.scan("users"), key=lambda u: u["email"]),
            "groups": sorted(self.store.scan("groups"), key=lambda g: g["name"]),
        }

    def import_bundle(self, bundle, replace=True):
        users = {u["email"].lower(): {**u, "email": u["email"].lower()} for u in bundle.get("users", [])}
        for u in users.values():
            about = u.setdefault("about", {})
            if not about.get("avatar"):
                about["avatar"] = avatar_url(u.get("id"))
        groups = {g["name"]: g for g in bundle.get("groups", [])}
        if not replace:
            users = {**self.users_map(), **users}
            groups = {**self.groups_map(), **groups}
        clean_groups = []
        for g in groups.values():
            clean, _ = self._validate_group(g, users, groups)
            clean_groups.append(clean)
        if replace:
            self.store.clear("users")
            self.store.clear("groups")
        self.store.put_many("users", list(users.values()))
        self.store.put_many("groups", clean_groups)
        self._recompute_hierarchy(users, changed=set())
        return {"users": len(users), "groups": len(clean_groups)}


def _counter(values):
    out = {}
    for v in values:
        out[str(v)] = out.get(str(v), 0) + 1
    return out
