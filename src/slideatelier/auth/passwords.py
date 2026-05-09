"""Password hashing — bcrypt directly (the library, not the passlib wrapper).

We tried passlib first but its bcrypt backend touches `bcrypt.__about__`,
which was removed in bcrypt 5.x — passlib raises ValueError on import-time
backend probing under Python 3.14. The bcrypt package itself has a stable,
small surface (`hashpw`, `checkpw`, `gensalt`) so we use it directly.
"""
from __future__ import annotations

import bcrypt

# Cost = 12 is the conventional default for 2024+. Verify takes ~250ms on a
# laptop CPU which is the right ballpark — fast enough for a sign-in flow,
# slow enough to make brute force expensive.
_BCRYPT_ROUNDS = 12

MIN_PASSWORD_LEN = 8
MAX_PASSWORD_LEN = 256  # rough sanity bound; bcrypt itself caps at 72 bytes.


def _to_bytes(s: str) -> bytes:
    # bcrypt's algorithm operates on at most 72 raw bytes; truncate UTF-8 to
    # match passlib's historical behaviour so existing test vectors line up.
    raw = s.encode("utf-8")
    return raw[:72]


def hash_password(plaintext: str) -> str:
    if not isinstance(plaintext, str):
        raise TypeError("password must be a string")
    salt = bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
    return bcrypt.hashpw(_to_bytes(plaintext), salt).decode("utf-8")


def verify_password(plaintext: str, hashed: str) -> bool:
    if not plaintext or not hashed:
        return False
    try:
        return bcrypt.checkpw(_to_bytes(plaintext), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        # Malformed hash → treat as failed verify rather than 500-ing.
        return False


def validate_password_strength(plaintext: str) -> str | None:
    """Return None if OK, else a human-friendly error message."""
    if not plaintext:
        return "Password is required."
    if len(plaintext) < MIN_PASSWORD_LEN:
        return f"Password must be at least {MIN_PASSWORD_LEN} characters."
    if len(plaintext) > MAX_PASSWORD_LEN:
        return f"Password is too long (max {MAX_PASSWORD_LEN} characters)."
    if plaintext.lower() in {"password", "12345678", "qwertyui", "letmein!"}:
        return "That password is too common — pick something less guessable."
    return None
