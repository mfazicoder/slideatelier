# GitHub-invite agent — handle validation + on-demand re-invite

Close two real-customer gaps in the GitHub-per-tenant flow:

1. The wizard accepted any regex-shape-valid handle, so customers who
   pasted their product name ("myidea") ended up with a private repo
   they couldn't see — the collaborator invite silently 404'd.
2. PATCH `/api/account/me` saved a corrected handle but never re-fired
   the invite. The repo invite was one-shot at provision time.

## Files touched

### Server

- `proposals/hatchik/signup-service/main.py`
  - **New helper** `_github_user_exists(handle)` — async GET
    `/users/{handle}` against the GitHub API with a 1.2s timeout.
    Returns `(exists, reason)`. **Fail-open** on timeout, 5xx, 403
    rate-limit, missing PAT, or any network error. Only the explicit
    404 path rejects the signup.
  - **POST `/api/signup`** gains a handle-existence gate that runs
    after Turnstile + geo + disposable-email checks and before the DB
    insert. On `not_found`, returns 422 with
    `{ok:false, error:"github_user_not_found", message:"…not your
    product name."}` exactly as the spec asked. Empty handles skip
    the check entirely.
  - **New helper** `_invite_github_collaborator(slug, handle)` —
    interprets GitHub's PUT-collaborator response. 201/204 →
    `invitation_sent`; 304 / 422 "already a collaborator" →
    `already_collaborator`; 403 → `forbidden` with a
    `FOUNDER_NOTIFY:` journalctl-grep-friendly log line; 404 →
    `not_found`; everything else → `upstream_error`.
  - **New helper** `_github_invite_check_rate_limit(email)` — in-process
    sliding window, capped at 5 re-invites per email per hour
    (`GITHUB_INVITE_RATE_LIMIT_MAX` / `..._WINDOW_SECONDS`).
  - **New endpoint** `POST /api/account/sandboxes/{slug}/github-invite`
    — session-cookie auth, owner-checked via `_tenant_for_session`,
    reads the customer's most recent non-empty `github_username` from
    `signups`, fires the invite. Owner-mismatch returns 403, missing
    handle 400, GitHub 404 surfaces a clear "handle doesn't exist"
    message, 403 surfaces "founder is on it".
- `proposals/hatchik/sandbox-orchestrator/github_repo.py`
  - `_invite_collaborator(slug, github_username)` now recognises 304
    and 422-already-a-collaborator as success, and logs
    `FOUNDER_NOTIFY:` on 403/404 so the existing provision flow
    doesn't surface confusing "invite failed" errors when nothing
    actually needs to happen.

### Client

- `proposals/hatchik/start.html` — sub-label under the `githubUsername`
  input rewritten to "Your GitHub username, not your product name. Used
  to give you owner access on the repo we create for you. Skip this and
  we'll just email you the repo link."
- `proposals/hatchik/index.html` — inline signup form gains a
  `signupGithub` input (it had no GitHub field previously) with the
  same sub-label, plus client-side regex validation in the submit
  handler, plus `github_username` in the request body sent to
  `/api/signup`.
- `proposals/hatchik/account.html`
  - Settings → Connect GitHub: sub-label updated for the same
    not-your-product-name copy.
  - Settings → Connect GitHub save handler now PATCHes
    `/api/account/me` (unchanged), then for *each* active sandbox with
    a `repo_url`, POSTs `/api/account/sandboxes/{slug}/github-invite`
    if the handle changed (vs. the cached `/api/account/me` payload).
    Inline status: "Sent owner invite to {handle} for {N} sandbox(es).
    Check your GitHub email to accept." with graceful partial-failure
    and full-failure copy.
  - Sandbox tab card renders an **amber banner** when `repo_url` is
    present but `github_username` is empty, linking back to Settings.
    The repo link + "Open repo" button hide in that state — if the
    customer can't open it, surfacing it as a tappable link is just
    a frustration vector.
  - `renderSandboxes` re-runs from the GitHub save handler so the
    banner disappears immediately on save (no full page reload
    required).

