#!/bin/sh
# Production container entrypoint. Runs the one-time/idempotent setup a
# freshly-started container needs before it can safely serve traffic, then
# execs the given command (so it becomes PID 1 and receives signals
# correctly — see `exec` at the bottom).
#
# Designed for a single-instance deployment (e.g. one Azure Web App for
# Containers instance): both steps below are safe to run on every
# container start (migrations are idempotent; key generation is skipped
# once a keypair exists). If this is ever scaled to multiple concurrent
# instances, move both steps into a separate one-off deploy/release step
# instead of running them from every instance's own boot.
set -eu

cd /app

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
    echo "docker-entrypoint: running control-plane migrations..."
    alembic -c alembic_control.ini upgrade head
    echo "docker-entrypoint: running tenant-plane migrations..."
    alembic -c alembic_tenant.ini upgrade head
fi

private_key_path="${LICENSE_LEASE_PRIVATE_KEY_PATH:-./infrastructure/keys/license_lease_private.pem}"
public_key_path="${LICENSE_LEASE_PUBLIC_KEY_PATH:-./infrastructure/keys/license_lease_public.pem}"
if [ "${GENERATE_LICENSE_KEYS_IF_MISSING:-true}" = "true" ] && { [ ! -f "$private_key_path" ] || [ ! -f "$public_key_path" ]; }; then
    echo "docker-entrypoint: no license lease keypair found at $private_key_path — generating one..."
    # Only device activation/offline-license features depend on this
    # keypair (see app/devices/lease.py) — nothing else reads it at
    # startup, so a fresh keypair here never blocks the API from serving
    # traffic. It DOES need to live on storage that survives a restart
    # (see docs/AZURE_DEPLOYMENT.md) — a lease signed with a keypair that
    # then disappears can never be verified again.
    #
    # Deliberately not ../scripts/generate_license_keys.py: that script
    # hardcodes its output to <repo root>/infrastructure/keys/, which
    # isn't in this image's build context (context: ./api) and ignores
    # LICENSE_LEASE_*_PATH entirely — fine for bare-metal/CI, wrong here.
    mkdir -p "$(dirname "$private_key_path")" "$(dirname "$public_key_path")"
    python -c "
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import os

key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
private_path = '$private_key_path'
public_path = '$public_key_path'

with open(private_path, 'wb') as f:
    f.write(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ))
with open(public_path, 'wb') as f:
    f.write(key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ))
os.chmod(private_path, 0o600)
print(f'Wrote {private_path} and {public_path}')
"
fi

echo "docker-entrypoint: starting: $*"
exec "$@"
