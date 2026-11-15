# MCP server

MockHRIS includes a [Model Context Protocol](https://modelcontextprotocol.io) server, built with
the official Python SDK. An AI assistant such as Claude can use it to query the HRIS, manage
people and groups, and run provisioning: plan, sync and verify.

## Connecting

**Streamable HTTP** (when the app is running, e.g. in Docker):

```bash
claude mcp add --transport http mockhris http://localhost:8000/mcp
```

**stdio** (the client launches the server, which talks to the DynamoDB container directly):

```json
{
  "mcpServers": {
    "mockhris": {
      "command": "python",
      "args": ["-m", "mockhris", "mcp"],
      "cwd": "/path/to/scim-mock-hris",
      "env": { "DYNAMODB_ENDPOINT": "http://localhost:8001" }
    }
  }
}
```

**Security:**
- The HTTP endpoint has DNS-rebinding protection: only `localhost`, `127.0.0.1` and `[::1]`
  Host headers are accepted. Add others with `MCP_ALLOWED_HOSTS`.
- If `MOCKHRIS_API_KEY` is set, clients must send it as a Bearer token or `X-API-Key`.

## Tools (33)

Each tool is annotated as read-only, write, or destructive. Expected errors, such as an unknown
user, an invalid query or a reporting cycle, come back to the model as readable tool errors.

### Schema and organisation
| Tool | Description |
|---|---|
| `describe_schema` | Field catalogue, query aliases and operators, org structure, sites, enums |
| `hris_stats` | Headcounts by lifecycle, employment type, region and division |
| `org_chart` | Reporting tree from a person (default: the CEO) to a given depth |

### People
| Tool | Description |
|---|---|
| `search_users` | Free text, query language, or exact-match filters, with paging |
| `get_user` | Full record |
| `get_user_groups` | Groups the person resolves into |
| `create_user` | Create a person. Site-derived fields are filled automatically. |
| `update_user` | Deep-merge a patch, e.g. a title, manager or lifecycle change |
| `terminate_user` | Mark as terminated |
| `rename_user` | Change email (the primary key) |
| `delete_user` | Delete. Fails while the person has reports. |

### Groups
| Tool | Description |
|---|---|
| `list_groups`, `get_group` | Definitions, resolved members, references, used-by |
| `evaluate_query` | Test a query before saving it |
| `create_group`, `update_group`, `delete_group` | CRUD |
| `add_group_members`, `remove_group_members` | Change static members |

### Integrations
| Tool | Description |
|---|---|
| `list_integrations`, `get_integration` | Configuration (secrets masked) and last run |
| `integration_reference` | Templates, target attributes, sources, transforms |
| `create_integration`, `update_integration`, `delete_integration` | Manage connections, scope, mappings, roles, groups and actions |
| `test_integration` | Connection test |
| `preview_integration_user` | How one person maps: scope, role, next action, attribute trace, payload |
| `plan_integration` | Dry run |
| `sync_integration` | Execute (waits by default) |
| `verify_integration` | Read back from the target and reconcile |
| `list_sync_runs`, `get_sync_run` | Run history and per-operation results |
| `mock_github_snapshot` | What the built-in GitHub mock holds |

## Example prompts

- *"Who in EMEA engineering is a staff-level or principal engineer, excluding contractors?"*
- *"Create a group `payments-oncall` with everyone in the Payments and Core API teams who has
  been here more than a year."*
- *"Add a mapping to the scim-target integration that sends `{department} ({site})` as
  `title`, preview it for Aarav Reddy, then plan the sync."*
- *"Terminate amelia.doyle@halcyon.example, sync the scim-target integration, and verify the
  target."*
