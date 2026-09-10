#!/bin/sh
# Brings the container's own state up to date, then hands off to the server.
#
# Both migration chains run on every boot: they are no-ops once applied, and
# a single-container deployment has nowhere else to run them.
set -e

echo "docker-entrypoint: running control-plane migrations..."
alembic -c alembic_control.ini upgrade head

echo "docker-entrypoint: running tenant-plane migrations..."
alembic -c alembic_tenant.ini upgrade head

# Only ever creates keys that aren't there. Regenerating would invalidate
# every offline license lease already signed with the old key, so point
# LICENSE_LEASE_*_KEY_PATH at persistent storage in any real deployment —
# otherwise a fresh keypair is minted each time the container is replaced.
python scripts/generate_license_keys.py --if-missing

if [ "$#" -gt 0 ]; then
    exec "$@"
fi

exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "${PORT:-8000}" \
    --workers "${UVICORN_WORKERS:-2}"
