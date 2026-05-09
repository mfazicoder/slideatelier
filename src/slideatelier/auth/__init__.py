"""Authentication & multi-tenant data isolation for slideAtelier.

This package gates the workflow editor behind email+password sign-in and
namespaces each user's output under output/users/<user_id>/.

Design notes:
- SQLite-backed (stdlib sqlite3 — no SQLAlchemy). Single file at
  ${SLIDEATELIER_OUTPUT_DIR}/atelier.db. Tables: users, sessions, decks.
- bcrypt password hashing via passlib.
- Session cookie ("atelier_session"), HttpOnly + SameSite=Lax. Secure flag is
  set when SLIDEATELIER_ENV=production.
- Anonymous (no cookie) access is allowed in dev mode; the existing flat
  output/workflow/<job_id>/ layout is preserved so the user's pre-auth decks
  keep working. Production gates everything behind /login.
- The slug → (user_id, job_id) index lives in the `decks` table — public
  /web/<slug> viewer is intentionally unauthenticated (publishing is the point)
  but resolves through SQL, not through the legacy shared web_slugs.json.
"""
from .db import (  # noqa: F401
    AuthDB,
    User,
    Deck,
    get_db,
    SYSTEM_USER_ID,
)
from .middleware import (  # noqa: F401
    SessionMiddleware,
    get_current_user,
    login_redirect_if_anonymous,
    require_user,
    user_owns_job,
    user_workflow_root,
)
from .paths import (  # noqa: F401
    new_job_dir,
    resolve_job_dir,
    workflow_root_for_request,
)
