# REST API

Base URL: `http://localhost:8000`. Interactive OpenAPI docs are at `/docs`, and the schema is at
`/openapi.json`. Bodies are JSON.

Errors return `{"detail": "..."}` with one of these statuses:

| Status | Meaning |
|---|---|
| `404` | Not found |
| `409` | Conflict, e.g. the item already exists or is still referenced |
| `422` | Validation, e.g. an invalid query, unknown field, or reporting cycle |

If `MOCKHRIS_API_KEY` is set, send it as `X-API-Key: <key>` or `Authorization: Bearer <key>`.

## System

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | Liveness and DynamoDB reachability |
| GET | `/api/stats` | Headcounts by lifecycle, employment type, region and division; last sync; integration count |
| GET | `/api/meta` | Field catalogue, query aliases and operators, org structure, sites, enums |

## People

| Method | Path | Description |
|---|---|---|
| GET | `/api/users` | Search. Query params: `search`, `query` (the query language), `division`, `department`, `team`, `site`, `region`, `status`, `lifecycle`, `employmentType`, `limit`, `offset`, and `full` (whole records instead of summaries). |
| POST | `/api/users` | Create. `email`, `firstName` and `surname` are required. `work.reportsTo` can be an email. |
| GET | `/api/users/{email}` | Full record |
| PATCH | `/api/users/{email}` | Deep-merge a partial record |
| PUT | `/api/users/{email}` | Replace the record. Derived fields are recomputed. |
| DELETE | `/api/users/{email}` | Delete. Returns 409 while the person has direct reports. |
| POST | `/api/users/{email}/terminate` | `{"terminationDate": "YYYY-MM-DD", "reason": "Resignation"}` |
| POST | `/api/users/{email}/rename` | `{"newEmail": "..."}` |
| GET | `/api/users/{email}/groups` | Groups the person resolves into, showing static vs query membership |
| GET | `/api/org-chart` | Reporting tree. Params: `root`, `includeTerminated`, `depth`. |
| GET | `/avatars/{id}.svg` | Generated avatar |

```bash
curl -s "localhost:8000/api/users?query=memberOf(%22engineering-org%22)%20and%20region%20%3D%3D%20%22EMEA%22&limit=5"
curl -s -X POST localhost:8000/api/users -H 'Content-Type: application/json' -d '{
  "email": "new.starter@halcyon.example", "firstName": "New", "surname": "Starter",
  "work": {"title": "Software Engineer", "department": "Platform Engineering", "site": "Sydney",
           "reportsTo": "amara.owusu@halcyon.example",
           "custom": {"division": "Engineering", "team": "Observability", "jobLevel": "IC2"}}}'
curl -s -X PATCH localhost:8000/api/users/new.starter@halcyon.example -H 'Content-Type: application/json' \
  -d '{"work": {"title": "Senior Software Engineer", "custom": {"jobLevel": "IC3"}}}'
```

## Groups

| Method | Path | Description |
|---|---|---|
| GET | `/api/groups` | All groups with type (`static`, `dynamic` or `mixed`) and resolved member counts |
| POST | `/api/groups` | `{"name", "displayName", "description", "members": [...], "query": "..."}` |
| GET | `/api/groups/{name}` | Definition, `resolvedMembers`, `references`, `usedBy` |
| PATCH | `/api/groups/{name}` | Update any of `displayName`, `description`, `members`, `query` |
| DELETE | `/api/groups/{name}` | Returns 409 while other groups or integrations reference it |
| POST | `/api/groups/{name}/members` | Add static members: `{"emails": [...]}` |
| POST | `/api/groups/{name}/members/remove` | Remove static members |
| POST | `/api/query` | `{"query": "...", "limit": 100}` returns `{count, emails}` |
| GET | `/api/groups-validate` | Resolve every group and report errors |

## Integrations

| Method | Path | Description |
|---|---|---|
| GET | `/api/integrations/reference` | Templates, target attributes, special sources, aliases, transforms |
| GET | `/api/integrations` | All integrations with their last run (secrets masked) |
| POST | `/api/integrations` | Create from a template: `{"type": "scim2", "id": "slack", "name": "Slack", "connection": {...}}` |
| GET | `/api/integrations/{id}` | Configuration |
| PUT | `/api/integrations/{id}` | Update. Top-level sections are merged; `mappings` replaces the whole list. |
| DELETE | `/api/integrations/{id}` | Delete the configuration and state. The target isn't touched. |
| POST | `/api/integrations/{id}/reset-mappings` | Restore the template's mappings |
| POST | `/api/integrations/{id}/test` | Connection test |
| POST | `/api/integrations/{id}/preview` | `{"email", "draft"?}` returns scope, role, next action, per-attribute trace, payload |
| POST | `/api/integrations/{id}/scope-preview` | `{"mode", "group", "query"}` returns counts and a sample |
| POST | `/api/integrations/{id}/groups-preview` | `{"draft"?}` returns the groups that would be pushed |
| POST | `/api/integrations/{id}/plan` | Dry run. `{"full": false}` |
| POST | `/api/integrations/{id}/sync` | Start a sync (`202`). Returns the run. |
| POST | `/api/integrations/{id}/verify` | Reconcile the target against the HRIS |
| GET | `/api/integrations/{id}/runs` | Run history |
| GET, DELETE | `/api/integrations/{id}/state` | Provisioned identities, or forget them |
| GET | `/api/runs`, `/api/runs/{runId}` | Runs across all integrations, or one run with per-operation results |

```bash
curl -s -X POST localhost:8000/api/integrations -H 'Content-Type: application/json' \
  -d '{"type": "scim2", "id": "wiki", "name": "Team wiki",
       "connection": {"baseUrl": "http://host.docker.internal:8100/t/wiki/scim/v2", "token": "target-token"}}'
curl -s -X PUT localhost:8000/api/integrations/wiki -H 'Content-Type: application/json' \
  -d '{"scope": {"mode": "query", "query": "division == \"Engineering\""},
       "groups": {"patterns": ["team-*"]}}'
curl -s -X POST localhost:8000/api/integrations/wiki/preview -H 'Content-Type: application/json' \
  -d '{"email": "aarav.reddy@halcyon.example"}'
curl -s -X POST localhost:8000/api/integrations/wiki/sync -H 'Content-Type: application/json' -d '{}'
curl -s -X POST localhost:8000/api/integrations/wiki/verify
```

## Built-in GitHub mock

| Method | Path | Description |
|---|---|---|
| * | `/mock-github/scim/v2/enterprises/{enterprise}/Users[/{id}]`, `/Groups[/{id}]` | The GitHub EMU-flavoured SCIM API. Bearer `MOCK_SCIM_TOKEN`. |
| GET | `/api/mock-scim` | Snapshot: users (login, active, roles) and groups with members |
| DELETE | `/api/mock-scim` | Reset the mock |

## Admin

| Method | Path | Description |
|---|---|---|
| POST | `/api/admin/seed` | `{"seed": 20261114}`: wipe and regenerate people and groups |
| GET | `/api/admin/export` | Export all users and groups as JSON |
| POST | `/api/admin/import?replace=true` | Import an export bundle, replacing or merging |
