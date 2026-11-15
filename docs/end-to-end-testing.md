# End-to-end testing

The aim is to prove that what the HRIS says equals what the target holds, across the whole
identity lifecycle.

## Setup

```bash
git clone https://github.com/dddaaammmooo/scim-mock-hris.git
git clone https://github.com/dddaaammmooo/scim-target.git
(cd scim-mock-hris && docker compose up -d --build)
(cd scim-target   && docker compose up -d --build)
```

The `scim-target` integration in MockHRIS is preconfigured with:
- base URL `http://host.docker.internal:8100/t/hris/scim/v2`
- bearer token `target-token`
- scope `all-staff`
- 20 attribute mappings, including the enterprise extension and manager references
- `div-*`, `region-*` and `people-managers` groups pushed

## Scenarios

| # | Scenario | Action in MockHRIS | Verify before sync | After **Sync now** |
|---|---|---|---|---|
| 1 | Initial load | **Sync now** | — | ~425 users and 18 groups created, managers linked; Verify ✓ |
| 2 | Idempotence | **Plan** | ✓ | Plan shows nothing to do |
| 3 | Leaver | People → *Terminate* | ✗ should be disabled but still active, plus group differences | User suspended (`active: false`), removed from groups; ✓ |
| 4 | Rehire | Profile → *Rehire* | ✗ | `reactivate_user`, groups updated; ✓ |
| 5 | Mover | Change team, site or title | ✗ attribute mismatches (`title`, `enterprise:department`, …) | `update_user`, memberships moved; ✓ |
| 6 | New joiner | People → *New person* (lifecycle `employed`) | ✗ missing on target | `create_user`; ✓ |
| 7 | Future starter | New person with lifecycle `hired` | ✓ (not expected yet) | Not created until their lifecycle becomes `employed` |
| 8 | Out-of-band edit | PATCH a user directly on scim-target | ✗ shows the attribute, expected vs actual | **Full resync** repairs it; ✓ |
| 9 | Target wiped | Delete the `hris` tenant on scim-target | ✗ everything missing | *Provisioning actions → Forget state*, then sync re-creates everything; ✓ |
| 10 | Flaky target | scim-target faults: `errorRatePercent: 15` | — | Run is `partial` with per-operation errors; the next sync completes; ✓ |
| 11 | Throttling | scim-target faults: `rateLimitEvery: 10` | — | 429s are retried after `Retry-After`; run succeeds |
| 12 | Scope change | Users in scope → a query such as `region == "EMEA"` | ✗ unexpected active users | Out-of-scope users are disabled; ✓ |

## Automating it

```python
import time, urllib.parse, httpx

H, T = "http://localhost:8000", "http://localhost:8100"
c = httpx.Client(timeout=120)

def sync(iid="scim-target", full=False):
    run = c.post(f"{H}/api/integrations/{iid}/sync", json={"full": full}).json()
    while run["status"] == "running":
        time.sleep(0.5)
        run = c.get(f"{H}/api/runs/{run['id']}").json()
    assert run["status"] == "succeeded", [r for r in run["results"] if r["status"] != "ok"][:3]

def verify(iid="scim-target"):
    return c.post(f"{H}/api/integrations/{iid}/verify").json()

sync()
assert verify()["ok"]

q = urllib.parse.quote('memberOf("all-staff") and isManager == false and team == "Payments"')
leaver = c.get(f"{H}/api/users?limit=1&query={q}").json()["items"][0]["email"]
c.post(f"{H}/api/users/{leaver}/terminate", json={})
assert any(w["email"] == leaver for w in verify()["users"]["wronglyActive"])   # drift detected
sync()
assert verify()["ok"]

remote = next(u for u in c.get(f"{T}/api/tenants/hris/users").json() if u["userName"] == leaver)
assert remote["active"] is False
```

The CLI is handy in CI as well. `python -m mockhris verify scim-target` exits non-zero when
there is drift.

## Rehearsing GitHub EMU

The `github-emu` integration targets the built-in GitHub mock (**Mock GitHub target** in the
GUI). The mock enforces:
- GitHub's required attributes
- uniqueness of `userName` and `externalId`
- that group members are already provisioned
- suspend-on-`active: false`
- EMU `handle_shortcode` logins

Run the same scenarios there before pointing the integration at a real enterprise.
