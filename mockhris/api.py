"""REST API (mounted at /api). Interactive docs at /docs."""
from typing import Any, Optional

from fastapi import APIRouter, Body, Query
from pydantic import BaseModel, Field

from . import mock_scim, services
from .store import get_store

router = APIRouter(prefix="/api")


# ----------------------------------------------------------------------- models
class GroupIn(BaseModel):
    name: str = Field(..., examples=["team-payments-oncall"])
    displayName: Optional[str] = None
    description: Optional[str] = ""
    members: list[str] = Field(default_factory=list, description="Static member emails")
    query: Optional[str] = Field(None, examples=['team == "Payments" and status == "Active"'])


class GroupPatch(BaseModel):
    displayName: Optional[str] = None
    description: Optional[str] = None
    members: Optional[list[str]] = None
    query: Optional[str] = None


class MembersIn(BaseModel):
    emails: list[str]


class QueryIn(BaseModel):
    query: str = Field(..., examples=['memberOf("engineering-org") and region == "EMEA"'])
    limit: Optional[int] = None


class TerminateIn(BaseModel):
    terminationDate: Optional[str] = Field(None, description="YYYY-MM-DD, defaults to today")
    reason: str = "Resignation"


class RenameIn(BaseModel):
    newEmail: str


class SeedIn(BaseModel):
    seed: int = 20261114


class SyncIn(BaseModel):
    full: bool = Field(False, description="Push every user/group even if unchanged since the last sync")


# ----------------------------------------------------------------------- health
@router.get("/health", tags=["system"])
def health():
    get_store().ping()
    return {"ok": True}


@router.get("/stats", tags=["system"])
def stats():
    s = services.hr().stats()
    runs = services.runner().runs(limit=1)
    s["lastSync"] = runs[0] if runs else None
    s["integrations"] = len(services.integrations().list())
    return s


@router.get("/meta", tags=["system"], summary="Field catalogue, aliases, org structure and enums")
def meta():
    return services.hr().meta()


# ------------------------------------------------------------------------ users
@router.get("/users", tags=["users"])
def list_users(search: Optional[str] = None, query: Optional[str] = Query(None, description="Group query DSL"),
               division: Optional[str] = None, department: Optional[str] = None, team: Optional[str] = None,
               site: Optional[str] = None, region: Optional[str] = None, status: Optional[str] = None,
               lifecycle: Optional[str] = None, employmentType: Optional[str] = None,
               limit: Optional[int] = Query(None, ge=1, le=5000), offset: int = Query(0, ge=0), full: bool = False):
    filters = dict(division=division, department=department, team=team, site=site, region=region,
                   status=status, lifecycle=lifecycle, employmentType=employmentType)
    return services.hr().list_users(search=search, query=query, filters=filters, limit=limit, offset=offset, full=full)


@router.post("/users", tags=["users"], status_code=201)
def create_user(user: dict[str, Any] = Body(..., examples=[{
        "email": "new.starter@halcyon.example", "firstName": "New", "surname": "Starter",
        "work": {"title": "Software Engineer", "department": "Platform Engineering", "site": "Sydney",
                 "reportsTo": "someone@halcyon.example",
                 "custom": {"division": "Engineering", "team": "Observability", "jobLevel": "IC2"}}}])):
    return services.hr().create_user(user)


@router.get("/users/{email}", tags=["users"])
def get_user(email: str):
    return services.hr().get_user(email)


@router.patch("/users/{email}", tags=["users"], summary="Deep-merge a partial record")
def update_user(email: str, patch: dict[str, Any] = Body(...)):
    return services.hr().update_user(email, patch)


@router.put("/users/{email}", tags=["users"], summary="Replace the record (derived fields are recomputed)")
def replace_user(email: str, user: dict[str, Any] = Body(...)):
    return services.hr().update_user(email, user)


@router.delete("/users/{email}", tags=["users"])
def delete_user(email: str):
    return services.hr().delete_user(email)


@router.post("/users/{email}/terminate", tags=["users"])
def terminate_user(email: str, body: TerminateIn = Body(default_factory=TerminateIn)):
    return services.hr().terminate_user(email, body.terminationDate, body.reason)


@router.post("/users/{email}/rename", tags=["users"], summary="Change a user's email (primary key)")
def rename_user(email: str, body: RenameIn):
    return services.hr().rename_user(email, body.newEmail)


@router.get("/users/{email}/groups", tags=["users"], summary="Groups the user resolves into")
def user_groups(email: str):
    return services.hr().user_groups(email)


@router.get("/org-chart", tags=["users"])
def org_chart(root: Optional[str] = None, includeTerminated: bool = False, depth: Optional[int] = None):
    return services.hr().org_chart(root, includeTerminated, depth)


# ----------------------------------------------------------------------- groups
@router.get("/groups", tags=["groups"])
def list_groups(search: Optional[str] = None):
    return services.hr().list_groups(search=search)


@router.post("/groups", tags=["groups"], status_code=201)
def create_group(group: GroupIn):
    return services.hr().create_group(group.model_dump())


@router.get("/groups/{name}", tags=["groups"])
def get_group(name: str, resolve: bool = True):
    return services.hr().get_group(name, resolve)


@router.patch("/groups/{name}", tags=["groups"])
def update_group(name: str, patch: GroupPatch):
    return services.hr().update_group(name, patch.model_dump(exclude_unset=True))


@router.delete("/groups/{name}", tags=["groups"])
def delete_group(name: str):
    return services.hr().delete_group(name)


@router.post("/groups/{name}/members", tags=["groups"], summary="Add static members")
def add_members(name: str, body: MembersIn):
    return services.hr().add_group_members(name, body.emails)


@router.post("/groups/{name}/members/remove", tags=["groups"], summary="Remove static members")
def remove_members(name: str, body: MembersIn):
    return services.hr().remove_group_members(name, body.emails)


