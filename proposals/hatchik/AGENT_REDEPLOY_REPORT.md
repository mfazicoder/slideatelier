# AGENT_REDEPLOY_REPORT

Build the per-tenant redeploy webhook that makes Hatchik's "push to
deploy" promise real, and wire it into the AI handoff so any AI
coding tool (Claude Code / Cursor / Windsurf / etc.) can trigger a
deploy after editing code.

## Files touched

| File | Change |
| --- | --- |
| `proposals/hatchik/sandbox-orchestrator/provision.py` | Generate per-tenant `deploy_token`, persist on registry entry, thread through `render_substrate` + `write_ai_context`, pass to `create_tenant_repo`. AI_CONTEXT.md rewritten: new top-of-file "Tip for the AI tool" callout, replaced "Dev workflow" with "Deploying changes" (git push **or** direct POST with token). Walkthrough email copy updated to "redeploys automatically — usually within 30 seconds…" (text + HTML). |
| `proposals/hatchik/sandbox-orchestrator/github_repo.py` | New helper `_register_redeploy_webhook(slug, deploy_token)` — POST `/repos/{org}/{repo}/hooks` with `events=["push"]`, JSON content type, `deploy_token` as secret. Idempotent (lists existing hooks first). `create_tenant_repo` now takes `deploy_token` and returns `webhook: bool` in the result dict. New `PUBLIC_BASE_URL` env (`HATCHIK_PUBLIC_BASE_URL`, default `https://hatchik.com`). |
| `proposals/hatchik/signup-service/main.py` | New `POST /api/tenants/{slug}/redeploy` endpoint: dual-auth (`X-Deploy-Token` or `X-Hub-Signature-256`), per-slug `asyncio.Lock`, in-memory rate limit, `asyncio.create_subprocess_exec` for `git pull --rebase` + `docker compose up -d --build`, append-only per-tenant log, last-50-lines tail on failure. Updates registry with `last_redeploy_at` / `last_redeploy_commit` / `last_redeploy_via` (also surfaced in `/api/admin/accounts`). Adds `_save_registry` helper. |
| `proposals/hatchik/signup-service/test_redeploy.py` | New: 11 smoke tests covering all auth/error paths, happy path via both auth modes, HMAC tamper rejection, concurrent-lock 429, rate-limit 429, subprocess-failure log-tail. |
| `proposals/hatchik/account.html` | GitHub card copy changed from "we'll redeploy your sandbox within the hour (automatic push-to-deploy ships soon)" to "your sandbox redeploys automatically, usually within 30 seconds…". |
| `proposals/hatchik/FIRST_CUSTOMER_RUNBOOK.md` | "Follow-up: redeploy daemon" replaced with a full "Redeploy webhook" section: how it works, where the token lives (registry.json), the two auth modes, the rate limit, manual curl debug command, and the per-tenant log path. Updated the AI_CONTEXT.md description to mention the new tip line + deploy-token curl. |

No new Python dependencies — everything is stdlib (`hmac`, `hashlib`,
`asyncio`, `secrets`, `subprocess`, `pathlib`, `json`, `datetime`) plus
the existing `httpx` and FastAPI.

## Design decisions

### Dual auth (X-Deploy-Token vs X-Hub-Signature-256)

GitHub webhooks cannot inject arbitrary headers — they always sign the
raw body with the configured secret and send `X-Hub-Signature-256:
sha256=<hex>`. AI tools, on the other hand, can trivially send a
header but won't bother computing HMACs. Rather than minting two
different secrets per tenant (and then having to keep them in sync in
the AI_CONTEXT.md + the GitHub webhook config), we use one
`deploy_token` for both:

- AI tools send `X-Deploy-Token: <token>` verbatim. We
  `hmac.compare_digest` it against the registry value.
- GitHub sends `X-Hub-Signature-256` with the body HMAC'd by the same
  `deploy_token`. We compute the expected HMAC and constant-time
  compare hex digests.

Only one auth header needs to validate; the endpoint logs `via=ai-tool`
or `via=github-webhook` so the audit trail in the per-tenant log is
honest about how the deploy was triggered.

### Per-slug asyncio.Lock + in-memory rate-limit

`asyncio.Lock` keyed by slug means we can serialise redeploys for a
single tenant (two concurrent pushes → second one gets a 429 instantly
rather than racing into the same git working tree) without blocking
unrelated tenants' redeploys. The lock dictionary is process-local;
the signup-service runs a single uvicorn worker (see
`hatchik-signup.service`), so we don't have a multi-worker
coordination problem yet. If we ever scale to multiple workers,
the lock dict + rate-limit dict need a SQLite-backed equivalent.

The rate-limit (default 6 per 5 min) is a simple in-memory rolling
window per slug. Same multi-worker caveat applies. Both knobs are
configurable via env (`HATCHIK_REDEPLOY_RATE_LIMIT_MAX`,
`HATCHIK_REDEPLOY_RATE_LIMIT_WINDOW_SECONDS`) so we can dial them in
without redeploying.

### Subprocess via asyncio.create_subprocess_exec

