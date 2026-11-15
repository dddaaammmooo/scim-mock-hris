# Development

## Setup

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt       # bin/ on macOS/Linux
docker compose up -d dynamodb                           # DynamoDB Local on :8001
.venv/Scripts/python -m mockhris serve --port 8000      # API + GUI + MCP with code from your working tree
```

The GUI is plain HTML, CSS and JavaScript in `mockhris/static/`, so a browser refresh picks up
changes. After changing Python code, restart `serve`, or run uvicorn with `--reload`.

## CLI

```bash
python -m mockhris serve [--host 127.0.0.1] [--port 8000]
python -m mockhris mcp                                  # MCP over stdio
python -m mockhris seed [--seed N]                      # wipe and regenerate people and groups
python -m mockhris check                                # resolve every group; non-zero exit on errors
python -m mockhris query 'memberOf("engineering-org") and region == "EMEA"'
python -m mockhris export out.json | import in.json [--merge]
python -m mockhris integrations                         # list integrations
python -m mockhris plan <id> [--full]
python -m mockhris sync <id> [--full]                   # foreground; non-zero exit on failures
python -m mockhris verify <id>                          # JSON report; non-zero exit on drift
```

## Tests

```bash
.venv/Scripts/python -m pytest                                   # backend: 100% line + branch coverage enforced
.venv/Scripts/python -m playwright install chromium              # once, for the UI tests
.venv/Scripts/python -m pytest -m ui --no-cov                    # browser tests (bundled chromium)
PLAYWRIGHT_CHANNEL=msedge .venv/Scripts/python -m pytest -m ui --no-cov   # or an installed Edge/Chrome
```

`pytest.ini` runs the **backend suite** by default (`-m "not ui"`), with `--cov-branch` and
`fail_under = 100` from `.coveragerc`. It runs against **moto**'s in-memory DynamoDB, so it
needs no Docker. The backend tests are:

| File | Covers |
|---|---|
| `tests/test_units.py` | Query language (every operator and error, nesting, cycles); avatars; DynamoDB store (cache, pagination, retries, conditions); settings; the generator |
| `tests/test_hr.py` | People: create, update, derived fields, manager rules, terminate, rename, delete. Groups: CRUD and reference guards. Org chart, meta, stats, import/export, avatar backfill. |
| `tests/test_integrations.py` | Mapping helpers and transforms; integration validation, masking and migration. Planning for every operation type; execution against a programmable fake SCIM server, including 409 adopt, 404 recreate, 429 retry, 500, network errors, skipped members and follow-up passes. Verify (every drift category); the runner (locking, background runs, crashes). |
| `tests/test_surfaces.py` | The built-in GitHub mock (all endpoints and errors); REST endpoints and the API-key guard; every MCP tool and the tool-error mapping; every CLI command and exit code |
| `tests/test_app.py` | End-to-end API flows: GitHub sync against the built-in mock, Verify tampering, custom SCIM 2.0 mapping, the MCP endpoint over Streamable HTTP |

The **UI suite** (`tests/ui/`, marker `ui`) starts the real app with uvicorn on a free port and
drives it with Playwright. It covers navigation; the fade-in behaviour and reduced motion;
People (search, filters, queries, paging, create, edit, raw JSON, terminate, rehire, rename,
delete); Groups (query testing, members, used-by links); the org chart (pan, zoom, expand,
focus, find, list view); and the whole integration editor, including plan, sync with live
progress, verify (in sync and with drift), Activity, the GitHub mock page and Admin. **Any
JavaScript error fails the test.**

## Continuous integration

`.github/workflows/tests.yml` runs three jobs on every push to `main` and on every pull request:

1. **backend**: the coverage-enforced suite, uploading `coverage.xml`.
2. **ui**: installs chromium and runs the Playwright suite.
3. **docker**: `docker compose up --build`. It then checks health, seeding and the GUI, runs a
   GitHub EMU sync against the built-in mock, and requires Verify to pass.

## Screenshots

`scripts/screenshots.py` regenerates every image in `docs/images` from a running instance. It
needs scim-target too, for the drift screenshot.

```bash
PLAYWRIGHT_CHANNEL=msedge .venv/Scripts/python scripts/screenshots.py --base http://localhost:8000 --target http://localhost:8100
```

## Code layout

```
mockhris/
  __main__.py      CLI
  app.py           ASGI app factory: lifespan, API-key guard, error handlers, mounts
  api.py           REST endpoints
  mcp_server.py    MCP tools (MCPServer, Streamable HTTP and stdio)
  services.py      service accessors shared by the front ends
  hr.py            people, groups, org chart, derived fields, seed/import/export
  query.py         query language parser, evaluator and group resolver
  integrations.py  templates, mapping, plan/execute/verify, SCIM client, runs
  mock_scim.py     GitHub EMU-flavoured SCIM mock
  generator.py     the fictitious company
  avatar.py        SVG avatars
  store.py         DynamoDB access with a scan cache
  settings.py      environment configuration
  static/          index.html, app.css, app.js
tests/             pytest suite (moto)
docs/              documentation and screenshots
```

## Conventions

- Validate in the service layer (`hr.py`, `integrations.py`). Every front end inherits the same
  rules and errors.
- Raise `NotFound`, `Conflict` or `Invalid`. The API maps them to 404, 409 and 422, and MCP
  turns them into readable tool errors.
- The UI uses no frameworks or build tools. Keep it dependency-free.
