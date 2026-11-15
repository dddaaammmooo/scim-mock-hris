# Provisioning integrations

An **integration** provisions HR identities to one SCIM 2.0 target. Its configuration has the
same shape as an outbound provisioning connection in PingOne:

```
connection ─ where and how to call the target
scope      ─ which users are provisioned
mappings   ─ target SCIM attribute ← source expression (+ transforms, default, apply-on)
roles      ─ rule-based role value, exposed to mappings as $role
groups     ─ which HR groups are pushed and how they are named
actions    ─ create / update allowed; leavers are disabled, deleted or left alone
```

You can manage integrations in the GUI (**Integrations**), through the REST API
(`/api/integrations`), through MCP (`*_integration` tools), or with the CLI.

## Templates

| Template | Use it for | Required target attributes | Group member key |
|---|---|---|---|
| `github-emu` | GitHub Enterprise Managed Users (github.com or GHE.com) | `userName`, `externalId`, `active`, `displayName`, `emails[type eq "work"].value` | `displayName` |
| `scim2` | Any RFC 7643/7644 service provider, e.g. [scim-target](https://github.com/dddaaammmooo/scim-target), Slack, Atlassian, Zoom | `userName` | `display` |

Two integrations are created on first start:
- **`github-emu`** targets the built-in GitHub mock.
- **`scim-target`** targets `http://host.docker.internal:8100/t/hris/scim/v2`.

## Connection

| Field | Notes |
|---|---|
| `baseUrl` | SCIM base URL, without a trailing `/Users` |
| `authType` | `bearer`, `basic` or `none` |
| `token`, `username`, `password` | Stored in DynamoDB and masked as `********` in every response. Sending `********` back keeps the stored value. `INTEGRATION_TOKEN_<ID>` overrides the token. |
| `headers` | Extra request headers |
| `apiVersion` | GitHub only: sent as `X-GitHub-Api-Version` |
| `verifyTls`, `timeoutSeconds`, `requestDelayMs` | Transport options. The delay paces requests. |

**Test connection** sends `GET /Users?count=1`.

## Users in scope

| Mode | Meaning |
|---|---|
| `group` | Members of an HR group, static or dynamic, with nested groups flattened |
| `query` | People matching a [query](query-language.md) |
| `all` | Everyone in the HRIS |

Only people whose HR status is **Active** (`employed` or `leave`) are provisioned. People in
scope who are inactive, such as a future starter or a leaver, are never created. If they were
provisioned before, the deprovision action applies to them.

## Attribute mapping

Each mapping row has:

| Field | Meaning |
|---|---|
| `target` | A SCIM attribute path. Supported forms are below. |
| `source` | The expression that produces the value |
| `transforms` | Applied in order |
| `default` | Used when the source is empty |
| `applyOn` | `always`, or `create` to send on creation and keep the previously sent value on updates |

**Target path forms:**

| Form | Example | Result |
|---|---|---|
| Simple or dotted | `title`, `name.givenName` | `{"name": {"givenName": …}}` |
| Multi-valued filter | `emails[type eq "work"].value`, `addresses[type eq "work"].country` | `[{"type": "work", "value": …, "primary": true}]` |
| List | `roles[].value` | `[{"value": …, "primary": false}]` |
| Extension URN | `urn:ietf:params:scim:schemas:extension:enterprise:2.0:User:department`, `…:User:manager.value` | Nested under the URN key; the URN is added to `schemas` |

**Source expressions:**

| Kind | Example |
|---|---|
| Field or alias | `email`, `title`, `work.custom.costCentre`, `personal.preferredName` |
| Template | `{firstName}.{surname}`, `{department} ({site})` |
| Literal | `"Halcyon"`, `true` |
| Computed | `$active` (bool), `$role`, `$fullName`, `$managerEmail`, `$managerScimId` (the manager's id **on the target**, available once the manager is provisioned) |

**Transforms:** `lowercase`, `uppercase`, `trim`, `localPart` (the part before `@`), `domain`,
`asciiFold` (`Ó` → `O`), `removeSpaces`, `slugify`, `toString`, `firstItem`.

**Live preview.** On the mapping tab, pick any person. The editor calls
`POST /api/integrations/{id}/preview` with your **unsaved** draft and shows:
- whether the person is in scope and active, their role, and what the next sync would do
- each attribute's raw source value and its final value
- the full SCIM payload

Validation errors such as an unknown source field, a duplicate target, a missing required
attribute or an unknown transform appear immediately.

### Default mappings

| Target | github-emu | scim2 |
|---|---|---|
| `externalId` | `id` | `id` |
| `userName` | `email` → lowercase | `email` → lowercase |
| `active` | `$active` | `$active` |
| `name.givenName` / `familyName` / `formatted` | `firstName` / `surname` / `$fullName` | same |
| `displayName` | `displayName` | `displayName` |
| `emails[type eq "work"].value` | `email` → lowercase | same |
| `roles[].value` | `$role` | — |
| `nickName`, `title`, `userType`, `timezone` | — | `personal.preferredName`, `title`, `employmentType`, `address.timezone` |
| `addresses[type eq "work"].locality` / `.country` | — | `address.city` / `country` |
| enterprise `employeeNumber`, `costCenter`, `organization`, `division`, `department`, `manager.value` | — | `employeeId`, `costCentre`, `legalEntity`, `division`, `department`, `$managerScimId` |

## Role rules

Role rules are evaluated in order, and the first group that contains the person wins.
Otherwise the default applies. The result is available to mappings as `$role`. For
`github-emu`, values are restricted to `user`, `enterprise_owner`, `billing_manager` and
`guest_collaborator`. By default, members of `github-enterprise-owners` map to
`enterprise_owner`.

## Group provisioning

| Field | Meaning |
|---|---|
| `enabled` | Whether to push groups at all |
| `patterns` / `exclude` | Glob patterns over HR group names, e.g. `team-*` |
| `nameSource` | The target `displayName` comes from the HR group's `displayName` or its `name`. `externalId` is always the HR group `name`. |
| `skipEmpty` | Don't create groups with no provisioned members |

Group members are the group's resolved membership limited to **provisioned, active** users,
referenced by their target ids.

## Provisioning actions

| Field | Values |
|---|---|
| `create` | Create users that are in scope but missing on the target |
| `update` | Update users whose mapped payload changed |
| `deprovision` | `disable` (PATCH `active: false`, which suspends on GitHub EMU), `delete`, or `none` |

## Plan, sync and verify

**Plan** (`POST /api/integrations/{id}/plan`) is a dry run. It returns the operations and a
summary of each: changed attributes, and group members added or removed.

| Operation | When |
|---|---|
| `create_user` | Active and in scope, not yet provisioned |
| `reactivate_user` | Provisioned earlier, then disabled, now active again |
| `update_user` | Mapped payload differs from the last one sent (`full: true` forces it) |
| `create_group` / `update_group` / `delete_group` | Group selection or membership changed |
| `deactivate_user` / `delete_user` | Inactive, out of scope, or removed from the HRIS |

**Sync** (`POST /api/integrations/{id}/sync`) runs in the background. Poll `GET /api/runs/{id}`
for progress. Execution rules:

- Operations run in dependency order: users first, then groups, then group deletions, then
  user deprovisioning.
- **409 on create:** the existing resource is looked up by `userName` or `externalId`, then
  adopted with a PUT.
- **404 on update:** the resource is re-created.
- **429:** retried after `Retry-After`, up to three times.
- Failed operations don't advance their state, so the next sync retries them.
- **Follow-up pass:** if users created in this run are referenced by other users (through
  `$managerScimId`), the attribute updates that become possible are applied **in the same
  run**, so one sync converges.

**Verify** (`POST /api/integrations/{id}/verify`) reads every user and group back from the
target and compares **every mapped attribute** against the HRIS. It reports:
- **missing:** expected active users that aren't on the target
- **mismatched:** each differing attribute, with expected and actual values
- **wronglyActive:** people who should be disabled but are still active
- **unexpected:** active target users that the HRIS doesn't expect
- **group membership differences:** missing or extra members, by userName

It catches HR changes that haven't been synced yet, and edits made directly on the target.

> A target that silently drops attributes shows up in Verify too. For example, the built-in
> GitHub mock, like GitHub itself, doesn't store the enterprise extension. Map only what the
> target supports.

## GitHub EMU notes

- **Base URL:** `https://api.github.com/scim/v2/enterprises/{enterprise}`. For GHE.com data
  residency use `https://api.{subdomain}.ghe.com/...`.
- **Token:** a personal access token (classic) from the enterprise **setup user**, with the
  `scim:enterprise` scope.
- **Suspension:** `active: false` suspends the user. GitHub obfuscates the handle and email of
  suspended users.
- **Usernames:** EMU creates `handle_shortcode` itself from `userName`. The built-in mock
  imitates this, so `ada.lovelace@x` becomes `ada-lovelace_halcyon`.
- **Before going live:** rehearse against the built-in mock, then plan against your real
  enterprise and review the plan before you sync.
