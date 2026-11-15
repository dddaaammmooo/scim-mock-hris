"""MCP server exposing MockHRIS (users, groups, queries, SCIM provisioning).

Two transports:
  * Streamable HTTP, mounted in the web app at http://localhost:8000/mcp
  * stdio:  python -m mockhris.mcp_server   (needs DynamoDB reachable, e.g. DYNAMODB_ENDPOINT=http://localhost:8001)
"""
import functools
from typing import Any, Optional

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from . import mock_scim, services
from .hr import ServiceError
from .query import QueryError

INSTRUCTIONS = """\
MockHRIS is a mock HR system (HiBob-style records) for Halcyon Systems Group, a fictitious
software conglomerate, used to test SCIM provisioning of GitHub Enterprise Managed Users.

Users are keyed by email. Groups have static `members` and/or a dynamic `query`; resolved
membership is the union. Query language examples:
  team == "Payments"
  department == "Platform Engineering" and status == "Active"
  region in ["EMEA", "APAC"] and not employmentType == "Contractor"
  memberOf("engineering-org") and jobLevel in ["IC4", "IC5"]
  title matches "^principal " / manager == "x@halcyon.example" / internal.terminationDate exists
Call describe_schema for the full field/alias list and org structure. Use evaluate_query to
test a query before saving it on a group.

Provisioning is configured as integrations (list_integrations): each has a SCIM connection, a user
scope, attribute mappings (target <- source + transforms) and optional group push. Use
preview_integration_user to check a mapping, plan_integration as a safe dry run, sync_integration
to push, and verify_integration to reconcile the target against the HRIS end to end."""

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)
EXTERNAL = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True)

mcp = MCPServer("MockHRIS", instructions=INSTRUCTIONS, version="1.0.0")


def tool(annotations):
    """Register an MCP tool; expected domain errors reach the model as readable tool errors."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except (ServiceError, QueryError) as e:
                raise ToolError(str(e)) from e
        return mcp.tool(annotations=annotations)(wrapper)
    return deco


def _strip_none(d):
    return {k: v for k, v in d.items() if v is not None}


# ------------------------------------------------------------------------ schema
@tool(READ)
def describe_schema() -> dict:
    """Field catalogue, query aliases/operators, org structure (division -> department -> teams), sites and enums."""
    return services.hr().meta()


@tool(READ)
def hris_stats() -> dict:
    """Headcount by lifecycle, employment type, region and division."""
    return services.hr().stats()


# ------------------------------------------------------------------------- users
@tool(READ)
def search_users(search: Optional[str] = None, query: Optional[str] = None, division: Optional[str] = None,
                 department: Optional[str] = None, team: Optional[str] = None, site: Optional[str] = None,
                 region: Optional[str] = None, lifecycle: Optional[str] = None,
                 employment_type: Optional[str] = None, limit: int = 50, offset: int = 0) -> dict:
    """Find users. `search` is free text (name/email/title/team/employee id); `query` uses the group query
    language; the other arguments are exact-match filters. Returns summaries and the total match count."""
    filters = dict(division=division, department=department, team=team, site=site, region=region,
                   lifecycle=lifecycle, employmentType=employment_type)
    return services.hr().list_users(search=search, query=query, filters=filters, limit=limit, offset=offset)


@tool(READ)
def get_user(email: str) -> dict:
    """Full HiBob-style record for one user."""
    return services.hr().get_user(email)


@tool(READ)
def get_user_groups(email: str) -> list:
    """Every group the user resolves into (static or via query)."""
    return services.hr().user_groups(email)


@tool(WRITE)
def create_user(email: str, first_name: str, surname: str, title: Optional[str] = None,
                division: Optional[str] = None, department: Optional[str] = None, team: Optional[str] = None,
                site: Optional[str] = None, manager_email: Optional[str] = None, job_level: Optional[str] = None,
                employment_type: str = "Permanent", start_date: Optional[str] = None,
                lifecycle: str = "employed", extra: Optional[dict[str, Any]] = None) -> dict:
    """Create a user. Region, legal entity, address and site id are derived from `site` when it is a known
    site (see describe_schema). `extra` is deep-merged into the record for any other HiBob fields."""
    data = {
        "email": email, "firstName": first_name, "surname": surname,
        "work": _strip_none({"title": title, "department": department, "site": site, "startDate": start_date,
                             "reportsTo": manager_email,
                             "custom": _strip_none({"division": division, "team": team, "jobLevel": job_level})}),
        "payroll": {"employment": {"type": employment_type}},
        "internal": {"lifecycleStatus": lifecycle},
    }
    if extra:
        from .hr import deep_merge
        data = deep_merge(data, extra)
    return services.hr().create_user(data)


@tool(WRITE)
def update_user(email: str, patch: dict[str, Any]) -> dict:
    """Deep-merge `patch` into a user record, e.g. {"work": {"title": "Staff Engineer", "custom": {"team": "Mobile"}}}.
    Set a manager with {"work": {"reportsTo": "manager@halcyon.example"}}. Change lifecycle via
    {"internal": {"lifecycleStatus": "leave"}}; status is derived from lifecycle."""
    return services.hr().update_user(email, patch)


@tool(DESTRUCTIVE)
def terminate_user(email: str, termination_date: Optional[str] = None, reason: str = "Resignation") -> dict:
    """Mark a user as terminated (status becomes Inactive; the next SCIM sync suspends them)."""
    return services.hr().terminate_user(email, termination_date, reason)


@tool(WRITE)
def rename_user(email: str, new_email: str) -> dict:
    """Change a user's email (their primary key). Updates group memberships, manager references and SCIM state."""
    return services.hr().rename_user(email, new_email)


