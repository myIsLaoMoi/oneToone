import secrets

from . import db


def new_token() -> str:
    return secrets.token_urlsafe(24)


def new_key() -> str:
    return secrets.token_urlsafe(24)


def new_code() -> str:
    """Six digits, unique among transfers that have not expired yet.

    The code stays usable for the whole retention window, so uniqueness must be
    checked against that same window; checking code_expires_at would let a
    duplicate appear once the (shorter) join window passed.
    """
    for _ in range(50):
        code = f"{secrets.randbelow(1_000_000):06d}"
        row = db.one(
            "SELECT 1 FROM transfer WHERE code = ? AND expires_at > strftime('%s','now')",
            (code,),
        )
        if row is None:
            return code
    raise RuntimeError("no free code available")
