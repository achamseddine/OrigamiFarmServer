# Architecture

## Planes

Four planes, matching the technical spec:

1. **Client plane** — Origami Admin Web (Next.js), FarmOS tablets, future integrations.
2. **Edge/security plane** — TLS termination + reverse proxy in front of the API (not part of this
   repo; see `docker-compose.yml` for where Nginx/Traefik/Caddy would sit in production), CORS,
   security headers, correlation IDs (`app/common/logging.py`).
3. **Application plane** — the FastAPI modular monolith: control-plane services, entitlement
   engine, tenant farm-data APIs, sync, audit, support, files, backups. One package per domain
   under `api/app/`.
4. **Data plane** — two logically separate PostgreSQL databases, Redis, S3-compatible object
   storage.

No microservices were introduced. `api/app/` is a single deployable FastAPI app with clear package
boundaries per domain (`tenants/`, `plans/`, `devices/`, `entitlements/`, `sync/`, `audit/`,
`support/`, `backups/`, `billing/`, `files/`, `platform/`, `tenant_api/`) — split into services
later only if load or team structure demands it.

## Control plane vs. farm data plane

Two independent PostgreSQL databases, two independent Alembic environments
(`api/migrations/control` and `api/migrations/tenant`, driven by `alembic_control.ini` /
`alembic_tenant.ini`):

- **Control** (`app.common.db.ControlBase`, `origami_control`): tenant, farm registry, module
  catalog, plans, subscriptions, entitlements, devices, license leases, billing metadata, backup
  and export metadata, support sessions, audit log, feature flags, user identity references. No
  RLS here — every query already carries an explicit `tenant_id` filter in application code, and
  platform staff legitimately need cross-tenant visibility (gated by platform roles instead).
- **Tenant/farm data** (`app.common.db.TenantBase`, `origami_tenant_shared` by default):
  `animal`, `field`, `inventory_item`, `inventory_movement`, `task`, `sync_event` — a
  representative slice of the FarmOS domain. Every table has Row-Level Security enabled and
  forced (see TENANCY.md). Milk/eggs/Mouneh/sales/visits follow the identical pattern when built.

Why split the database rather than just a schema: it makes the "shared vs. dedicated" tenancy
mode a connection-string swap (see `TenantDataRouter` below) instead of a schema-namespacing
exercise, and it means a runaway farm-data query can never starve control-plane connections.

## Tenant Data Router

`app/common/tenant_router.py`:

```python
with TenantDataRouter.session_for(tenant_id) as db:
    ...
```

Looks up `tenant_data_locator.mode` for the tenant. `SHARED_RLS` (the default) returns a session
against the shared tenant database with `app.tenant_id` set via `set_config(..., true)` for that
transaction. `DEDICATED_DB` resolves a per-tenant engine from an environment variable named by
`tenant_data_locator.connection_secret_ref` (never a raw connection string stored in the row) and
returns a session against that instead — cached per tenant_id so repeated calls don't reconnect.
Every farm-data repository/route calls this, so introducing a dedicated-database tenant later is a
router change plus one row update, not an API contract change.

`app/common/db.py` also sets `type_annotation_map = {datetime: DateTime(timezone=True)}` on both
declarative bases — every timestamp in this system is UTC-aware end to end; this was a real bug
caught while building device activation (naive vs. aware comparison) and fixed at the base-class
level rather than column-by-column.

## Authorization chain

`app/auth/dependencies.py` composes, in order:

1. `get_identity` — verifies the bearer token via the configured `IdentityProvider`
   (`app/auth/providers.py`; OIDC/Keycloak in staging+prod, a local HS256 dev provider gated by
   `AUTH_DEV_MODE` otherwise) and resolves/creates the internal `UserIdentity` row.
2. `get_tenant_context` — resolves `tenant_id`, membership, role, farm scope, and permissions
   *entirely from server-side lookups* (see TENANCY.md for exactly how — this is the "never trust
   a client-supplied tenant ID" boundary).
3. `require_module(code)` — calls `EntitlementService.is_module_active`.
4. `require_permission(module, action)` — `require_module` plus a membership permission check.
5. `has_farm_access(farm_id)` on `TenantContext` — farm scope, checked explicitly by each route.

A request is only permitted if every stage it depends on passes. Denials use the stable error
codes in API_ERROR_CODES.md — never a generic 403 with no machine-readable reason.

## Entitlement engine

`app/entitlements/service.py` (`EntitlementService`) is the single place that answers "is tenant X
allowed to use module Y right now" — reading `tenant_entitlement` rows plus tenant status. Both
`GET /api/v1/me/entitlements` and every `require_module()` check read through this exact class;
there is no separate "cached client view" that could drift from server enforcement. See
LICENSE_ENTITLEMENTS.md for the state machines.

## Sync

See SYNC_PROTOCOL.md.

## Devices and offline licensing

See LICENSE_ENTITLEMENTS.md.

## Observability

`app/common/logging.py`: structlog JSON logging, a correlation-ID middleware
(`X-Correlation-Id`, generated if absent, echoed back, attached to every audit event via
`get_correlation_id()`), and security-header middleware (`X-Content-Type-Options`,
`X-Frame-Options`, `Referrer-Policy`, HSTS, `Cache-Control: no-store`). `/healthz` is a liveness
check; `/readyz` actually pings both databases and reports per-dependency status.

## What's real vs. scaffolded

Everything under "What's in v0.1" in the README is implemented and covered by a passing automated
test or a manual Playwright-driven browser check against the running admin web — not aspirational.
What's explicitly a scaffold, not a claim of completeness:

- `workers/main.py` starts cleanly (so `docker-compose up` works end to end) but does not yet
  claim `PENDING` `backup_job`/`tenant_export` rows and produce a real backup or export package.
  The API/data model for this is real; the job execution is Phase E follow-up work.
- Billing is an internal state model only (`billing_account`, `invoice`) — no payment provider
  adapter is wired up (Phase F).
- Keycloak ships in `docker-compose.yml` and `app/auth/providers.py` has a real
  `OIDCIdentityProvider`, but it has not been exercised against a live Keycloak realm in this
  environment (no Docker daemon available here) — only against the dev provider. The JWKS/RS256
  validation path is standard PyJWT and should work unmodified against a real issuer; treat it as
  code-reviewed, not integration-tested.
- Admin Web implements the pages listed in the README. Plans/Module-catalog/Devices/Backups/
  Billing/Support/Audit as *dedicated top-level pages* (rather than tabs inside Tenant 360) are not
  built — there is no sidebar link to a page that doesn't exist.

## Roadmap (unchanged from the technical spec's phases)

Phase B (admin web breadth) and Phase C (full FarmOS domain APIs: health, feed, milk, eggs,
Mouneh, sales, visits) are the next milestones on top of this foundation — the tenant/entitlement/
RLS/sync scaffolding here is designed so adding those is "more of the same pattern," not a
redesign. Phases E–G (real backup/export execution, billing automation, usage metering, dedicated-
DB tenants in practice, load testing, SSO) follow.
