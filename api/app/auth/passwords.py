"""Password hashing, shared by the two credential paths.

Both the platform console (app/platform/auth_routes.py) and the FarmOS
tablet (app/farmos/routes_auth.py) authenticate against the same
user_identity.password_hash column, so the hashing lives here rather than
inside either one. bcrypt's own salt handling and constant-time compare
are the whole implementation — never reach for a plain digest.
"""

from __future__ import annotations

import secrets

import bcrypt


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


# The same alphabet the licence key uses — one of each confusable pair —
# because this gets read down a phone line too. Lower case and no prefix so
# it cannot be mistaken for a pairing key (ORG-XXXX-XXXX-XXXX): the two are
# handed over in the same breath, and one word for two credentials has
# already caused trouble here once.
GENERATED_PASSWORD_ALPHABET = "acdefghjkmnpqrtuvwxy2346789"
GENERATED_PASSWORD_GROUPS = 3
GENERATED_PASSWORD_GROUP_SIZE = 4


def generate_password() -> str:
    """A password an admin can read aloud and a farm owner can type.

    Twelve characters from a 27-letter alphabet is about 57 bits — far
    beyond anything that gets guessed against a login, while still short
    enough to dictate over the phone, which is the whole point of offering
    this instead of a link.
    """
    groups = [
        "".join(
            secrets.choice(GENERATED_PASSWORD_ALPHABET)
            for _ in range(GENERATED_PASSWORD_GROUP_SIZE)
        )
        for _ in range(GENERATED_PASSWORD_GROUPS)
    ]
    return "-".join(groups)
