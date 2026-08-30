# Origami Server

The secure multi-tenant SaaS control plane and API platform that powers Origami FarmOS for every
customer farm. This is the v0.1 foundation: tenant isolation, entitlements, device licensing, a
representative FarmOS API + sync protocol, and the platform admin console — built and tested
against real PostgreSQL, not mocked.

See also: [ARCHITECTURE.md](ARCHITECTURE.md) · [TENANCY.md](TENANCY.md) ·
[SECURITY.md](SECURITY.md) · [SYNC_PROTOCOL.md](SYNC_PROTOCOL.md) ·
[LICENSE_ENTITLEMENTS.md](LICENSE_ENTITLEMENTS.md) · [API_ERROR_CODES.md](API_ERROR_CODES.md) ·
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) · [docs/AZURE_DEPLOYMENT.md](docs/AZURE_DEPLOYMENT.md) ·
[docs/FARMOS_API.md](docs/FARMOS_API.md)

## What's in v0.1

Built, tested, and running end to end:

- Tenant / farm / membership / platform-role control-plane schema (Alembic migrations).
- PostgreSQL Row-Level Security on every farm-data-plane table, with a `TenantDataRouter`
  abstraction so a tenant can later move to a dedicated database without an API contract change.
- Auth abstraction (OIDC/Keycloak-shaped `IdentityProvider`, plus a dev-mode provider for local
  work) and the centralized authorization dependency chain: identity → tenant context → module
  entitlement → permission → farm scope.
- `EntitlementService` + tenant/subscription/module state machines, all audited.
- Device activation (hashed, single-use, expiring codes) and signed RS256 offline license leases.
- The full FarmOS tablet API contract — 92 endpoints across 19 groups (animals, tasks, animal
  health, observations, feed, production, agriculture, employees & permissions, sales/expenses,
  notifications, priorities, audit, AI recommendations, reports, module licensing, and the Mouneh
  and Farm Visits licensed add-ons), enforcing its own permission grid + RLS. See
  [docs/FARMOS_API.md](docs/FARMOS_API.md).
- Idempotent, cursor-based sync push/pull with a structured conflict model (the generic
  `app/sync/` protocol, independent of the FarmOS tablet contract's own `Idempotency-Key` handling).
- Audit service, support sessions (time-boxed, expiring), file presign endpoint, backup/export
  metadata endpoints.
- Admin Web (Next.js): dev login, platform dashboard with real counts, tenant list with
  search/filter/pagination, a multi-step create-tenant wizard where every step is a real API call,
  and a Tenant 360 page (Overview / Farms / Modules / Devices / Audit) with working
  activate/deactivate/revoke actions.
- 72 automated tests against a real Postgres instance, including every mandatory isolation,
  entitlement, device, sync, support-session, audit, and platform-role scenario from the product
  brief, plus the full FarmOS tablet contract's own test suite (`api/tests/test_farmos_stage*.py`).

**Deliberately deferred** (see "Roadmap" in ARCHITECTURE.md): billing/payment provider integration,
scheduled backup/export job execution (the API + data model exist; a worker doesn't yet produce
real backups), notifications, usage-metering aggregation, the full 22-page admin console (only the
pages above are built — no page in this repo is a dead link), and Keycloak/MFA wired into a live
IdP (the abstraction and Docker Compose service are there; local dev uses `AUTH_DEV_MODE`).

## Repository layout

```
admin-web/        Next.js admin console
api/               FastAPI backend
  app/             application code, one package per domain area
  migrations/      two independent Alembic environments: control/ and tenant/
  tests/           pytest suite (runs against real Postgres)
workers/           background worker entrypoint (scaffold; see ARCHITECTURE.md Roadmap)
infrastructure/    Keycloak realm import, RLS notes, generated license-lease keys (gitignored)
scripts/           seed.py, generate_license_keys.py
docker-compose.yml full local stack
.env.example       documented environment variables
```

## Running it locally

### Option A — Docker Compose (matches production topology)

```bash
cp .env.example .env      # edit as needed
docker compose up --build
```

This starts: `control-db` and `tenant-db` (separate Postgres instances, matching the
control-plane/data-plane split — see ARCHITECTURE.md), `redis`, `minio`, `keycloak`, the `api`,
`workers`, and `admin-web`. Then, in a shell inside the `api` container (or locally with
`CONTROL_DATABASE_URL`/`TENANT_DATABASE_URL` pointed at the compose ports):

```bash
alembic -c alembic_control.ini upgrade head
alembic -c alembic_tenant.ini upgrade head
python ../scripts/seed.py
```

### Option B — bare-metal local Postgres (what this repo was actually developed and tested against)

```bash
# 1. Two databases in any local Postgres 16+
createdb origami_control && createdb origami_tenant_shared

# 2. API
cd api
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp ../.env.example .env   # then edit CONTROL_DATABASE_URL / TENANT_DATABASE_URL / AUTH_DEV_MODE=true
python ../scripts/generate_license_keys.py   # writes infrastructure/keys/*.pem, run once
alembic -c alembic_control.ini upgrade head
alembic -c alembic_tenant.ini upgrade head
python ../scripts/seed.py
uvicorn app.main:app --reload

# 3. Admin web (separate shell)
cd admin-web
npm install
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 npm run dev
```

Then open `http://localhost:3000/login` and sign in as `admin@origami-platform.com` (seeded
platform super admin) or `owner@farm-a-demo.com` / `owner@farm-b-demo.com` (seeded tenant owners).
Dev login only works when the API has `AUTH_DEV_MODE=true` — never enable that outside local/CI.

### Running the tests

```bash
cd api
source .venv/bin/activate
# tests point at origami_control_test / origami_tenant_shared_test — create them once:
createdb origami_control_test && createdb origami_tenant_shared_test
python -m pytest -q
```

The suite runs every write against real PostgreSQL (RLS included) — nothing is mocked at the
database layer. See `api/tests/` for the mandatory scenario coverage.

## Seeded demo data

`scripts/seed.py` creates a platform super admin and two tenants with deliberately different
entitlements (and a same-named, same-tag-code animal in each, so you can immediately confirm
isolation is real):

| Tenant | Company ID | Modules | Owner |
|---|---|---|---|
| Dairy Farm | `FARM-A` | CORE, ANIMALS, FEED, MILK | `owner@farm-a-demo.com` |
| Mixed Farm | `FARM-B` | CORE, ANIMALS, AGRICULTURE, PRODUCE, MOUNEH, SALES, FARM_VISITS | `owner@farm-b-demo.com` |

Safe to re-run; every insert is guarded by a natural-key lookup first. `scripts/seed.py` also
password-enables both tenant owners for the FarmOS tablet contract's own login
(`POST /api/v1/auth/login`, distinct from the platform's OIDC/dev-login) — password
`farmos-demo-2026` for either owner email above — and grants Tenant B's licensed add-ons
(`mouneh`, `visits_agritourism`) as active `TenantEntitlement` rows so its owner can immediately
exercise the Mouneh and Farm Visits endpoints. See [docs/FARMOS_API.md](docs/FARMOS_API.md).
