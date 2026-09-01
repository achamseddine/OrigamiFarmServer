-- Origami Server — roles and databases.
--
-- Run FIRST, as a superuser, connected to any existing database:
--
--     psql -U postgres -f infrastructure/sql/00_bootstrap.sql
--
-- Override the application password (do this for anything that isn't a
-- throwaway local instance):
--
--     psql -U postgres -v app_password='...' -f infrastructure/sql/00_bootstrap.sql
--
-- This file is psql-specific (it uses \set and \gexec). The two schema
-- files that follow are plain SQL and will run through any client.

\set ON_ERROR_STOP on

-- Default only; -v app_password=... on the command line wins.
\if :{?app_password}
\else
\set app_password 'origami_dev_password'
\endif

-- The application role.
--
-- NOSUPERUSER NOBYPASSRLS is not a hardening nicety, it is load-bearing.
-- Tenant isolation in the farm-data plane is Postgres row-level security
-- (see TENANCY.md), and RLS does not apply to superusers or to roles with
-- BYPASSRLS — Postgres skips the policies silently, with no error and no
-- log line. An API connected as such a role looks completely healthy
-- while every tenant reads every other tenant's rows.
-- \gexec rather than a DO block: psql does not substitute its variables
-- inside dollar-quoted strings, so :'app_password' would arrive at the
-- server literally. This renders the statement in a plain SELECT — where
-- substitution does happen — and executes the result, only when the role
-- is absent.
SELECT format('CREATE ROLE origami LOGIN PASSWORD %L', :'app_password')
 WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'origami')\gexec

-- Applied unconditionally, including to a pre-existing role: if someone
-- created `origami` as a superuser earlier, the whole isolation model is
-- off until this runs. Cheap to re-apply, and the one thing here you
-- never want to depend on having been done right the first time.
ALTER ROLE origami NOSUPERUSER NOBYPASSRLS;

-- The two databases — control plane (tenants, plans, entitlements,
-- devices, audit) and farm-data plane (RLS-isolated operational data).
-- See ARCHITECTURE.md for why they're separate.
--
-- CREATE DATABASE cannot run inside a transaction block or a DO block, so
-- \gexec is used to emit the statement only when the database is absent,
-- which keeps this file re-runnable.
SELECT 'CREATE DATABASE origami_control OWNER origami'
 WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'origami_control')\gexec

SELECT 'CREATE DATABASE origami_tenant_shared OWNER origami'
 WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'origami_tenant_shared')\gexec

\echo ''
\echo 'Bootstrap complete. Next, as the origami role (ownership matters — see README.md):'
\echo '  psql -U origami -d origami_control       -f infrastructure/sql/01_control_schema.sql'
\echo '  psql -U origami -d origami_tenant_shared -f infrastructure/sql/02_tenant_schema.sql'
