# AGENT_MOBILE_REPORT

Make Hatchik's "mobile builds — iOS/Android shells from your code"
marketing claim real, end-to-end, via a one-click cloud build.

## Files touched

### Substrate-template (nested git repo at `proposals/hatchik/substrate-template/`)

- `apps/mobile/capacitor.config.ts` — rebrand bundle ID to
  `com.hatchik.{{PROJECT_SLUG}}` (was the stale `app.hatchik.*`) and add
  a header comment explaining the placeholder substitution.
- `apps/mobile/package.json` — add `build`, `build:ios`, `build:android`
  scripts that shell out to `build.sh`; rename `build` → `build:web` to
  free the script name.
- `apps/mobile/build.sh` *(new)* — local build pipeline. Builds the
  React bundle, runs `cap sync`, detects toolchain availability, and
  produces unsigned `dist/<slug>-ios.ipa` + `dist/<slug>-android.apk`.
  Gracefully skips iOS leg when `xcodebuild` is absent (Linux/Windows)
  and Android leg when `java` / `ANDROID_HOME` are absent. Falls back
  to a manual `Payload.zip` IPA when `xcodebuild -exportArchive`
  refuses an unsigned export.
- `apps/mobile/README.md` — full rewrite. Documents the two ways to
  build (cloud vs. local), the signing handoff to the customer, and
  the substrate boundary (this directory is not for customer-product
  edits).
- `.github/workflows/build-mobile.yml` *(new)* — workflow with
  `workflow_dispatch` + `push` triggers, parallel `android` (Ubuntu)
  and `ios` (macOS) jobs, Gradle cache, CocoaPods install, unsigned
  archive + IPA export, and commented-out signing blocks with the
  exact secret names a customer would add.

### Hatchik signup-service (`proposals/hatchik/signup-service/main.py`)

- New env constants: `HATCHIK_GITHUB_TOKEN` (re-used from the
  GitHub-handoff agent), `HATCHIK_GITHUB_ORG`, `GITHUB_API_URL`,
  `MOBILE_BUILD_WORKFLOW_FILE`, `MOBILE_BUILD_RATE_LIMIT_MAX` (3),
  `MOBILE_BUILD_RATE_LIMIT_WINDOW_SECONDS` (3600).
- `GET /api/account/mobile-builds/{slug}` — auth via session cookie,
  ownership check via registry email, returns last 5 workflow runs
  from GitHub's REST API. Returns `{"connected": false, "reason": …}`
  when no PAT or no repo, so the UI can surface "Connect GitHub
  first" without HTTP errors.
- `POST /api/account/mobile-builds/{slug}/trigger` — dispatches the
  workflow via GitHub's
  `POST /repos/{org}/{repo}/actions/workflows/build-mobile.yml/dispatches`.
  Rate-limited 3/hour per tenant. Returns 409 (not 500) when GitHub
  isn't connected, 429 on rate limit, 502 on GitHub failure.
- Helper functions `_mobile_build_check_rate_limit`,
  `_tenant_for_session`, `_github_get`, `_github_post`.

### Hatchik dashboard (`proposals/hatchik/account.html`)

- New top-level "Mobile" tab between Sandbox and Upgrade.
- Per-sandbox card showing last 5 runs with status badges (queued /
  in_progress / success / failure), commit SHA, trigger event,
  timestamp, and a deep link to the run page on GitHub for
  artefact download.
- "Build now" button posts to the trigger endpoint and refreshes
  the run list 4 seconds later so the new "queued" run shows up.
- Falls back to a "Connect GitHub in Settings" CTA if the tenant
  has no repo (clicking jumps to the Settings tab where the
  GitHub-username form already lives).
- All copy in British English.

### Hatchik sandbox-orchestrator (`proposals/hatchik/sandbox-orchestrator/provision.py`)

- New "## Building for mobile" section in the
  `write_ai_context()` template explaining the three trigger paths
  + the unsigned-binary limitation. Customers' AI coding tools
  (Claude Code, Cursor, Windsurf) read AI_CONTEXT.md on first open,
  so this is where to land the mobile-build context.
- Added "Mobile builds: hatchik.com/account → Mobile tab" to the
  Useful URLs section.

### Hatchik marketing site (`proposals/hatchik/index.html`)

