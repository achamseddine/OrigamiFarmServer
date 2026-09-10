"""Password hashing, shared by the two credential paths.

Both the platform console (app/platform/auth_routes.py) and the FarmOS
tablet (app/farmos/routes_auth.py) authenticate against the same
user_identity.password_hash column, so the hashing lives here rather than
inside either one. bcrypt's own salt handling and constant-time compare
are the whole implementation — never reach for a plain digest.
"""

from __future__ import annotations

import bcrypt


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False
