# Data model

## The company

*Halcyon Systems Group* is fictitious. Every name, email address and identifier is generated.
`generator.py` builds the company deterministically from a seed: run `python -m mockhris seed
--seed N`, or use **Admin → Regenerate**.

| Dimension | Values |
|---|---|
| Regions | APAC, EMEA, AMER |
| Sites | Sydney, Melbourne, Singapore, Tokyo, Bengaluru, London, Dublin, Berlin, San Francisco, New York, Austin, Toronto; remote in the US, UK and EU |
| Legal entities | Halcyon Systems Group Ltd (AU parent), Inc. (US), Canada Inc., UK Ltd, Ireland Ltd, GmbH, India Pvt. Ltd., Asia Pte. Ltd., Japan K.K., Employer of Record (NL) |
| Subsidiaries | Ferrocene Labs GmbH (`@ferrocene.example`), Quillmark Analytics Inc. (`@quillmark.example`), Bluegate Security Ltd (`@bluegate.example`) |
| Divisions | Executive Office, Engineering, Security, Information Technology, Product, Revenue, Marketing, Finance, Legal & Compliance, People, Operations, plus the three subsidiaries |
| Hierarchy | Division (C-level or subsidiary GM) → department (VP or director) → team (manager) → individual contributors |
| Job levels | `E1` CEO, `E2` C-suite, `E3` subsidiary GM, `M5` VP, `M4` director, `M3`/`M2` managers, `IC1`–`IC5` |

The subsidiary GMs report across entities: Ferrocene to the CTO, Quillmark to the CPO, and
Bluegate to the CISO.

**Edge cases seeded on purpose:**
- **Lifecycle:** `terminated` (Inactive, with a termination date and reason); `hired` (start
  date in the future, Inactive); `leave` (Active).
- **Employment type:** `Permanent`, `Fixed Term`, `Contractor` or `Intern`, with full-time and
  part-time contracts.
- **Names:** diacritics, apostrophes, hyphens and multi-word surnames. Email addresses are
  folded to ASCII.
- **Namesakes:** identical names get a numeric suffix (`jane.doe2@`).

## User record (HiBob-style)

```jsonc
{
  "id": "3332883884017958642",            // HiBob employee id -> SCIM externalId
  "companyId": 640321,
  "email": "aarav.reddy@halcyon.example",  // primary key in MockHRIS
  "firstName": "Aarav", "surname": "Reddy", "displayName": "Aarav Reddy", "fullName": "Aarav Reddy",
  "creationDateTime": "2019-03-04T09:00:00.000000",
  "personal": { "gender": "Male", "pronouns": "He/Him", "preferredName": null, "nationality": ["India"],
                "shortBirthDate": "07-02", "age": 34, "communication": { "slackUsername": "aarav.reddy" } },
  "about":    { "avatar": "http://localhost:8000/avatars/3332883884017958642.svg", "hobbies": ["Chess"] },
  "work": {
    "employeeIdInCompany": "HSG-10043", "title": "Senior Network Engineer",
    "department": "Platform Engineering", "site": "Bengaluru", "siteId": 205, "startDate": "2021-06-14",
    "reportsTo": { "id": "…", "displayName": "Amara Owusu", "email": "amara.owusu@halcyon.example" },
    "isManager": false, "directReports": [], "secondLevelManager": "…", "tenureYears": 5,
    "custom": { "division": "Engineering", "team": "Cloud Infrastructure", "jobLevel": "IC3",
                "costCentre": "CC-120", "legalEntity": "Halcyon Systems India Pvt. Ltd.", "region": "APAC" }
  },
  "payroll":  { "employment": { "type": "Permanent", "contract": "Full time", "weeklyHours": 40,
                                "legalEntity": "Halcyon Systems India Pvt. Ltd." } },
  "address":  { "country": "India", "state": "Karnataka", "city": "Bengaluru", "timezone": "Asia/Kolkata" },
  "internal": { "status": "Active", "lifecycleStatus": "employed", "terminationDate": null, "terminationReason": null }
}
```

### Derived fields, recomputed on every write

| Field | Rule |
|---|---|
| `internal.status` | `Active` if `lifecycleStatus` is `employed` or `leave`, otherwise `Inactive` |
| `address.*`, `work.siteId`, `custom.region`, `custom.legalEntity` | Derived from `work.site` when it's a known site. Subsidiaries keep their own entity. Values you set explicitly win. |
| `work.reportsTo` | You set it by email. The id and display name are filled in. Self-reporting and reporting cycles are rejected. |
| `directReports`, `isManager`, `secondLevelManager` | Recomputed across the whole organisation. Terminated people are excluded from reports. |
| `fullName`, `displayName` | `displayName` follows name changes unless you set it explicitly |
| `about.avatar` | Defaults to the generated `/avatars/<id>.svg`. Set any URL to override it. |

**Operations:**
- **terminate:** sets `lifecycleStatus=terminated`, plus a date and reason.
- **rename:** changes the email, and with it group memberships, manager references and
  provisioning state.
- **delete:** blocked while the person still has direct reports.

## Groups

```json
{
  "name": "github-enterprise-owners",
  "displayName": "GitHub Enterprise Owners",
  "description": "CTO and CISO (static) plus the IAM team (dynamic).",
  "members": ["benjamin.thornton@halcyon.example", "lea.mercier@halcyon.example"],
  "query": "memberOf(\"team-identity-access-management\") and memberOf(\"all-staff\")"
}
```

Resolved membership is the static `members` plus everyone the query matches. See
[query-language.md](query-language.md).

### Seeded groups

| Kind | Examples |
|---|---|
| Org mirrors | `div-*`, `dept-*`, `team-*`, `region-*`, `entity-*`: one per division, department, team, region and legal entity |
| Workforce | `all-staff`, `employees`, `contractors`, `interns`, `people-managers`, `executive-leadership`, `senior-leadership`, `subsidiary-staff` |
| GitHub EMU access (nested) | `engineering-org`, `github-emu-users` (the provisioning scope), `github-copilot`, `github-enterprise-owners`, `security-champions`, `emea-engineering`, `soc2-production-access`, `incident-commanders`, `legal-and-compliance-reviewers` |

A group can't be deleted while another group's query uses it, or while an integration uses it
for its scope or role rules.

## Avatars

`GET /avatars/<id>.svg` returns a deterministic illustrated portrait. The face, hair, glasses
and so on come from the person's id. Shirt colour comes from their division, and the org chart
uses the same colours. The images are generated locally, so no third-party image service is
involved.
