# Hatchik sign-in: 6-digit verification code fallback

Agent run on the `worktree-agent-ada960927b98e3f0b` branch (forked from
`claude/cranky-nash-5e9af5`). Magic-link sign-in stays the default; the
code is the fallback for inboxes / corporate proxies that mangle
clickable links.

## Files touched

| Path | Change |
|---|---|
| `proposals/hatchik/signup-service/main.py` | New constant `LOGIN_CODE_MAX_ATTEMPTS = 5`. Additive PRAGMA-gated migration on `login_tokens` adds two columns (`code TEXT`, `code_attempts INTEGER NOT NULL DEFAULT 0`). New helper `_generate_login_code()`. `POST /api/account/login` now mints a 6-digit code alongside the existing magic-link token and persists it on the same row. `_send_login_email` takes an extra `code` arg and includes a `123 456`-formatted block below the button. New endpoint `POST /api/account/login-with-code` with `LoginCodeRequest` pydantic model. |
| `proposals/hatchik/account.html` | New post-submit panel `#signInOptions` with two cards ("click the link" / "enter the 6-digit code"). New JS submit handler for `#signInCodeForm` that POSTs to `/api/account/login-with-code` and navigates to `/account` on 200. Inline error surface for 404/410/429. "Use a different email" restart button. |
| `proposals/hatchik/signup-service/test_login_code.py` | New test file (8 tests): code generation, persistence, success, replay-after-consume, spaced format, no-pending, wrong-code, expired, sixth-attempt-429, post-429-correct-code-fails, magic-link still works, unknown email, malformed code. |

No changes to the wizard / marketing signup flows — this is sign-in only, as
specified.

## Migration

Two new columns on `login_tokens`, both added behind a `PRAGMA table_info`
check so re-running `init_db()` on an already-migrated DB is a no-op
(matches the pattern already used for the `signups` table):

```sql
ALTER TABLE login_tokens ADD COLUMN code TEXT;
ALTER TABLE login_tokens ADD COLUMN code_attempts INTEGER NOT NULL DEFAULT 0;
```

Existing in-flight rows (issued before this deploy) will have `code IS NULL`
— the new endpoint treats those as "no pending sign-in" and returns 404, so
the customer simply requests a fresh email.

## Design decisions

- **6 digits, not 8.** 1-in-900,000 odds with a 5-guess hard cap = effectively
  zero brute-force risk while still being short enough to type/dictate
  comfortably. The codes themselves are emitted as `secrets.randbelow(900000)
  + 100000` so they're always exactly 6 ASCII digits — no leading zeros, no
  ambiguity with phone numbers.
- **Same row, not a separate `code_tokens` table.** The magic link and the
  code are two faces of the same single-use sign-in attempt — consuming
  either has to invalidate the other. Storing both on the same row makes
  that atomic for free; a separate table would have needed a foreign key
  and a multi-statement consume. The schema cost is two nullable columns
  on a low-traffic table.
- **Constant-time compare** via `hmac.compare_digest`. Paranoid for a
  6-digit code (the search space is small enough that timing leaks
  effectively nothing) but it costs nothing and matches the project's
  existing crypto hygiene.
- **`code_attempts` lives on the row, not in a separate rate-limit table.**
  The rate-limit lifetime is exactly the token lifetime (30 minutes) — once
  the row is consumed or expires, the counter is moot. No external state to
  garbage-collect.
- **Sixth attempt returns 429, not the fifth.** Per the spec "5 attempts per
  15-min window" → five guesses each return 410, the sixth returns 429 and
  burns the token. A correct code submitted after the burn returns 404
  (token already consumed), exactly as required.
- **422 on malformed input** (non-digits, wrong length): pydantic validates
  before the handler runs, so attackers can't even spend an `attempts` slot
  on a garbage payload — they have to send a syntactically valid 6-digit
  string. The validator also accepts the visual `123 456` format that
  customers naturally paste from the email.
- **Single endpoint, no preflight.** The page already knows the email
  (stored in `pendingSignInEmail` after the 202 from `/api/account/login`),
  so the code form just needs the digits. On 200 we hard-navigate to
  `/account` so the dashboard re-bootstraps cleanly via the session cookie.
- **British English** in customer copy ("Or copy this code into the sign-in
  form on hatchik.com/account", "Expires in 30 minutes", "If you didn't ask
  to sign in, ignore this email").

## What is *not* in this change

- No new Python dependencies. Existing `secrets`, `sqlite3`, `hmac`,
  pydantic, httpx, fastapi only.
- No change to `start.html` / `index.html` — those are signup, not sign-in.
- No change to `_resolve_session`, `/api/account/me`, `/api/account/logout`,
  or any dashboard endpoint — the session cookie set by the code path is
  byte-identical to the one set by the magic-link path.
- No Turnstile gate on `/api/account/login-with-code`. The customer has
  already passed Turnstile to get the email mailed; the per-token attempt
  cap is the second line of defence.

## Open questions

1. Should the success-banner copy reveal that *two* options exist before
   the user has tried Option 1? The current copy is "we've sent you an
   email with two ways to sign in" — minimal but discoverable. An A/B
   alternative would be "Magic link in your inbox — if it doesn't work,
   use the code below" which is more action-oriented but longer.
2. Should we offer a "resend" button on the options panel? Currently the
   customer has to click "Use a different email" and re-enter the same
   address. Cheap to add if support tickets show it's needed.
3. The Turnstile gate is enforced on `/api/account/login` but not on the
   code endpoint. If we ever see code-guessing attempts logged, we could
   require a Turnstile token on `/api/account/login-with-code` too — the
   widget is already rendered on the page.
4. Email-validator was not available in any reachable virtualenv on this
   host so `pytest test_login_code.py` was not executed locally; the test
   file passes `ast.parse` and follows the same shape as
   `test_redeploy.py`. Reviewer should run the suite before merging.

## Verification checklist (for the reviewer)

- [ ] `cd proposals/hatchik/signup-service && pip install -r requirements.txt pytest && pytest test_login_code.py -v`
- [ ] Open `/account` in the browser, request a sign-in email, confirm the
      message contains a `123 456`-style code block beneath the button.
- [ ] Confirm the same code submitted on `/account` returns 200 and lands
      on the dashboard.
- [ ] Confirm five wrong codes each return 410 and the sixth returns 429.
- [ ] Confirm the magic link in the email still works (clicking lands on
      the dashboard, the row is single-use against both paths).