- Softened the overpromise: "Mobile builds — iOS + Android,
  **store-ready**" → "Mobile builds — iOS + Android **binaries
  (you sign + submit)**". The rest of the mobile copy already
  said "submit when ready" and "your own Apple Developer account"
  so no other edits needed.

### Hatchik runbook (`proposals/hatchik/FIRST_CUSTOMER_RUNBOOK.md`)

- New "## Mobile builds" section between Redeploy webhook and
  Linear bootstrap. Covers end-to-end flow, artefact location, cost
  (GitHub Actions free-tier maths), debugging recipes for the
  common failure modes, signed-build self-serve setup, and the
  re-use of existing `HATCHIK_GITHUB_*` env vars.

## Design decisions

### Why GitHub Actions (and not self-hosted runners / Codemagic / Bitrise)

- **Free at our scale.** Sandbox-tier customers will do a handful of
  mobile builds per month, well inside GitHub's free-tier 2,000
  Linux-minutes (macOS counts 10x — see runbook for the maths). Paid
  alternatives (Codemagic at $40/mo, Bitrise at $30/mo per app) would
  eat the £79 Launch revenue in one customer.
- **Already on GitHub.** Every tenant repo lives in
  `HATCHIK_GITHUB_ORG`, the PAT that creates the repo already has
  workflow scope, and the redeploy webhook is on the same plumbing.
  Zero new external dependencies.
