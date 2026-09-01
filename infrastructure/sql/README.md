# Plain SQL schema

Creates both databases without Python, Alembic, or Docker — for a DBA who
wants to read the DDL before it runs, a managed Postgres where you only
have a SQL console, or a restore/rebuild where the app isn't available.

**Alembic remains the source of truth.** These files are generated from
the migrations in `api/migrations/`, not maintained alongside them.

| File | What it does | Run as |
|---|---|---|
| `00_bootstrap.sql` | Creates the `origami` role and the two databases | superuser |
| `01_control_schema.sql` | Control-plane tables (tenants, plans, entitlements, devices, audit) | `origami` |
| `02_tenant_schema.sql` | Farm-data-plane tables + row-level security | `origami` |

## Running them

```bash
psql -U postgres -f infrastructure/sql/00_bootstrap.sql

PGPASSWORD=origami_dev_password \
  psql -h localhost -U origami -d origami_control       -f infrastructure/sql/01_control_schema.sql
PGPASSWORD=origami_dev_password \
  psql -h localhost -U origami -d origami_tenant_shared -f infrastructure/sql/02_tenant_schema.sql
```

Set a real password on anything that isn't a throwaway local instance:
`psql -U postgres -v app_password='...' -f infrastructure/sql/00_bootstrap.sql`.

Then seed and run per the root README — `python ../scripts/seed.py`, then
`uvicorn app.main:app --reload`.

## Two things that will bite you if you improvise

**Run 01 and 02 as `origami`, not as a superuser.** Whoever executes the
DDL owns the resulting tables. Run them as `postgres` and the `origami`
role the API connects as ends up with no privileges on anything, and the
API fails on its first query. There is no `GRANT` section in these files
by design — ownership is the simpler and more durable answer.

**The `origami` role must not be a superuser and must not have
`BYPASSRLS`.** Tenant isolation in the farm-data plane is row-level
security (see `TENANCY.md`), and RLS does not apply to superusers or
`BYPASSRLS` roles — Postgres skips the policies silently, with no error
and no log line. An API connected that way looks completely healthy while
every tenant reads every other tenant's rows. `00_bootstrap.sql` applies
`ALTER ROLE origami NOSUPERUSER NOBYPASSRLS` unconditionally, including to
a pre-existing role, precisely so this can't be got wrong once.

To confirm isolation is live after loading `02` and seeding:

```sql
-- as origami
SELECT count(*) FROM animal;                                     -- 0, no tenant set
SELECT set_config('app.tenant_id', '<a tenant uuid>', false);
SELECT count(*) FROM animal;                                     -- only that tenant's rows

SELECT count(*) FROM pg_class WHERE relrowsecurity AND relforcerowsecurity;  -- 39
SELECT count(*) FROM pg_policies WHERE schemaname = 'public';               -- 39
```

If the first query returns rows before any tenant is set, RLS is not in
effect — check the role, not the schema.

## Keeping them in sync

`01` and `02` are generated artifacts. Never hand-edit them; add an
Alembic migration and regenerate:

```bash
cd api && source .venv/bin/activate
../infrastructure/sql/generate.sh
```

`generate.sh` runs `alembic upgrade base:head --sql`, Alembic's offline
mode, which renders every migration to DDL without touching a database.
Crucially it also emits the `alembic_version` bookkeeping, so a database
built from these files is one Alembic recognizes as current: the next
`alembic upgrade head` applies only what's new instead of trying to
recreate everything. That's the reason to generate these rather than
hand-write them, and the reason a hand-edit is a trap — the DDL would
drift from the version stamp that claims to describe it.

## Verified

Built from an empty cluster with these three files (nothing else), then
seeded and run: Alembic reports `9cff7b1c5dc1 (head)` and
`cbd6ac9cefaf (head)`, `alembic upgrade head` is a no-op on both planes,
`/health` and `POST /api/v1/auth/login` return correctly, and RLS filters
as shown above — 39 tables with `ENABLE` + `FORCE ROW LEVEL SECURITY` and
39 matching policies, with one tenant's `COW-001` visible and the other's
not.