@tool(DESTRUCTIVE)
def delete_user(email: str) -> dict:
    """Permanently delete a user record. Fails if they still have direct reports."""
    return services.hr().delete_user(email)


@tool(READ)
def org_chart(root_email: Optional[str] = None, depth: int = 2, include_terminated: bool = False) -> Any:
    """Reporting tree from `root_email` (default: the CEO), `depth` levels deep."""
    return services.hr().org_chart(root_email, include_terminated, depth)


# ------------------------------------------------------------------------ groups
@tool(READ)
def list_groups(search: Optional[str] = None) -> list:
    """All groups with type (static/dynamic/mixed), query and resolved member count."""
    return services.hr().list_groups(search=search)


@tool(READ)
def get_group(name: str, include_members: bool = True) -> dict:
    """One group: definition, groups it references, groups that use it, and resolved members."""
    g = services.hr().get_group(name, resolve=True)
    if not include_members:
        g.pop("resolvedMembers", None)
    return g


@tool(READ)
def evaluate_query(query: str, limit: int = 100) -> dict:
    """Evaluate a group query and return the match count and matching emails (up to `limit`)."""
    return services.hr().evaluate_query(query, limit)


@tool(WRITE)
def create_group(name: str, display_name: Optional[str] = None, description: str = "",
                 members: Optional[list[str]] = None, query: Optional[str] = None) -> dict:
    """Create a group. `name` is a lowercase slug. Provide static `members` (emails), a `query`, or both.
    Use memberOf("other-group") in the query to nest groups."""
    return services.hr().create_group({"name": name, "displayName": display_name, "description": description,
                                       "members": members or [], "query": query})


@tool(WRITE)
def update_group(name: str, display_name: Optional[str] = None, description: Optional[str] = None,
                 members: Optional[list[str]] = None, query: Optional[str] = None) -> dict:
    """Update a group. Omitted arguments are unchanged; pass query="" to remove the query and members=[] to
    clear static members."""
    patch = _strip_none({"displayName": display_name, "description": description, "members": members,
                         "query": query})
    return services.hr().update_group(name, patch)


@tool(WRITE)
def add_group_members(name: str, emails: list[str]) -> dict:
    """Add static members to a group."""
    return services.hr().add_group_members(name, emails)


@tool(WRITE)
def remove_group_members(name: str, emails: list[str]) -> dict:
    """Remove static members from a group (users matched by its query remain members)."""
    return services.hr().remove_group_members(name, emails)


@tool(DESTRUCTIVE)
def delete_group(name: str) -> dict:
    """Delete a group. Fails if other groups or the SCIM config reference it."""
    return services.hr().delete_group(name)


# ------------------------------------------------------------------ integrations
@tool(READ)
def list_integrations() -> list:
    """Outbound provisioning integrations (connection, scope, mappings) with their last run."""
    return services.integrations().list()


@tool(READ)
def integration_reference() -> dict:
    """Templates (github-emu, scim2), target SCIM attributes, special sources ($active, $role,
    $managerScimId, $managerEmail, $fullName), aliases and transforms for building attribute mappings."""
    from .integrations import reference
    return reference()


