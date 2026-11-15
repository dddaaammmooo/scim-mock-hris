"""A small mock of GitHub's EMU SCIM API, persisted in DynamoDB.

Mounted at /mock-github/scim/v2/enterprises/{enterprise}. It enforces the parts of
GitHub's behaviour that matter for testing a provisioner: bearer auth, required
attributes, userName/externalId uniqueness (409), group members referencing
provisioned user ids, `eq` filters, pagination, PATCH semantics, and EMU login
generation (<normalised userName>_<enterprise shortcode>).
"""
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from . import settings
from .store import get_store

LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"
USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
SCIM_JSON = "application/scim+json"
ROLES = {"user", "enterprise_owner", "billing_manager", "guest_collaborator"}

router = APIRouter(prefix="/mock-github/scim/v2/enterprises/{enterprise}", tags=["mock GitHub SCIM"])


class ScimHTTPError(Exception):
    def __init__(self, status, detail, scim_type=None):
        self.status, self.detail, self.scim_type = status, detail, scim_type


def scim_error_response(exc: ScimHTTPError):
    body = {"schemas": [ERROR_SCHEMA], "status": str(exc.status), "detail": exc.detail}
    if exc.scim_type:
        body["scimType"] = exc.scim_type
    return JSONResponse(body, status_code=exc.status, media_type=SCIM_JSON)


def ok(body, status=200):
    return JSONResponse(body, status_code=status, media_type=SCIM_JSON)


def require_token(request: Request):
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer ") or auth[7:].strip() != settings.get().mock_scim_token:
        raise ScimHTTPError(401, "Requires authentication")


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def emu_login(user_name, enterprise):
    """GitHub EMU: handle = normalised userName (local part) + '_' + enterprise shortcode."""
    local = user_name.split("@")[0]
    handle = re.sub(r"[^A-Za-z0-9]+", "-", local).strip("-")
    return f"{handle}_{enterprise}"


# --------------------------------------------------------------------------- #
# persistence helpers
# --------------------------------------------------------------------------- #
def _all(rtype, enterprise):
    return [i for i in get_store().scan("mock-scim") if i["resourceType"] == rtype and i["enterprise"] == enterprise]


def _get(rtype, enterprise, rid):
    item = get_store().get("mock-scim", rid)
    if not item or item["resourceType"] != rtype or item["enterprise"] != enterprise:
        raise ScimHTTPError(404, "Resource not found")
    return item


def _save(item):
    item["meta"]["lastModified"] = now()
    get_store().put("mock-scim", item)


def _render(item, request, enterprise):
    body = dict(item["data"])
    body["id"] = item["id"]
    rtype = item["resourceType"]
    body["meta"] = {**item["meta"], "resourceType": rtype,
                    "location": str(request.base_url).rstrip("/") +
                    f"/mock-github/scim/v2/enterprises/{enterprise}/{rtype}s/{item['id']}"}
    return body


def _filter(items, flt):
    if not flt:
        return items
    m = re.fullmatch(r'\s*(\w+)\s+eq\s+"(.*)"\s*', flt)
    if not m:
        raise ScimHTTPError(400, f"Unsupported filter: {flt}", "invalidFilter")
    attr, value = m.groups()
    if attr == "id":
        return [i for i in items if i["id"] == value]
    if attr not in ("userName", "externalId", "displayName"):
        raise ScimHTTPError(400, f"Filtering on {attr} is not supported", "invalidFilter")
    return [i for i in items if str(i["data"].get(attr, "")).casefold() == value.casefold()]


def _list(rtype, request, enterprise, flt, start, count, excluded=None):
    items = sorted(_filter(_all(rtype, enterprise), flt), key=lambda i: i["meta"]["created"] + i["id"])
    start = max(1, start)
    page = items[start - 1: start - 1 + max(0, count)]
    resources = [_render(i, request, enterprise) for i in page]
    if excluded == "members":
        for r in resources:
            r.pop("members", None)
    return ok({"schemas": [LIST_SCHEMA], "totalResults": len(items), "itemsPerPage": len(resources),
               "startIndex": start, "Resources": resources})


# --------------------------------------------------------------------------- #
# Users
# --------------------------------------------------------------------------- #
def _validate_user(body, enterprise, exclude_id=None):
    for field in ("userName", "externalId", "displayName", "emails"):
        if not body.get(field):
            raise ScimHTTPError(400, f"Missing required attribute: {field}", "invalidValue")
    if "active" not in body:
        raise ScimHTTPError(400, "Missing required attribute: active", "invalidValue")
    for e in body["emails"]:
        if not all(k in e for k in ("value", "type", "primary")):
            raise ScimHTTPError(400, "emails entries require value, type and primary", "invalidValue")
    for r in body.get("roles") or []:
        if r.get("value") not in ROLES and not re.fullmatch(r"[0-9a-f-]{36}", str(r.get("value"))):
            raise ScimHTTPError(400, f"Invalid role: {r.get('value')}", "invalidValue")
    for other in _all("User", enterprise):
        if other["id"] == exclude_id:
            continue
        if other["data"]["userName"].casefold() == body["userName"].casefold():
            raise ScimHTTPError(409, "User with userName already exists", "uniqueness")
        if other["data"]["externalId"] == body["externalId"]:
            raise ScimHTTPError(409, "User with externalId already exists", "uniqueness")


