# Configuration

All settings are environment variables. In Docker, set them in `docker-compose.yml` or in a
`.env` file next to it.

| Variable | Default | Purpose |
|---|---|---|
| `DYNAMODB_ENDPOINT` | `http://localhost:8001` (compose: `http://dynamodb:8000`) | DynamoDB endpoint. **Empty means real AWS DynamoDB**, using the default credential chain. |
| `AWS_REGION` | `us-east-1` | Region for DynamoDB |
| `TABLE_PREFIX` | `mockhris` | Table names are `<prefix>-users`, `<prefix>-groups`, and so on |
| `AUTO_SEED` | `true` | Generate the company on startup when the users table is empty |
| `MOCKHRIS_API_KEY` | (unset) | When set, `/api` and `/mcp` require `X-API-Key` or `Authorization: Bearer`. The GUI prompts for it. `/api/health` stays open. |
| `INTEGRATION_TOKEN_<ID>` | (unset) | Bearer token for an integration, overriding the stored one. The ID is upper-cased with `-` replaced by `_`, e.g. `INTEGRATION_TOKEN_GITHUB_EMU`. |
| `GITHUB_SCIM_TOKEN` | (unset) | Alias for `INTEGRATION_TOKEN_GITHUB_EMU` |
| `MOCK_SCIM_TOKEN` | `mock-scim-token` | Token accepted by the built-in GitHub mock |
| `PUBLIC_BASE_URL` | `http://localhost:8000` | Used for `about.avatar` URLs and the default GitHub mock base URL |
| `MCP_ALLOWED_HOSTS` | (unset) | Comma-separated extra `Host` values for the MCP endpoint, e.g. `mockhris.internal:*` |

## Networking

- Both containers publish on `127.0.0.1` only: the app on `8000`, DynamoDB Local on `8001`.
- To reach a service on the host, such as scim-target on `:8100`, the app container uses
  `host.docker.internal`. The compose file maps it with `extra_hosts: host-gateway`, so this
  also works on Linux Docker.
- To expose MockHRIS beyond localhost, set `MOCKHRIS_API_KEY` and `MCP_ALLOWED_HOSTS`, and put
  it behind TLS.

## Secrets

Integration tokens and passwords entered in the GUI are stored in DynamoDB and never returned:
responses show `********`. For real targets, prefer `INTEGRATION_TOKEN_<ID>` so the secret
never touches the database. The GUI then shows *"set by environment variable"*.

## Persistence and reset

| Goal | How |
|---|---|
| Keep data across restarts | Automatic (`dynamodb-data` volume) |
| Regenerate people and groups | Admin → Regenerate, `POST /api/admin/seed`, or `python -m mockhris seed --seed N` |
| Forget provisioning state | Integration → Provisioning actions → Forget state |
| Start completely fresh | `docker compose down -v && docker compose up -d --build` |

## Using real AWS DynamoDB

```bash
DYNAMODB_ENDPOINT= AWS_REGION=ap-southeast-2 TABLE_PREFIX=mockhris-dev python -m mockhris serve
```

Missing tables are created on demand, with PAY_PER_REQUEST billing.