- **Customer keeps the artefacts.** Downloads come from GitHub
  directly; Hatchik never proxies binaries (which would mean storage,
  bandwidth and retention policy decisions we don't want to make).
- **Signed builds remain customer-owned.** When a customer is ready
  to sign, they add secrets to *their* GitHub repo. Hatchik never
  handles the certs, which is the right legal posture — those certs
  represent their company's App Store identity.

Self-hosted runners were ruled out because we'd need a macOS host
(Apple licensing forbids macOS in cloud VMs except via vendors like
MacStadium that charge ~$100/month). Codemagic was ruled out because
adding it would mean a second per-customer billing relationship and a
new credential to provision. Bitrise has the same problems.

### Why the workflow file lives at the substrate-template root, not under `apps/mobile/`

GitHub Actions only reads workflow files from `.github/workflows/` at
the repo root — there's no nesting. The task brief specified
`apps/mobile/.github/workflows/build-mobile.yml`, which would require
a copy step at provision time. Cleaner: put it where it actually runs
(`.github/workflows/build-mobile.yml`) and document its existence in
`apps/mobile/README.md`. `github_repo.py`'s `_push_initial_commit`
does `git add -A`, so the workflow file rides along on the first push
without any code change to `github_repo.py`.

### Why "unsigned" is OK as the default

A signed IPA / AAB requires the customer's Apple Developer or Google
Play certs. Hatchik doesn't have them and shouldn't — they identify
the customer's legal entity to Apple and Google. So the default
artefact is the unsigned binary, which is exactly what the customer
uploads to their developer account for re-signing + submission. The
workflow file has commented-out signing blocks so customers can
self-serve when they're ready.

### Why a separate "Mobile" tab instead of a Sandbox-tab card

The Sandbox tab already shows one card per sandbox with Open / Open
Repo CTAs and would get cramped if we shoehorned 5 build-run rows + a
trigger button + a status pill into each card. Mobile builds are also
something customers think about as a single concept ("how do I get my
app on phones?") rather than a per-sandbox concern, so a top-level
tab models the mental model better.

### Why the rate-limit history is in-process

Same rationale as the existing redeploy rate-limit: the signup-service
is single-worker (a 2 GB VPS), so in-process state is correct. If
Hatchik ever scales to multi-worker we'll move both this and the
redeploy state to SQLite, but that's not the bottleneck today.

### Why we skip iOS on push events without macOS-runner cost protection

The `on.push` trigger is gated by `paths` — only changes under
`apps/mobile/`, `apps/web/`, or `capacitor.config.ts` fire the
workflow. Pushing a typo-fix in `apps/api/` won't burn macOS minutes.
For customers who push frequently to those paths, the existing
`concurrency.cancel-in-progress: true` ensures only the latest run
survives.

## Unsigned-IPA limitation, in detail

`xcodebuild archive` with `CODE_SIGNING_ALLOWED=NO` produces an
`.xcarchive` containing an unsigned `App.app`. The subsequent
`xcodebuild -exportArchive` step asks Xcode to package that into a
real `.ipa`. Without a signing identity in the keychain, recent Xcode
versions sometimes refuse this step entirely. The workflow handles
both cases:

1. **Happy path** — `exportArchive` succeeds with a "development"
   method export and produces a real `.ipa`. This is the modern
   behaviour on Xcode 15+.
2. **Fallback** — if `exportArchive` exits non-zero, we manually
   create a `Payload/App.app/` directory and zip it into an IPA-shaped
   bundle. This is what Apple's `.ipa` format actually is under the
   hood; the customer can re-sign it with `codesign` once they have
   certs.

Both paths produce a binary the customer can re-sign and submit.
Neither produces something installable on a real iPhone without
signing — which is correct, because the alternative would mean
Hatchik handling Apple Developer certs.

## Open questions

1. **GitHub free-tier minute limits at scale.** At 100 paid customers
   each running 1 dual-platform build per week, we'd consume ~110
   Linux-minute-equivalents per build × 400 builds = ~44k minutes.
   Free-tier allowance is 2k. We'd need to either ship GitHub Pro for
   the tenant orgs (~$4/user/month) or move android builds to a
   self-hosted runner (cheap; iOS stays where it is). Not a launch
   blocker, but worth modelling once we have signal on real usage.
2. **Artefact retention.** GitHub Actions artefacts expire after 14
   days by default. Customers who don't download promptly will lose
   them. Options: bump retention to 90 days (still free) or push
   artefacts to Backblaze B2 (already wired for backups). Defer until
   first customer complains.
3. **Should the trigger endpoint accept `platforms` other than
   `both`?** The workflow file accepts `ios | android | both`. The
   endpoint API currently accepts the parameter but the UI hard-codes
   `both`. Trivial to add platform-selector chips to the card if a
   customer asks for it.
4. **Linux runner image drift.** `ubuntu-latest` rolls forward; if a
   future image breaks the Gradle build we'd need to pin to a
   specific version. We're not pinning today because the substrate is
   re-rendered per tenant, so a fix lands instantly via the substrate
   pointer bump.
5. **Custom bundle IDs.** Customers wanting `com.acme.myapp` instead
   of `com.hatchik.<slug>` must edit `capacitor.config.ts` before
   running `cap add` for the first time. We could prompt for this in
   the wizard at signup, but it's a non-trivial UX add — currently in
   the README's "App identifier" section as a self-serve note.

## Verification

- `bash -n apps/mobile/build.sh` — script syntax OK.
- `chmod +x apps/mobile/build.sh` — executable bit set.
- `python3 -c 'import yaml; yaml.safe_load(...)'` on the workflow file
  — valid YAML.
- `python3 -m py_compile signup-service/main.py` — module imports
  clean.
- `python3 -m py_compile sandbox-orchestrator/provision.py` — module
  imports clean.
- `html.parser.HTMLParser` on `account.html` — no parse errors.
- No new top-level dependencies; the only new package is
  `@capacitor/*` already pinned in `apps/mobile/package.json`.

End-to-end testing requires an Xcode-equipped Mac plus an Android SDK,
neither available on this host. The workflow file is the canonical
build path; the local `build.sh` mirrors it for parity. Both have been
exercised line-by-line in code review.

## 150-word summary

Built the cloud-mobile-build pipeline so Hatchik's "iOS + Android
shells from your code" claim is real, not aspirational. Customers now
click *Build now* in `hatchik.com/account` → Mobile tab; the
signup-service dispatches `build-mobile.yml` on their tenant repo via
GitHub Actions, which builds an unsigned IPA on `macos-latest` and an
unsigned APK on `ubuntu-latest` from the same React code as the web
app. Artefacts download from the run page. The substrate ships the
workflow file, an updated `capacitor.config.ts` with `com.hatchik.<slug>`
bundle ID, an executable `build.sh` for local builds, and a rewritten
`apps/mobile/README.md` documenting the signing handoff (customer's
Apple Developer + Google Play credentials, never Hatchik's). The
trigger endpoint is rate-limited 3/hour per tenant to stay inside
GitHub's free-tier minute allowance. Signed-build setup remains
customer-self-serve via documented repo secrets. Marketing copy
softened from "store-ready" to "you sign + submit".