@router.post("/query", tags=["groups"], summary="Evaluate a query and return matching emails")
def evaluate_query(body: QueryIn):
    return services.hr().evaluate_query(body.query, body.limit)


@router.get("/groups-validate", tags=["groups"], summary="Resolve every group and report errors")
def validate_groups():
    return services.hr().validate_groups()


# ----------------------------------------------------------------- integrations
class IntegrationIn(BaseModel):
    type: str = Field(..., examples=["scim2"], description="github-emu or scim2")
    id: str = Field(..., examples=["slack"])
    name: Optional[str] = None
    description: Optional[str] = None
    connection: Optional[dict[str, Any]] = None


class PreviewIn(BaseModel):
    email: str
    draft: Optional[dict[str, Any]] = Field(None, description="Unsaved integration edits to preview with")


class DraftIn(BaseModel):
    draft: Optional[dict[str, Any]] = None


class ScopeIn(BaseModel):
    mode: str = "group"
    group: Optional[str] = None
    query: Optional[str] = None


@router.get("/integrations/reference", tags=["integrations"],
            summary="Templates, target attributes, source expressions and transforms for the mapping editor")
def integration_reference():
    from .integrations import reference
    return reference()


@router.get("/integrations", tags=["integrations"])
def list_integrations():
    return services.integrations().list()


@router.post("/integrations", tags=["integrations"], status_code=201, summary="Create from a template")
def create_integration(body: IntegrationIn):
    return services.integrations().create(body.model_dump(exclude_none=True))


@router.get("/integrations/{iid}", tags=["integrations"])
def get_integration(iid: str):
    return services.integrations().get(iid)


@router.put("/integrations/{iid}", tags=["integrations"],
            summary="Update (sections are merged; secrets sent as ******** are kept)")
def update_integration(iid: str, body: dict[str, Any] = Body(...)):
    return services.integrations().update(iid, body)


@router.delete("/integrations/{iid}", tags=["integrations"])
def delete_integration(iid: str):
    return services.integrations().delete(iid)


@router.post("/integrations/{iid}/reset-mappings", tags=["integrations"])
def reset_mappings(iid: str):
    return services.integrations().reset_mappings(iid)


@router.post("/integrations/{iid}/test", tags=["integrations"])
def test_integration(iid: str):
    return services.engine(iid).test_connection()


@router.post("/integrations/{iid}/preview", tags=["integrations"],
             summary="Map one user with the saved configuration and show the SCIM payload")
def preview_user(iid: str, body: PreviewIn):
    return services.engine(iid, draft=body.draft).preview_user(body.email)


@router.post("/integrations/{iid}/scope-preview", tags=["integrations"], summary="Count users for a scope rule")
def scope_preview(iid: str, body: ScopeIn):
    return services.engine(iid).scope_preview(body.model_dump())


@router.post("/integrations/{iid}/groups-preview", tags=["integrations"], summary="Groups that would be pushed")
def groups_preview(iid: str, body: DraftIn = Body(default_factory=DraftIn)):
    return services.engine(iid, draft=body.draft).groups_preview()


@router.post("/integrations/{iid}/plan", tags=["integrations"], summary="Dry run")
def plan(iid: str, body: SyncIn = Body(default_factory=SyncIn)):
    return services.engine(iid).plan(full=body.full)


@router.post("/integrations/{iid}/sync", tags=["integrations"], status_code=202, summary="Start a sync")
def sync(iid: str, body: SyncIn = Body(default_factory=SyncIn)):
    return services.runner().start(iid, full=body.full)


@router.post("/integrations/{iid}/verify", tags=["integrations"],
             summary="Read users/groups back from the target and reconcile against the HRIS")
def verify(iid: str):
    return services.engine(iid).verify()


@router.get("/integrations/{iid}/runs", tags=["integrations"])
def integration_runs(iid: str, limit: int = 20):
    return services.runner().runs(iid, limit)


@router.get("/integrations/{iid}/state", tags=["integrations"], summary="Last-synced state per user/group")
def state(iid: str):
    users, groups = services.engine(iid).state()
    strip = lambda d: {k: v for k, v in d.items() if k != "payload"}
    return {"users": [strip(v) | {"email": k} for k, v in sorted(users.items())],
            "groups": [strip(v) | {"name": k, "members": len(v.get("members", []))}
                       for k, v in sorted(groups.items())]}


@router.delete("/integrations/{iid}/state", tags=["integrations"],
               summary="Forget provisioning state (next sync re-creates or adopts)")
def reset_state(iid: str):
    return services.engine(iid).reset_state()


@router.get("/runs", tags=["integrations"], summary="Recent runs across all integrations")
def all_runs(limit: int = 30):
    return services.runner().runs(None, limit)


@router.get("/runs/{run_id}", tags=["integrations"])
def run(run_id: str):
    return services.runner().get_run(run_id)


@router.get("/mock-scim", tags=["mock GitHub SCIM"], summary="What the mock GitHub target currently holds")
def mock_snapshot():
    return mock_scim.snapshot()


@router.delete("/mock-scim", tags=["mock GitHub SCIM"])
def mock_reset():
    return mock_scim.reset()


# ------------------------------------------------------------------------ admin
@router.post("/admin/seed", tags=["admin"], summary="Wipe users/groups and regenerate the synthetic company")
def seed(body: SeedIn = Body(default_factory=SeedIn)):
    return services.hr().seed(body.seed)


@router.get("/admin/export", tags=["admin"])
def export():
    return services.hr().export()


@router.post("/admin/import", tags=["admin"])
def import_bundle(bundle: dict[str, Any] = Body(...), replace: bool = True):
    return services.hr().import_bundle(bundle, replace)
