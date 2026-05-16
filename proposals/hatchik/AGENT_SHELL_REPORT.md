# Agent shell report — per-email sandbox cap + Launch/Growth shell

Run on branch `claude/cranky-nash-5e9af5` from the isolated worktree
`worktree-agent-a0d36a7936716c4e6` (synced to the cranky-nash tip
before work began). The substrate-template is a nested git repo;
changes to it were made in-place in its own working tree.

## Files touched

### Main repo (proposals/hatchik/)

| File | Change |
|---|---|
| `signup-service/main.py` | Added `_count_active_sandboxes()` helper + cap enforcement in `POST /api/signup`; lifted `JSONResponse` import to the top. |
| `start.html` | New red banner `#wizardSandboxExists`; refactored `showError()` to render the 409 message with a `/delete-sandbox` link; reset button state on error. |
| `index.html` | Mirror of the above on the inline marketing form (`#signupSandboxExists` banner; 409 handler). |
| `FIRST_CUSTOMER_RUNBOOK.md` | New "One active Sandbox per email" subsection under the account harness; new "Launch/Growth shell" section before "When you hit problems". |
| `sandbox-orchestrator/provision.py` | Added `PRODUCT_IDEA` to `TEMPLATE_VARS` and `subs`; tagline derivation (first sentence, 160 chars); appended `VITE_PRODUCT_IDEA` + `VITE_BUILT_WITH_HATCHIK` to tenant `.env`. |
| `AGENT_SHELL_REPORT.md` | This file. |

### Substrate-template (nested git repo, branch `main`)

| File | Change |
|---|---|
| `apps/web/src/routes/index.tsx` | Full rewrite — public marketing landing for signed-out visitors (hero, three feature cards, three-step "how it works", accordion FAQ, footer with privacy/terms + conditional "Built with Hatchik"). Signed-in branch unchanged. Uses `VITE_PRODUCT_NAME` and `VITE_PRODUCT_IDEA`. |
| `apps/web/src/routes/account.tsx` | New end-user account dashboard with Profile / Security / Billing / Sessions / Danger zone tabs. |
| `apps/web/src/routes/__root.tsx` | Added "Account" link in signed-in header nav (between sign-in and Billing). |
| `packages/db/migrations/0011_user_profiles.sql` | New migration creating `public.user_profiles` with RLS for self-only read/write/delete; uses the existing `substrate.touch_updated_at()` trigger. Idempotent. |

## Design decisions

