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

if [ "${GENERATE_LICENSE_KEYS_IF_MISSING:-true}" = "true" ]; then
    # Only device activation/offline-license features depend on this
    # keypair (see app/devices/lease.py) — nothing else reads it at
    # startup, so a fresh keypair here never blocks the API from serving
    # traffic. It DOES need to live on storage that survives a restart
    # (see docs/AZURE_DEPLOYMENT.md) — a lease signed with a keypair that
    # then disappears can never be verified again.
    #
    # scripts/ is in the build context now that the image builds from the
    # repository root, and the script reads the same LICENSE_LEASE_*_PATH
    # variables the API does, so this no longer needs its own inline copy
    # of the key generation.
    python scripts/generate_license_keys.py --if-missing
fi

echo "docker-entrypoint: starting: $*"
exec "$@"
