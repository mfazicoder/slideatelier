# Hatchik — known issues

Filed bugs that aren't blocking but need fixing. Tick when closed; move
to the bottom if you want a history. Always reference the commit that
closes each item.

Format per entry: severity, where it surfaced, root cause guess, action
plan, owner, opened-on. Keep prose short.

---

## Open

### [P2] Redeploy webhook returns 500 at provision time (race condition)

- **Surfaced**: smoke test signup #4 (slug `smoketest`), 2026-05-14 00:30 UTC.
  Journal:
  ```
  00:30:41 INFO redeploy auth ok slug=smoketest via=github-webhook
  00:30:41 INFO  140.82.115.56:0 - "POST /api/tenants/smoketest/redeploy HTTP/1.1" 500
  ```
- **Trigger**: GitHub fires the redeploy webhook from `140.82.115.56`
  (GitHub IP range) immediately on initial commit. Provision.py is
  still finishing the registry write / compose-up, so the redeploy
  endpoint hits a half-set-up state.
- **Impact**: log noise only. The tenant still comes up healthy because
  provision.py finishes its own compose-up in parallel and the redeploy
  retry on next push works fine. Not blocking signups.
- **Root cause (suspected)**: redeploy endpoint looks up the tenant in
  the registry (which hasn't been written yet) or the tenant compose
  dir (still being templated). Probably a `KeyError` or `FileNotFoundError`.
- **Fix**: gate the endpoint on `tenant.status == "live"`. If
  provisioning's still in flight, return 425 Too Early or 202 with a
  "queued" body. Subprocess (deploy.sh) shouldn't even start.
- **Tests to add**: extend `signup-service/test_redeploy.py` with a
  race-condition guard test (registry missing slug → 425 not 500).
- **Owner**: unassigned
- **Opened**: 2026-05-14 after the first end-to-end smoke test passed.

---

## Closed

*(empty — first issue file)*