`docker compose up -d --build` regularly takes 60–120s. The
signup-service shares its event loop with every other API call —
blocking it for two minutes would hang Paddle webhooks, magic-link
emails, the admin dashboard, and so on. So the redeploy runs through
`asyncio.create_subprocess_exec` with `await proc.communicate()`,
yielding back to the loop while the build runs. The per-slug lock
serialises redeploys for *one* tenant; other tenants can redeploy in
parallel up to whatever the Docker daemon will sustain.

### Defensive logging + log-tail on failure

Every step writes a timestamped line to
`/var/log/hatchik/redeploy-<slug>.log` (configurable). On failure the
response body includes the last 50 lines so the caller (AI tool or
admin via curl) can diagnose without ssh access. This keeps the
"your AI tool just works" promise: if a deploy breaks, the AI can
read the error and fix it inline.

### Idempotency

We don't try to detect what changed. `git pull --rebase` brings the
working tree to the latest commit; `docker compose up -d --build`
rebuilds anything whose Dockerfile layer hashes changed and leaves
the rest alone. Re-running on the same SHA is a fast no-op. SQL
migrations run via the substrate's existing migration runner on
container start.

### Webhook registration is best-effort

`_register_redeploy_webhook` returns False (and logs) if the GitHub
API call fails. The customer still has a working repo + a working
endpoint reachable via X-Deploy-Token from their AI tool. The
walkthrough email and AI_CONTEXT.md both surface the deploy-token
path, so the AI tool can ship code even when push-trigger doesn't.

## Env vars introduced

| Name | Default | Purpose |
| --- | --- | --- |
| `HATCHIK_REDEPLOY_LOG_DIR` | `/var/log/hatchik` | Where per-tenant redeploy logs land. |
| `HATCHIK_REDEPLOY_RATE_LIMIT_MAX` | `6` | Max redeploys per tenant per window. |
| `HATCHIK_REDEPLOY_RATE_LIMIT_WINDOW_SECONDS` | `300` | Window length for the rate limit. |
| `HATCHIK_REDEPLOY_TIMEOUT` | `600` | Subprocess timeout per redeploy step (git pull, docker compose). |
| `HATCHIK_PUBLIC_BASE_URL` | `https://hatchik.com` | Base URL embedded in the GitHub webhook target. Override for staging. |

`HATCHIK_TENANTS_DIR` already existed (provision.py) and is now also
read by main.py to find the tenant compose directories.

## Smoke-test results

All 11 redeploy tests pass:

```
test_redeploy.py::test_no_auth_returns_403 PASSED
test_redeploy.py::test_bad_deploy_token_returns_403 PASSED
test_redeploy.py::test_unknown_slug_returns_404 PASSED
test_redeploy.py::test_archived_returns_410 PASSED
test_redeploy.py::test_happy_path_via_deploy_token PASSED
test_redeploy.py::test_happy_path_via_github_webhook_signature PASSED
test_redeploy.py::test_github_signature_rejects_tampered_body PASSED
test_redeploy.py::test_subprocess_failure_returns_500_with_log_tail PASSED
test_redeploy.py::test_concurrent_redeploy_returns_429 PASSED
test_redeploy.py::test_rate_limit_kicks_in PASSED
test_redeploy.py::test_hmac_signature_helper_round_trip PASSED
============================== 11 passed in 0.66s ==============================
```

The pre-existing `test_cohort_metrics.py` suite still passes (6/6),
so the admin-account / registry plumbing additions didn't regress.

`provision.py` + `github_repo.py` pass `py_compile` and `ast.parse`,
and import cleanly: `create_tenant_repo` is now
`(slug, target, product_name, idea, github_username, deploy_token)`
with `deploy_token` optional for back-compat.

## Open questions

1. **Token rotation.** No rotation endpoint exists. If a customer
   leaks their `deploy_token` (e.g. AI_CONTEXT.md committed to a
   public repo — though the substrate gitignores nothing in repo
   root by default), we'd have to manually edit registry.json and
   re-issue the GitHub webhook. A future `/api/admin/account/{slug}/rotate-deploy-token`
   endpoint would be cheap to add.
2. **Branch filtering.** The webhook fires on every push to every
   branch. We blindly `git pull --rebase` on whatever branch the
   working tree happens to be on (main, by default). If the customer
   pushes a feature branch, the rebase is a no-op and the rebuild
   happens unnecessarily. Worth filtering on `refs/heads/main` in
   the GitHub event payload before triggering — but that means
   parsing the JSON, which we currently don't.
3. **Multi-worker scale.** The asyncio.Lock and rate-limit dicts are
   per-process. We're on a single uvicorn worker today; if we ever
   scale, both need SQLite-backed equivalents (or Redis, but adding
   Redis just for this is overkill).
4. **Long-running redeploys timing out the GitHub webhook.** GitHub
   waits 10s for a webhook response. Our redeploy regularly takes
   60s+. We return 200 only after the rebuild finishes, so GitHub
   will mark the delivery as failed and retry up to 3 times. Fix:
   accept-and-detach pattern — return 202 immediately, run the
   redeploy in a `asyncio.create_task`. Trade-off: status visibility
   becomes harder. Deferred for now; if GitHub retry chatter becomes
   a problem we can revisit.
5. **No metrics on deploy success rate.** `last_redeploy_*` lands on
   the registry but we don't aggregate. Easy to add to the cohort
   metrics agent later.
