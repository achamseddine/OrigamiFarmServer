#!/bin/bash
# Mounted into /docker-entrypoint-initdb.d by docker-compose.yml and
# docker-compose.local.yml, so it runs once, when the postgres container
# first initialises its data directory, as the bootstrap superuser
# (POSTGRES_USER, `postgres` in both compose files).
#
# The official postgres image creates POSTGRES_USER as a superuser, and
# Postgres silently exempts superusers (and BYPASSRLS roles) from
# row-level security: no error, no log line, every tenant sees every
# other tenant's rows. The API's tenant isolation depends on RLS (see
# TENANCY.md), so the role it connects as must not be one — and since
# PostgreSQL 16 the bootstrap user cannot be demoted ("The bootstrap user
# must have the SUPERUSER attribute"), so a separate application role is
# the only correct shape. This is the same role 00_bootstrap.sql creates
# on the bare-metal path.
#
# The application password defaults to POSTGRES_PASSWORD so the compose
# files' connection strings (origami:<password>) keep working; set
# ORIGAMI_APP_PASSWORD on the database service to use a different one.
set -euo pipefail

APP_PASSWORD="${ORIGAMI_APP_PASSWORD:-$POSTGRES_PASSWORD}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
     -v app_password="$APP_PASSWORD" -v dbname="$POSTGRES_DB" <<'EOSQL'
SELECT format('CREATE ROLE origami LOGIN PASSWORD %L', :'app_password')
 WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'origami')\gexec

-- Load-bearing, applied unconditionally: see the header.
ALTER ROLE origami NOSUPERUSER NOBYPASSRLS;

-- Whoever owns the database owns what the migrations create in it.
ALTER DATABASE :"dbname" OWNER TO origami;
EOSQL

echo "origami role ready: NOSUPERUSER NOBYPASSRLS, owns $POSTGRES_DB"
