#!/usr/bin/env bash
# Regenerates 01_control_schema.sql and 02_tenant_schema.sql from the
# Alembic migrations. Run this after adding a migration, and commit the
# result — the .sql files are generated artifacts, never hand-edited.
#
#     cd api && source .venv/bin/activate
#     ../infrastructure/sql/generate.sh
#
# `alembic upgrade base:head --sql` is Alembic's offline mode: it renders
# every migration to DDL without connecting to a database, and emits the
# alembic_version INSERT/UPDATEs alongside it. Those stamps are the reason
# to generate rather than hand-write — a database built from these files
# is a database Alembic recognizes, so the next `alembic upgrade head`
# applies only what's new instead of trying to recreate everything.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
api="$(cd "$here/../../api" && pwd)"

cd "$api"

emit() {
    local ini="$1" out="$2" plane="$3"
    {
        echo "-- Origami Server — ${plane} schema."
        echo "--"
        echo "-- GENERATED FILE. Do not edit by hand: regenerate with"
        echo "-- infrastructure/sql/generate.sh after changing a migration."
        echo "--"
        echo "-- Run as the origami role, against an empty database:"
        echo "--     psql -U origami -d <db> -f $(basename "$out")"
        echo "--"
        echo "-- Ownership matters: whoever runs this owns the tables. Run it as"
        echo "-- a superuser and the origami role ends up with no privileges on"
        echo "-- them. See README.md in this directory."
        echo "--"
        echo "-- Rendered by: alembic -c $(basename "$ini") upgrade base:head --sql"
        echo ""
        alembic -c "$ini" upgrade base:head --sql 2>/dev/null
    } > "$here/$out"
    echo "wrote $out ($(wc -l < "$here/$out") lines)"
}

emit alembic_control.ini 01_control_schema.sql "control-plane"
emit alembic_tenant.ini  02_tenant_schema.sql  "farm-data-plane"
