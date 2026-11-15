# Query language

Dynamic groups, user search, and integration scopes all use a small, safe query language. It is
parsed and evaluated by `query.py`; nothing is passed to `eval`.

```
team == "Payments"
department == "Platform Engineering" and status == "Active"
region in ["EMEA", "APAC"] and not employmentType == "Contractor"
memberOf("engineering-org") and jobLevel in ["IC4", "IC5"]
title matches "^principal " or manager == "someone@halcyon.example"
startDate >= "2025-01-01" and internal.terminationDate exists
```

## Grammar

```
expr     := and_expr ( "or" and_expr )*
and_expr := not_expr ( "and" not_expr )*
not_expr := "not" not_expr | primary
primary  := "(" expr ")"
          | memberOf ( "group-name" )
          | field "exists"
          | field operator value
value    := "string" | 'string' | number | true | false | null | [ value, ... ]
```

Keywords are case-insensitive. Precedence, from highest: `not`, then `and`, then `or`.

## Operators

| Operator | Meaning | Example |
|---|---|---|
| `==`, `!=` | Equality. Strings are compared case-insensitively. | `site == "london"` |
| `<`, `<=`, `>`, `>=` | Ordering for numbers and strings, including ISO dates | `startDate < "2020-01-01"` |
| `in [ … ]` | Value is one of a list | `jobLevel in ["M4", "M5"]` |
| `contains` | Substring; for list fields, contains the element | `title contains "engineer"`, `directReports contains "x@halcyon.example"` |
| `startsWith`, `endsWith` | Prefix or suffix | `email endsWith "@ferrocene.example"` |
| `matches` | Case-insensitive regular expression search | `title matches "^(staff\|principal) "` |
| `exists` | Non-null and non-empty | `internal.terminationDate exists` |
| `memberOf("g")` | The person is in group `g`'s **resolved** membership | `memberOf("all-staff")` |

When a field holds a list, such as `nationality` or `directReports`, a comparison is true if
**any** element matches.

## Fields

Use any dotted path into the user record (for example `work.custom.costCentre` or
`personal.pronouns`), or one of these aliases:

| Alias | Path | Alias | Path |
|---|---|---|---|
| `division` | `work.custom.division` | `status` | `internal.status` |
| `department` | `work.department` | `lifecycle` | `internal.lifecycleStatus` |
| `team` | `work.custom.team` | `employmentType` | `payroll.employment.type` |
| `title` | `work.title` | `legalEntity` | `work.custom.legalEntity` |
| `jobLevel` | `work.custom.jobLevel` | `region` | `work.custom.region` |
| `manager` | `work.reportsTo.email` | `costCentre` | `work.custom.costCentre` |
| `site` | `work.site` | `employeeId` | `work.employeeIdInCompany` |
| `startDate` | `work.startDate` | `isManager` | `work.isManager` |
| `directReports` | `work.directReports` | `country`, `state` | `address.country`, `address.state` |

An unknown field is rejected as an error rather than silently matching nothing, so typos are
caught.

## Groups of groups

`memberOf("other-group")` uses the other group's resolved membership: its static members plus
its query matches, recursively. The resolver:

- **flattens** nested groups to a set of user emails
- rejects **cycles** with a message such as `circular group reference: a -> b -> a`
- rejects references to **unknown groups**

Example: the seeded master provisioning group for GitHub EMU.

```
(memberOf("engineering-org") or memberOf("div-security") or memberOf("dept-corporate-it")
 or memberOf("team-technical-writing") or memberOf("team-developer-relations")
 or memberOf("team-solutions-engineering") or memberOf("team-quillmark-solutions")
 or memberOf("team-ferrocene-developer-community") or memberOf("team-enterprise-applications"))
and memberOf("all-staff")
```

## Trying queries

- **GUI:** the query box on the People page, or **Test query** in the group editor.
- **API:** `POST /api/query {"query": "...", "limit": 50}` returns `{count, emails}`.
- **MCP:** the `evaluate_query` tool.
- **CLI:** `python -m mockhris query 'team == "Payments"'`.
