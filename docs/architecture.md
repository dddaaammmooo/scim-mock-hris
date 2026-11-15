# Architecture

```
                         ┌──────────────────────── app container (FastAPI, :8000) ───────────────────────┐
 Browser ── /ui ────────▶│ static GUI (vanilla JS SPA)                                                   │
 curl/tests ── /api ────▶│ REST API (api.py) ───┐                                                        │
 Claude/MCP ── /mcp ────▶│ MCP server (mcp_server.py, Streamable HTTP) ──┤                               │
 CLI (python -m mockhris)│                      ▼                                                        │
                         │   service layer:  hr.py (people, groups, org)   integrations.py (provisioning) │
                         │                   query.py (query language)     avatar.py, generator.py       │
                         │                      │                                   │ SCIM 2.0 (httpx)  │
                         │                      ▼                                   ▼                   │
                         │   store.py (DynamoDB + write-through cache)   /mock-github (mock_scim.py)     │
                         └──────────────────────┬────────────────────────────────────┬─────────────────┘
                                                ▼                                    ▼
                                 DynamoDB Local container (:8001)       scim-target (:8100) or GitHub EMU
```

## Components

| Module | Responsibility |
|---|---|
| `generator.py` | Builds the fictitious company deterministically from a seed: org design, people, lifecycle edge cases, groups |
| `avatar.py` | Deterministic SVG portraits; shirt colour follows the division |
| `query.py` | Tokenizer, parser and evaluator for the group query language; resolves nested groups and detects cycles |
| `hr.py` | People and group CRUD; derived fields such as status, address, region, entity and manager links; org chart; seeding and import/export |
| `integrations.py` | Templates, the integration store and validation, attribute mapping, planning, the SCIM client, execution, verification, and run management |
| `mock_scim.py` | A GitHub EMU-flavoured SCIM mock (auth, required fields, uniqueness, EMU logins) |
| `store.py` | DynamoDB access, table creation, and a scan cache |
| `api.py` / `mcp_server.py` / `__main__.py` | REST, MCP and CLI front ends over the same services |
| `app.py` | ASGI app: lifespan (tables, seeding, migration, avatar backfill), API-key guard, error mapping, mounts |
| `static/` | GUI: People, Groups, Org chart, Integrations editor, GitHub mock viewer, Admin |

## Storage

DynamoDB tables, each with a single string hash key:

| Table | Key | Contents |
|---|---|---|
| `<prefix>-users` | `email` | HiBob-style employee records |
| `<prefix>-groups` | `name` | Group definitions: static `members` and/or `query` |
| `<prefix>-integrations` | `id` | Provisioning integrations |
| `<prefix>-scim-state` | `key` | `<integration>#user#<email>` / `<integration>#group#<name>` mapped to the target id, last payload and hash |
| `<prefix>-sync-runs` | `id` | Sync run records with per-operation results |
| `<prefix>-settings` | `key` | Miscellaneous settings |
| `<prefix>-mock-scim` | `id` | Resources held by the built-in GitHub mock |

Group resolution needs whole-table views, because a query is evaluated against every person.
`store.py` therefore caches full scans in process. The cache is updated by this process's own
writes and expires after a 3-second TTL. That bounds staleness when another process, such as a
stdio MCP server, writes to the same tables.

## Provisioning flow

```
HR users ─▶ scope (group | query | all) ─▶ map each user (mappings + transforms + $role ...) ─▶ desired payloads
                                                                                                    │
                       stored state (last payload hash + target id per user/group) ◀── diff ────────┘
                                                                                                    │
     plan: create / reactivate / update users ─▶ create / update groups ─▶ delete groups ─▶ disable / delete users
                                                                                                    │
                       execute over SCIM (409 → adopt, 404 → recreate, 429 → retry) ─▶ update state
                                                                                                    │
                                    follow-up pass if new users made references resolvable ─────────┘
```

**Verify** is independent of the stored state. It reads `/Users` and `/Groups` from the target,
then compares them with the desired payloads and group memberships computed fresh from the
HRIS.

## Design decisions

- **Email is the person's identity in the HRIS.** The HiBob `id` is the SCIM `externalId`.
  Renaming a user's email updates group memberships, manager references and provisioning
  state.
- **Groups are flattened before provisioning,** because GitHub EMU and many other targets don't
  support nested groups.
- **One service layer.** The GUI, REST API, MCP server and CLI behave identically, and errors map
  to the same HTTP or MCP error semantics.
- **No build step and no CDN** for the GUI, so it works offline and is easy to read.
