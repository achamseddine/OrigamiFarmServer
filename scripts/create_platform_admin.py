#!/usr/bin/env python3
"""Creates (or updates) an Origami staff account that can sign in to the console.

This is the bootstrap: a fresh deployment has no way in until one of these
exists. Run it against the control database — in a container that is
`python scripts/create_platform_admin.py --email you@example.com`, and
locally it is the same with CONTROL_DATABASE_URL pointed where you want.

Re-running for an existing address updates that account's password and
role rather than failing, which is also how you reset a lost password.

The password is prompted for by default so it never lands in shell history
or a process listing; PLATFORM_ADMIN_PASSWORD is honoured for automated
provisioning.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))

from sqlalchemy import func, select  # noqa: E402

from app.auth.models import UserIdentity  # noqa: E402
from app.auth.passwords import hash_password  # noqa: E402
from app.common.db import ControlSessionLocal  # noqa: E402
from app.common.enums import PlatformRole  # noqa: E402
from app.platform.auth_routes import MIN_PASSWORD_LENGTH  # noqa: E402
from app.tenants.models import PlatformRoleAssignment  # noqa: E402


def read_password() -> str:
    from_env = os.environ.get("PLATFORM_ADMIN_PASSWORD")
    if from_env:
        return from_env

    while True:
        password = getpass.getpass("Password: ")
        if len(password) < MIN_PASSWORD_LENGTH:
            print(f"  Too short — at least {MIN_PASSWORD_LENGTH} characters.", file=sys.stderr)
            continue
        if password != getpass.getpass("Repeat password: "):
            print("  Passwords did not match.", file=sys.stderr)
            continue
        return password


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", default=None, help="Display name; defaults to the email address.")
    parser.add_argument(
        "--role",
        default=PlatformRole.PLATFORM_SUPER_ADMIN.value,
        choices=[role.value for role in PlatformRole],
    )
    args = parser.parse_args()

    password = read_password()
    if len(password) < MIN_PASSWORD_LENGTH:
        raise SystemExit(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")

    email = args.email.strip()

    with ControlSessionLocal() as db:
        user = db.execute(
            select(UserIdentity).where(func.lower(UserIdentity.email) == email.lower())
        ).scalar_one_or_none()

        if user is None:
            user = UserIdentity(
                idp_subject=email,
                email=email,
                display_name=args.name or email,
                password_hash=hash_password(password),
            )
            db.add(user)
            db.flush()
            print(f"Created user {email} ({user.id})")
        else:
            user.password_hash = hash_password(password)
            if args.name:
                user.display_name = args.name
            print(f"Updated password for existing user {email} ({user.id})")

        existing = db.execute(
            select(PlatformRoleAssignment).where(
                PlatformRoleAssignment.user_id == user.id,
                PlatformRoleAssignment.platform_role == args.role,
            )
        ).scalar_one_or_none()
        if existing is None:
            db.add(PlatformRoleAssignment(user_id=user.id, platform_role=args.role))
            print(f"Granted {args.role}")
        else:
            print(f"Already holds {args.role}")

        db.commit()

    print("Done — sign in at the console with this email and password.")


if __name__ == "__main__":
    main()
