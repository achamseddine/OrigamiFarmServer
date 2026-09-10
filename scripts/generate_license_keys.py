#!/usr/bin/env python3
"""Generates the RSA keypair used to sign/verify offline license leases.

Run once per environment. The private key must stay on the server / in a
secret store — it is never shipped to a tablet. Re-run only if you intend
to rotate keys (existing leases signed with the old key stay valid only
until their own expires_at; there is no separate revocation for leases
already issued, by design — see LICENSE_ENTITLEMENTS.md).

Paths come from the same LICENSE_LEASE_*_KEY_PATH variables the API reads,
so this writes wherever that deployment expects to find them.
"""

from __future__ import annotations

import argparse
import os
import pathlib

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

REPO_KEYS_DIR = pathlib.Path(__file__).resolve().parent.parent / "infrastructure" / "keys"

DEFAULT_PRIVATE_PATH = os.environ.get(
    "LICENSE_LEASE_PRIVATE_KEY_PATH", str(REPO_KEYS_DIR / "license_lease_private.pem")
)
DEFAULT_PUBLIC_PATH = os.environ.get(
    "LICENSE_LEASE_PUBLIC_KEY_PATH", str(REPO_KEYS_DIR / "license_lease_public.pem")
)


def generate(private_path: pathlib.Path, public_path: pathlib.Path) -> None:
    private_path.parent.mkdir(parents=True, exist_ok=True)
    public_path.parent.mkdir(parents=True, exist_ok=True)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    private_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    public_path.write_bytes(
        key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    private_path.chmod(0o600)
    print(f"Wrote {private_path} and {public_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private", default=DEFAULT_PRIVATE_PATH)
    parser.add_argument("--public", default=DEFAULT_PUBLIC_PATH)
    parser.add_argument(
        "--if-missing",
        action="store_true",
        help="Do nothing if the keypair already exists, instead of refusing to run. "
        "This is what container startup uses.",
    )
    args = parser.parse_args()

    private_path = pathlib.Path(args.private)
    public_path = pathlib.Path(args.public)

    # is_file(), not exists(): a path that is a directory is a misconfigured
    # LICENSE_LEASE_*_KEY_PATH, and treating it as "already generated" would
    # hide that until the first lease is signed.
    both_present = private_path.is_file() and public_path.is_file()

    if both_present:
        if args.if_missing:
            print(f"License lease keypair already present at {private_path.parent} — leaving it alone.")
            return
        raise SystemExit(
            f"Refusing to overwrite existing keys at {private_path} / {public_path}. "
            "Remove them first if you really intend to rotate."
        )

    generate(private_path, public_path)


if __name__ == "__main__":
    main()