@tool(READ)
def get_integration(integration_id: str) -> dict:
    """Full configuration of one integration (secrets masked)."""
    return services.integrations().get(integration_id)


@tool(WRITE)
def create_integration(integration_type: str, integration_id: str, name: Optional[str] = None,
                       base_url: Optional[str] = None, token: Optional[str] = None) -> dict:
    """Create an integration from a template: integration_type is 'github-emu' or 'scim2'.
    Defaults (scope, attribute mappings, group push) come from the template; adjust with update_integration."""
    conn = _strip_none({"baseUrl": base_url, "token": token})
    return services.integrations().create({"type": integration_type, "id": integration_id, "name": name,
                                           "connection": conn or None})


@tool(WRITE)
def update_integration(integration_id: str, patch: dict[str, Any]) -> dict:
    """Update an integration. Top-level sections: name, description, enabled, connection {baseUrl, authType,
    token, username, password, headers, apiVersion, verifyTls, timeoutSeconds, requestDelayMs},
    scope {mode: group|query|all, group, query}, mappings [{target, source, transforms[], default, applyOn}]
    (replaces the whole list), roles {enabled, rules [{group, value}], defaultValue}, groups {enabled,
    patterns[], exclude[], skipEmpty, nameSource}, actions {create, update, deprovision: disable|delete|none}.
    Sources: a field/alias (e.g. email, work.title), a template "{firstName}.{surname}", a quoted literal,
    or a special ($active, $role, ...)."""
    return services.integrations().update(integration_id, patch)


@tool(DESTRUCTIVE)
def delete_integration(integration_id: str) -> dict:
    """Delete an integration and its provisioning state (does not touch the target)."""
    return services.integrations().delete(integration_id)


@tool(READ)
def test_integration(integration_id: str) -> dict:
    """Check the target is reachable and accepts the credentials."""
    return services.engine(integration_id).test_connection()


@tool(READ)
def preview_integration_user(integration_id: str, email: str) -> dict:
    """Show how one user maps: in scope?, role, next action, per-attribute source -> value trace, SCIM payload."""
    return services.engine(integration_id).preview_user(email)


@tool(READ)
def plan_integration(integration_id: str, full: bool = False, max_operations: int = 100) -> dict:
    """Dry run: the create/update/disable/delete operations a sync would perform (nothing is sent)."""
    plan = services.engine(integration_id).plan(full=full)
    ops = plan.pop("operations")
    plan["operations"] = [{k: v for k, v in o.items() if k not in ("payload", "group")} for o in ops[:max_operations]]
    plan["operationsTruncated"] = max(0, len(ops) - max_operations)
    return plan


@tool(EXTERNAL)
def sync_integration(integration_id: str, full: bool = False, wait: bool = True) -> dict:
    """Execute a sync against the integration's target. wait=False runs in the background (poll get_sync_run).
    full=True re-sends every user and group even if unchanged."""
    run = services.runner().start(integration_id, full=full, background=not wait)
    results = run.get("results") or []
    summary = {k: v for k, v in run.items() if k != "results"}
    summary["failures"] = [r for r in results if r.get("status") != "ok"][:25]
    return summary


@tool(READ)
def verify_integration(integration_id: str) -> dict:
    """End-to-end check: read users and groups back from the target over SCIM and reconcile them against
    the HRIS (missing, mismatched attributes, wrongly active, unexpected users, group membership diffs)."""
    return services.engine(integration_id).verify()


@tool(READ)
def list_sync_runs(integration_id: Optional[str] = None, limit: int = 10) -> list:
    """Recent sync runs, newest first, optionally for one integration."""
    return services.runner().runs(integration_id, limit)


@tool(READ)
def get_sync_run(run_id: str) -> dict:
    """One sync run including per-operation results."""
    return services.runner().get_run(run_id)


@tool(READ)
def mock_github_snapshot(include_users: bool = False) -> dict:
    """What the built-in mock GitHub SCIM target holds: users (login, active, roles) and groups with members."""
    snap = mock_scim.snapshot()
    out = {"userCount": len(snap["users"]), "activeUsers": sum(1 for u in snap["users"] if u["active"]),
           "groupCount": len(snap["groups"]),
           "groups": [{"displayName": g["displayName"], "members": len(g["members"])} for g in snap["groups"]]}
    if include_users:
        out["users"] = snap["users"]
    return out


def main():
    from .store import get_store
    get_store().ensure_tables()
    mcp.run("stdio")


if __name__ == "__main__":
    main()
