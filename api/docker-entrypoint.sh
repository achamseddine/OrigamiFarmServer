#!/bin/sh
# Production container entrypoint. Runs the one-time/idempotent setup a
# freshly-started container needs before it can safely serve traffic, then
# execs the given command (so it becomes PID 1 and receives signals
# correctly — see `exec` at the bottom).
#
# Designed for a single-instance deployment (e.g. one Azure Web App for
# Containers instance): the migration step below is idempotent and safe to
# run on every container start. If this is ever scaled to multiple
# concurrent instances, move it into a separate one-off deploy/release
# step instead of running it from every instance's own boot.
#
# It used to also generate a signing keypair for offline licence leases.
# Device licences are gone — Origami is one subscription covering the
# whole product — so there is nothing left to sign, and the keypair that
# had to survive every restart is one fewer thing a deployment can lose.
set -eu

cd /app

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
    echo "docker-entrypoint: running control-plane migrations..."
    alembic -c alembic_control.ini upgrade head
    echo "docker-entrypoint: running tenant-plane migrations..."
    alembic -c alembic_tenant.ini upgrade head
fi

echo "docker-entrypoint: starting: $*"
exec "$@"