### Tests

- `proposals/hatchik/signup-service/test_github_invite.py` — 12 new
  tests, all passing alongside the existing 17:
  - Bogus handle → 422 with the spec'd error body.
  - Empty handle → 201, GitHub never consulted.
  - Real handle (mocked 200) → 201.
  - Fail-open on GitHub-side wobble (mocked `skipped` reason) → 201.
  - Re-invite happy path → 200 `invitation_sent`.
  - Re-invite without a handle on record → 400.
  - Re-invite on someone else's slug → 403.
  - Re-invite when GitHub returns "already a collaborator" → 200.
  - Re-invite when GitHub returns 304 → 200.
  - Re-invite when GitHub 404s the user → 404 with handle in message.
  - Re-invite when GitHub 403s the PAT → 403 with "founder is on it".
  - Re-invite without a session cookie → 401.

## Design decisions

### Fail-open on the inline GitHub validation

The 422 reject is asymmetric: a legitimate signup with a real handle
might get blocked if GitHub's `/users/{handle}` endpoint times out, is
rate-limited, or returns 5xx — that's a much worse outcome than
letting a typo'd handle through (which can be fixed via the new
re-invite endpoint). So `_github_user_exists` returns
`(True, "skipped")` on everything except explicit 200/404 and the
caller treats `skipped` as "advisory pass". The 1.2s timeout keeps the
end-to-end signup latency under 1.5s budget. If we ever see a wave of
false negatives in production, the toggle is one line in
`_github_user_exists`.

### Per-email re-invite rate limit

GitHub-side rate limit on collaborator-PUT is generous, but the
customer-side spam-loop is more interesting — someone could mash Save
in Settings repeatedly. 5/hour/email matches the rest of the
account-API's abuse-protection posture. Per-process state for now;
moves to SQLite alongside `_mobile_build_history` and
`_redeploy_history` when we scale horizontally (they all share that
deferred-fix shape).

### Pre-existing-invite recovery

GitHub returns 422 + "already a collaborator" when the customer
accepted the original invite, 304 when an invite is already pending,
201 for a fresh invite, and 204 in some edge cases. All four roll up
to "the customer has access" — surfacing distinct errors here is
hostile, so we collapse them. 403 (PAT scope issue) and 404 (user or
repo missing) are the only paths that surface real failures.

### Inline-form GitHub field

The brief said `index.html` is "the inline signup form (same field,
simpler version)". The current inline form has no GitHub field at all,
so "same field" means *add it*. I went minimal — `sr-only` label with
placeholder copy, same regex pattern as start.html, same sub-label
hint. Most marketing-page conversions still skip it (it's optional),
but customers who fill it in get the same downstream treatment.

## Open questions

- **Does the founder want the 403/PAT-scope error to email them?**
  Currently it logs `FOUNDER_NOTIFY:` with the customer email + slug
  for journalctl-grep alerting. A Resend hook is one line if you'd
  like real-time pings.
- **Should the amber banner also link directly to the repo's settings
  page on GitHub?** I left it pointing at our Settings tab so the
  customer goes through the re-invite flow rather than poking around
  on github.com without owner access. Reconsider if customers ask.
- **Should `/api/signup` rate-limit the GitHub-validation call
  separately?** Currently it shares the per-IP signup rate limiter
  (5/min/IP). At 1.2s/call, that's ≤6 GitHub API calls per IP per
  minute — well within GitHub's authenticated bucket of 5,000/hour.
  Fine for launch traffic.

## Constraints honoured

- No new Python deps (still httpx + stdlib).
- httpx timeout 1.2s on the inline check, fail-open on wobble.
- HATCHIK_GITHUB_TOKEN never returned to the client or logged.
- Re-invite endpoint rate-limited to 5 per email per hour.
- British English in customer-facing copy.
- Worktree-only, no deploy.