1. **409 body shape**. FastAPI's `HTTPException(detail=...)` wraps the
   body in `{"detail": ...}`. The spec wanted the literal shape
   `{ok, error, message}`, so I switched to returning a `JSONResponse`
   from the `/api/signup` handler for the cap-exceeded path. Annotated
   the function return type with `Any` and dropped `response_model`
   (it doesn't apply to direct `Response` returns anyway).

2. **What counts as "active"**. The cap considers a signup active when
   `signups.status NOT IN ('deleted','cancelled','archived_purged')`.
   I cross-reference the registry: tenants whose registry status is
   `'decommissioned'` don't count even if their signups row is still
   marked `'new'` (paranoia for status drift during decommission).
   Signups that never made it into the registry — e.g. provision
   crashed before the registry write — still count, otherwise outage
   windows would let customers spam sign-ups.

3. **Existing-sandbox URL in the message**. The 409 message tells the
   customer where their existing sandbox is so they can recognise it.
   Falls back to a generic phrase if the registry lookup turns up no
   URL.

4. **Banner UX**. Both the wizard (`start.html`) and the inline form
   (`index.html`) get a dedicated `#…SandboxExists` red banner. I kept
   the existing thin `#…Error` element for validation errors (short
   inline text) and routed the 409 to the new banner because the
   message is multi-line and needs an actionable link. The new banner
   uses `rose-200/rose-50/rose-800` Tailwind tokens to match other
   destructive surfaces on the marketing site.

5. **Marketing template tagline**. `provision.py` takes the customer's
   idea, picks the first sentence, trims to 160 chars, and surfaces
   it through `VITE_PRODUCT_IDEA`. Customers will typically rewrite
   it — but day one it reads as a real tagline, not "Hello, world."
   I considered piping through the raw description, but raw text
   from a textarea can run multiple paragraphs; a tagline shape is
   load-bearing for a hero.

6. **Built-with-Hatchik gating**. The footer link is on by default
   (Sandbox = free tier = fair to ask for organic referral) and
   gated by `VITE_BUILT_WITH_HATCHIK !== 'false'`. Launch/Growth
   provisioning can flip the var post-deploy without redeploying
   the substrate.

7. **Billing tab stub**. The spec says to stub `/api/billing/portal`
   if Stripe isn't wired. Since the customer's API is a separate
   FastAPI server (`apps/api/`) and the task brief says "don't break
   existing routes", I did **not** add a stub endpoint to the API —
   the `BillingTab` calls the route and surfaces the error verbatim
   if it 404s. When `VITE_STRIPE_PUBLISHABLE_KEY` is unset, the tab
   renders the "Billing not configured yet — your founder will set
   this up before launching" copy instead, so the customer sees
   coherent UX in the (current) default state. **Open question 1**.

8. **Session listing**. Supabase's `auth.admin.listUserSessions()`
   is server-only (needs the service-role key, which we don't ship
   to the browser). The Sessions tab therefore shows the current
   session card only, with a "Sign out from other devices" button
   that calls `supabase.auth.signOut({scope:'others'})`. A real
   listing needs a server endpoint that wraps the admin API. **Open
   question 2**.

9. **Delete-account scope**. The Danger zone deletes the user's row
   from `public.user_profiles` (RLS-scoped) and signs them out, but
   doesn't purge the `auth.users` row — that requires the service-
   role key. I chose this scope rather than calling the unsafe
   client-side admin path; the founder can add a server endpoint
   when they need full-purge. **Open question 3**.

10. **Account page vs Settings page**. Both routes now exist. `/account`
    is the new end-user dashboard (Profile / Security / Billing /
    Sessions / Danger). `/settings` is the existing app-level
    preferences (display name + locale + timezone). I considered
    consolidating but the brief explicitly says "don't break existing
    routes", and the two pages address different concerns. The header
    nav has both links.

11. **Migration numbering**. Existing migrations stop at `0001`. The
    spec asks for `0011_user_profiles.sql`. I created it at 0011 as
    specified — the gap implies migrations 0002–0010 are reserved
    for future substrate additions, which matches the convention
    documented in `CLAUDE.md` ("Your product migrations start at
    0010 and beyond" → the spec is asserting 11 as the next
    substrate slot).

12. **British English / friendly tone**. All customer-facing copy
    uses British spelling and conversational phrasing
    ("organise", "tyres", "have a play"). Status messages avoid
    exclamation marks. Destructive confirmations require typing the
    email — pattern lifted from GitHub's destructive flows.

## Open questions

1. **Server-side `/api/billing/portal` stub.** Should we add a real
   stub endpoint to `apps/api/app/billing/` returning a placeholder
   URL, so the Billing tab can demonstrate the round-trip? Right
   now if Stripe is configured but the endpoint is missing, the tab
   surfaces "HTTP 404" — not catastrophic, but rough. Suggest
   adding it in a follow-up: 10-line FastAPI route that returns
   `{"url": f"{STRIPE_BILLING_PORTAL_BASE}/login_token=stub"}`.

2. **Server-side session listing endpoint.** Wrapping
   `supabase.auth.admin.listUserSessions(user_id)` server-side would
   give the Sessions tab a real list with device fingerprints. Worth
   the effort once the customer's API has more than just the
   substrate-stub routes.

3. **`auth.users` purge endpoint.** The Danger zone is half-honest:
   it kills the profile + session but the auth record persists.
   Founders building products with stricter retention requirements
   (GDPR / CCPA) will need the full purge. Pattern: server-side
   route, admin SDK, calls `supabase.auth.admin.deleteUser(user_id)`.

4. **Substrate-template gitlink not registered as a submodule.**
   The parent repo tracks `proposals/hatchik/substrate-template`
   as a `commit` entry (mode `160000`) but there's no `.gitmodules`
   declaration. I didn't add one — it would change how the worktree
   resolves the directory and risk breaking the existing
   provisioning paths. If the user wants the substrate-template
   changes to flow into reviewers' worktrees automatically, a
   `.gitmodules` registration is the right follow-up.

5. **Index.html mentions 'plan: sandbox' rather than 'tier'.** The
   inline form posts `plan: 'sandbox'`, not `tier: 'sandbox'` — the
   server defaults to sandbox tier when no `tier` field is present
   (Pydantic field default), so the cap fires correctly today. Worth
   normalising in a follow-up, but I left it alone to avoid touching
   adjacent code.

## Verification

- `python3 -c 'import ast; ast.parse(open("signup-service/main.py").read())'` — passes
- `python3 -c 'import ast; ast.parse(open("sandbox-orchestrator/provision.py").read())'` — passes
- TanStack Router patterns match the existing `settings.tsx` /
  `login.tsx` style; `routeTree.gen.ts` regenerates at dev/build.
- No new npm dependencies added (account.tsx uses React, TanStack
  Router, supabase-js, the existing `cn` util).
- Existing routes `/login`, `/billing`, `/settings` untouched.

## Not done (deliberately)

- **No deploy.** Per instructions.
- **No commit / PR.** The user reviews + merges.
- **No tests added.** The task didn't ask, and the marketing-site +
  signup-service files don't have an existing test suite in this
  repo. The substrate-template has none either.
