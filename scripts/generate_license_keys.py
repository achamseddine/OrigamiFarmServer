#!/usr/bin/env python3
"""Generates the RSA keypair used to sign/verify offline license leases.

Run once per environment. The private key must stay on the server / in a
secret store — it is never shipped to a tablet. Re-run only if you intend
to rotate keys (existing leases signed with the old key stay valid only
until their own expires_at; there is no separate revocation for leases
already issued, by design — see LICENSE_ENTITLEMENTS.md).
"""

from __future__ import annotations

import pathlib

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

OUT_DIR = pathlib.Path(__file__).resolve().parent.parent / "infrastructure" / "keys"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    private_path = OUT_DIR / "license_lease_private.pem"
    public_path = OUT_DIR / "license_lease_public.pem"

    if private_path.exists() or public_path.exists():
        raise SystemExit(
            f"Refusing to overwrite existing keys in {OUT_DIR}. "
            "Remove them first if you really intend to rotate."
        )

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


if __name__ == "__main__":
    main()
