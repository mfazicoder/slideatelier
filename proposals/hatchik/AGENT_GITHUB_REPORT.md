# Agent report — GitHub-per-tenant + AI-tool handoff

Scaffolding for the "now what?" path after a Hatchik signup: customer
gets a sandbox + a private GitHub repo with the rendered substrate +
an AI_CONTEXT.md file + a follow-up walkthrough email explaining how
to plug their sandbox into Claude Code / Cursor / Windsurf.

## Files changed

- `proposals/hatchik/sandbox-orchestrator/provision.py`
  - New imports: `github_repo` module + `GITHUB_ORG`
  - `render_substrate` now uses `GITHUB_ORG` in the `REPO_URL`
    placeholder and writes `AI_CONTEXT.md` at the tenant repo root
  - New helpers: `write_ai_context`, `_extract_env_value`,
    `first_prompt_template`, `send_walkthrough_email`, `_html_escape`
  - `main()` adds steps 8 (create GitHub repo + push) and 9 (send
    walkthrough email); both resilient — failures are logged and
    provisioning still marks the tenant `live`
  - `--github-username` CLI arg added for manual provisioning
  - Registry now stores `repo_url` and `github_username` on the tenant
- `proposals/hatchik/sandbox-orchestrator/github_repo.py` (new)
  - `create_tenant_repo(slug, target, product_name, idea, github_username)`
  - Calls GitHub REST (`/orgs/{org}/repos`, repo collaborator PUT)
  - Initialises git in the tenant dir, gitignores `.env`, pushes the
    initial commit, then strips the PAT from the remote URL
  - Skips gracefully when `HATCHIK_GITHUB_TOKEN` is not set
- `proposals/hatchik/signup-service/main.py`
  - Adds `re` import
  - Additive migration: `signups.github_username TEXT`
  - `SignupRequest` accepts + validates `github_username`
  - INSERT statement persists it
  - `UpdateMeRequest` allows PATCH of `github_username` (with empty
    string meaning "clear")
  - `GET /api/account/me` returns `github_username` and, per sandbox,
    `repo_url` (read out of the orchestrator registry)
- `proposals/hatchik/start.html`
  - Optional GitHub username field with pattern validation
  - Client validation + JSON body now includes `github_username`
- `proposals/hatchik/account.html`
  - Settings tab gains a "Connect GitHub" card with form + status line
  - Sandbox card surfaces `repo_url` (text + "Open repo" button)
  - Bootstrap populates the GitHub field; submit handler PATCHes
    `/api/account/me`
- `proposals/hatchik/FIRST_CUSTOMER_RUNBOOK.md`
  - New "GitHub handoff (Sandbox tier, automated)" section above the
    Linear bootstrap: required env vars, the customer journey,
    AI_CONTEXT.md scope, what to do when token isn't set, follow-up
    daemon flag

## Decisions / assumptions

1. **No real OAuth.** Per task spec, the customer's GitHub handle is
   typed in (wizard or account settings) and the orchestrator invites
   them via the `/repos/{org}/{repo}/collaborators/{user}` PUT
   endpoint. This means the customer must accept the GitHub invite
   email — no transparent SSO. The walkthrough email implicitly tells
   them to "open your repo" which surfaces the invite.
2. **Org-owned, not user-owned.** All repos land under
   `HATCHIK_GITHUB_ORG` (default `hatchik-sandboxes`). Even with a
   real OAuth flow, hosting the repo in the customer's namespace
   complicates billing + the "Hatchik redeploys on push" story
   (we'd need their webhook on a private repo we don't control).
   Customers get admin rights via collaborator invite.
3. **PAT-based push** with `x-access-token` basic auth. After
   pushing, the remote URL is rewritten to remove the token so the
   tenant's `.git/config` (which lives on the sandbox host) doesn't
   store a long-lived credential.
4. **.env is gitignored before the initial commit** — service-role
   JWT and Postgres password must never reach GitHub. The
   AI_CONTEXT.md file only exposes the public anon key + sandbox URL.
5. **Walkthrough email gated on `repo_url`** — if GitHub is
   misconfigured, the customer doesn't get a misleading email
   telling them to clone a repo that doesn't exist.
6. **PATCH `/api/account/me` affects future repos only.** Surfacing
   "your existing repo doesn't yet have you as a collaborator" was
   out of scope; the runbook documents the manual repair path.

## Known limitations / follow-ups

- **Redeploy daemon not built.** The walkthrough email says "Hatchik
  picks up your push and redeploys automatically", which isn't yet
  true. Flagged in the runbook as a follow-up. Until then, a customer
  push requires manual `git pull && docker compose up -d` on the
  tenant dir.
- **Account dashboard "Re-invite" button absent.** If a customer fills
  in their GitHub handle after the sandbox is already provisioned,
  the dashboard PATCH stores the handle but nothing back-fills the
  collaborator invite. A small endpoint
  (`POST /api/account/sandboxes/{slug}/github-invite`) would close
  the loop — not built here to keep scope tight.
- **Substrate template AI_CONTEXT.md path assumption.** The
  generated AI_CONTEXT.md references `apps/web/src/product/` and
  `apps/api/src/product/` as the "edit here" directories. If the
  substrate template's directory layout changes, this file's
  guidance drifts. Worth either (a) sourcing the boundaries from a
  `substrate.json` manifest in the template or (b) version-pinning
  the AI_CONTEXT.md generator to the substrate template version.
- **No GitHub-rate-limit handling.** A burst of signups could hit the
  authenticated REST rate limit (5,000/hr). Provision.py logs the
  4xx and proceeds; AI_CONTEXT.md still lands locally.
- **PAT secret rotation** is out of band — the runbook tells the
  operator to set `HATCHIK_GITHUB_TOKEN` in
  `/opt/hatchik-orchestrator/.env` once. Production deployment will
  want this in a secrets store and rotated regularly.
- **GitHub `repo_url` injection into substrate placeholder.** The
  REPO_URL substitution happens during `render_substrate` *before*
  the repo is actually created on GitHub. The URL is deterministic
  (org + slug) so this is fine even if repo creation fails — the
  customer just sees a placeholder URL inside their substrate that
  doesn't resolve until the operator runs the manual repair path
  documented in the runbook.