def _user_data(body, enterprise):
    data = {k: body[k] for k in ("externalId", "userName", "active", "name", "displayName", "emails", "roles")
            if k in body}
    data["schemas"] = [USER_SCHEMA]
    data.setdefault("roles", [{"value": "user", "primary": False}])
    return data


@router.get("/Users", dependencies=[Depends(require_token)])
def list_users(enterprise: str, request: Request, filter: str = None, startIndex: int = 1, count: int = 30):
    return _list("User", request, enterprise, filter, startIndex, min(count, 1000))


@router.post("/Users", dependencies=[Depends(require_token)])
async def create_user(enterprise: str, request: Request):
    body = await request.json()
    _validate_user(body, enterprise)
    item = {"id": str(uuid.uuid4()), "resourceType": "User", "enterprise": enterprise,
            "login": emu_login(body["userName"], enterprise),
            "data": _user_data(body, enterprise), "meta": {"created": now(), "lastModified": now()}}
    _save(item)
    return ok(_render(item, request, enterprise), 201)


@router.get("/Users/{rid}", dependencies=[Depends(require_token)])
def get_user(enterprise: str, rid: str, request: Request):
    return ok(_render(_get("User", enterprise, rid), request, enterprise))


@router.put("/Users/{rid}", dependencies=[Depends(require_token)])
async def replace_user(enterprise: str, rid: str, request: Request):
    item = _get("User", enterprise, rid)
    body = await request.json()
    _validate_user(body, enterprise, exclude_id=rid)
    item["data"] = _user_data(body, enterprise)
    item["login"] = emu_login(body["userName"], enterprise)
    _save(item)
    return ok(_render(item, request, enterprise))


def _apply_path(data, path, value, op):
    parts = path.split(".")
    target = data
    for p in parts[:-1]:
        target = target.setdefault(p, {})
    if op == "remove":
        target.pop(parts[-1], None)
    else:
        target[parts[-1]] = value


@router.patch("/Users/{rid}", dependencies=[Depends(require_token)])
async def patch_user(enterprise: str, rid: str, request: Request):
    item = _get("User", enterprise, rid)
    body = await request.json()
    ops = body.get("Operations")
    if not isinstance(ops, list) or not ops:
        raise ScimHTTPError(400, "Operations are required", "invalidSyntax")
    data = item["data"]
    for o in ops:
        op = str(o.get("op", "")).lower()
        if op not in ("add", "replace", "remove"):
            raise ScimHTTPError(400, f"Unsupported op: {o.get('op')}", "invalidSyntax")
        path = o.get("path")
        if "[" in (path or ""):
            raise ScimHTTPError(400, "Complex filter paths are not supported", "invalidPath")
        if path:
            _apply_path(data, path, o.get("value"), op)
        elif isinstance(o.get("value"), dict):
            for k, v in o["value"].items():
                _apply_path(data, k, v, op)
        else:
            raise ScimHTTPError(400, "path or object value required", "invalidSyntax")
    _validate_user(data, enterprise, exclude_id=rid)
    item["login"] = emu_login(data["userName"], enterprise)
    _save(item)
    return ok(_render(item, request, enterprise))


@router.delete("/Users/{rid}", dependencies=[Depends(require_token)])
def delete_user(enterprise: str, rid: str):
    _get("User", enterprise, rid)
    store = get_store()
    for g in _all("Group", enterprise):
        members = g["data"].get("members") or []
        if any(m["value"] == rid for m in members):
            g["data"]["members"] = [m for m in members if m["value"] != rid]
            _save(g)
    store.delete("mock-scim", rid)
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# Groups
# --------------------------------------------------------------------------- #
def _validate_members(members, enterprise):
    user_ids = {u["id"] for u in _all("User", enterprise)}
    clean = []
    for m in members or []:
        if not m.get("value"):
            raise ScimHTTPError(400, "member value is required", "invalidValue")
        if m["value"] not in user_ids:
            raise ScimHTTPError(400, f"Member {m['value']} is not a provisioned user", "invalidValue")
        clean.append({"value": m["value"], "displayName": m.get("displayName") or m.get("display")})
    dedup = {m["value"]: m for m in clean}
    return list(dedup.values())


def _validate_group(body, enterprise, exclude_id=None):
    for field in ("externalId", "displayName"):
        if not body.get(field):
            raise ScimHTTPError(400, f"Missing required attribute: {field}", "invalidValue")
    for other in _all("Group", enterprise):
        if other["id"] != exclude_id and other["data"]["displayName"].casefold() == body["displayName"].casefold():
            raise ScimHTTPError(409, "Group with displayName already exists", "uniqueness")


