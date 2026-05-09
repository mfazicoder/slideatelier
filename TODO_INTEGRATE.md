# TODO_INTEGRATE — wire the invite gate into the auth signup endpoint

The auth agent's signup endpoint at
`src/slideatelier/auth/routes.py` (around lines 121–157, the `post_signup`
handler) does NOT currently call into the invite-validation module. The
invite module is fully implemented and tested under
`src/slideatelier/auth/invites.py` + `tests/test_invites.py`, but the HTTP
glue is missing because the auth signup landed in a parallel agent's
worktree.

## What needs to change

### 1. `src/slideatelier/auth/routes.py` — `post_signup` handler

Add an `invite` form field, validate it BEFORE creating the user, consume
it AFTER successful user creation. Patch sketch (numbers refer to existing
file structure as of this handoff):

```python
# Near the imports (line ~21):
from . import invites
from .invites import (
    InviteError,
    InviteExhausted,
    InviteExpired,
    InviteNotFound,
)

# In post_signup signature (currently ~line 122):
def post_signup(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    invite: str = Form(""),                 # NEW — invite code from the form
    next: str = Form("/workflow"),
):
    email = (email or "").strip().lower()
    next_url = _safe_next(next)
    ctx_email = email

    def _err(msg: str, status: int = 400) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "auth/signup.html",
            {"error": msg, "email": ctx_email, "invite": invite,   # echo back
             "next": next_url},
            status_code=status,
        )

    # Email + password validation (existing block stays):
    if not email or not _EMAIL_RE.match(email):
        return _err("Enter a valid email address.")
    pw_err = validate_password_strength(password)
    if pw_err:
        return _err(pw_err)

    # NEW — invite validation BEFORE we touch the user table.
    try:
        invites.validate_invite(invite)
    except InviteError as e:
        return _err(str(e))

    db = get_db(_output_dir_path())
    if db.get_user_by_email(email) is not None:
        return _err("An account with that email already exists. Try logging in.")
    try:
        user = db.create_user(email, hash_password(password))
    except Exception as e:  # noqa: BLE001
        return _err(f"Could not create account: {type(e).__name__}", status=500)

    # NEW — burn the invite ONLY after the user record exists.
    try:
        invites.consume_invite(invite)
    except InviteError:
        # The invite expired or was exhausted in the few ms between validate
        # and consume (race window). The user record exists; let the signup
        # complete — they have an account, the invite economics are
        # post-hoc enforced. Log and move on.
        pass

    token = db.create_session(user.id)
    response = RedirectResponse(url=next_url, status_code=303)
    _set_session_cookie(response, token)
    return response
```

### 2. `src/slideatelier/web/templates/auth/signup.html`

Add an `<input name="invite">` field. Pre-fill from `{{ invite or '' }}` so
the value survives validation errors (matching the existing email echo).
Suggested placement: between the email and password fields, with copy like
"Invite code (required during private beta)".

### 3. Tests in `tests/test_auth.py` (optional but recommended)

After patching, add a test that the signup form rejects requests with no
invite + accepts a valid one:

```python
def test_signup_requires_invite_code(tmp_path):
    from slideatelier.auth import invites
    invites.create_invite(code="GOLDENTICKET", max_uses=1)
    c = TestClient(app)
    # Missing invite → 400
    r = c.post("/signup",
        data={"email": "a@b.co", "password": GOOD_PW, "invite": ""},
        follow_redirects=False)
    assert r.status_code == 400
    # Valid invite → 303 + session cookie
    r = c.post("/signup",
        data={"email": "a@b.co", "password": GOOD_PW, "invite": "GOLDENTICKET"},
        follow_redirects=False)
    assert r.status_code == 303
    assert "atelier_session" in r.cookies
    # Code now exhausted.
    inv = invites.get_invite("GOLDENTICKET")
    assert inv.used_count == 1
```

## Why we ship invites separately

The invite module (`auth/invites.py`) is in the auth/ package because:
- it shares the same SQLite file (`atelier.db`),
- it shares the AuthDB connection lock to avoid double-locking,
- it lives next to the code that owns the signup HTTP surface.

But it is NOT imported from `auth/__init__.py`. The auth agent's
existing exports stay untouched. That keeps merge surface area small.
Just add the import + form-field + two function calls in `routes.py` and
the matching `<input>` in `signup.html`. ~15 lines of glue total.

## Verification

After patching, all of the following should pass:
- `pytest tests/test_invites.py -q`            (unchanged from this PR)
- `pytest tests/test_auth.py -q`               (existing flow keeps working with a valid invite passed in test data)
- `scripts/smoke_test_deploy.sh --insecure --invite=<code> https://<host>`