@router.get("/Groups", dependencies=[Depends(require_token)])
def list_groups(enterprise: str, request: Request, filter: str = None, startIndex: int = 1, count: int = 30,
                excludedAttributes: str = None):
    return _list("Group", request, enterprise, filter, startIndex, min(count, 1000), excludedAttributes)


@router.post("/Groups", dependencies=[Depends(require_token)])
async def create_group(enterprise: str, request: Request):
    body = await request.json()
    _validate_group(body, enterprise)
    data = {"schemas": [GROUP_SCHEMA], "externalId": body["externalId"], "displayName": body["displayName"],
            "members": _validate_members(body.get("members"), enterprise)}
    item = {"id": str(uuid.uuid4()), "resourceType": "Group", "enterprise": enterprise, "data": data,
            "meta": {"created": now(), "lastModified": now()}}
    _save(item)
    return ok(_render(item, request, enterprise), 201)


@router.get("/Groups/{rid}", dependencies=[Depends(require_token)])
def get_group(enterprise: str, rid: str, request: Request):
    return ok(_render(_get("Group", enterprise, rid), request, enterprise))


@router.put("/Groups/{rid}", dependencies=[Depends(require_token)])
async def replace_group(enterprise: str, rid: str, request: Request):
    item = _get("Group", enterprise, rid)
    body = await request.json()
    _validate_group(body, enterprise, exclude_id=rid)
    item["data"] = {"schemas": [GROUP_SCHEMA], "externalId": body["externalId"],
                    "displayName": body["displayName"],
                    "members": _validate_members(body.get("members"), enterprise)}
    _save(item)
    return ok(_render(item, request, enterprise))


@router.patch("/Groups/{rid}", dependencies=[Depends(require_token)])
async def patch_group(enterprise: str, rid: str, request: Request):
    item = _get("Group", enterprise, rid)
    body = await request.json()
    data = item["data"]
    members = {m["value"]: m for m in data.get("members") or []}
    for o in body.get("Operations") or []:
        op, path, value = str(o.get("op", "")).lower(), o.get("path") or "", o.get("value")
        if path.startswith("members"):
            m = re.fullmatch(r'members\[value eq "(.+)"\]', path)
            if op == "remove" and m:
                members.pop(m.group(1), None)
            elif op == "remove":
                for v in value or []:
                    members.pop(v.get("value"), None)
            elif op == "add":
                for v in _validate_members(value, enterprise):
                    members[v["value"]] = v
            elif op == "replace":
                members = {v["value"]: v for v in _validate_members(value, enterprise)}
            else:
                raise ScimHTTPError(400, f"Unsupported op: {op}", "invalidSyntax")
        elif op in ("replace", "add") and (path in ("displayName", "externalId") or isinstance(value, dict)):
            updates = {path: value} if path else value
            for k in ("displayName", "externalId"):
                if k in updates:
                    data[k] = updates[k]
            if "members" in (value if isinstance(value, dict) else {}):
                members = {v["value"]: v for v in _validate_members(value["members"], enterprise)}
        else:
            raise ScimHTTPError(400, f"Unsupported operation on path {path!r}", "invalidPath")
    data["members"] = list(members.values())
    _validate_group(data, enterprise, exclude_id=rid)
    _save(item)
    return ok(_render(item, request, enterprise))


@router.delete("/Groups/{rid}", dependencies=[Depends(require_token)])
def delete_group(enterprise: str, rid: str):
    _get("Group", enterprise, rid)
    get_store().delete("mock-scim", rid)
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# Admin view used by the GUI / API (not part of GitHub's API)
# --------------------------------------------------------------------------- #
def snapshot():
    items = get_store().scan("mock-scim")
    users = {i["id"]: i for i in items if i["resourceType"] == "User"}
    out_users = sorted(({
        "id": i["id"], "enterprise": i["enterprise"], "login": i.get("login"),
        "userName": i["data"].get("userName"), "displayName": i["data"].get("displayName"),
        "externalId": i["data"].get("externalId"), "active": i["data"].get("active"),
        "roles": [r.get("value") for r in i["data"].get("roles") or []],
        "lastModified": i["meta"]["lastModified"],
    } for i in users.values()), key=lambda u: u["userName"] or "")
    out_groups = sorted(({
        "id": i["id"], "enterprise": i["enterprise"], "displayName": i["data"].get("displayName"),
        "externalId": i["data"].get("externalId"),
        "members": sorted((users[m["value"]]["data"]["userName"] if m["value"] in users else m["value"])
                          for m in i["data"].get("members") or []),
        "lastModified": i["meta"]["lastModified"],
    } for i in items if i["resourceType"] == "Group"), key=lambda g: g["displayName"] or "")
    return {"users": out_users, "groups": out_groups}


def reset():
    get_store().clear("mock-scim")
    return {"reset": True}
