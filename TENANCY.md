# Tenancy

## Identifiers

- `tenant_id` — immutable internal UUID. The actual security boundary everywhere in the system.
- `company_code` (e.g. `FARM-A`) — human-readable, unique, shown to users. **Never** used for
  authorization; it exists purely as a display/lookup convenience for platform staff.
- `farm_id` — a physical site under a tenant. A tenant may own multiple farms.
- `membership_id` — a user's relationship to exactly one tenant (role, farm scope, permissions).

## How tenant_id is resolved — never from a client-supplied value

This is the rule the whole authorization model is built around: **a client is never trusted to
say which tenant it's acting as.** `app/auth/dependencies.py:get_tenant_context` resolves it one of
two ways:

1. **Device-bound requests** (the common tablet case): an `X-Device-Id` header names a device.
   The server loads that `device` row — which was written once, by an authorized platform/tenant
   admin, at activation time — and takes `tenant_id` from it. The device's own recorded status is
   checked (`DEVICE_REVOKED` if not `ACTIVE`) before anything else, so a revoked device can't reach
   any tenant at all, regardless of who's logged into it.
2. **User-session requests** (e.g. a browser): the server loads the caller's own *active*
   `tenant_membership` rows by `user_id` (never by anything the client sent). If there's exactly
   one, it's used automatically. If there's more than one (a future multi-tenant-user case), the
   client may send an `X-Membership-Id` *hint* — but the server always re-verifies that row belongs
   to `identity.user_id` before trusting it (`PERMISSION_DENIED` otherwise). The header is a
   convenience for picking among the caller's *own, already-verified* memberships; it is never
   authorization by itself.

In both cases, the resulting `tenant_id` — plus role, farm scope (`membership_farm_access`), and
module permissions (`membership_module_permission`) — is packaged into a `TenantContext` dataclass
that every downstream check reads. Nothing downstream re-parses a header.

## Row-Level Security

Every farm-data-plane table (`animal`, `field`, `inventory_item`, `inventory_movement`, `task`,
`sync_event`) has:

```sql
ALTER TABLE <table> ENABLE ROW LEVEL SECURITY;
ALTER TABLE <table> FORCE ROW LEVEL SECURITY;   -- applies even to the table owner

CREATE POLICY tenant_isolation_<table>
ON <table>
USING (tenant_id = app_current_tenant_id())
WITH CHECK (tenant_id = app_current_tenant_id());
```

`app_current_tenant_id()` is a `STABLE` PL/pgSQL function (see
`api/migrations/tenant/versions/..._initial_tenant_schema.py`) rather than an inline
`current_setting(...)::uuid` cast, for a specific reason found while building this: **Postgres
does not guarantee left-to-right short-circuit evaluation inside `AND`.** A policy written as
`current_setting(...) IS NOT NULL AND tenant_id = current_setting(...)::uuid` can still raise a
cast error for a connection that never set `app.tenant_id`, because the planner is free to
evaluate the cast first. Routing the cast through a function with an `EXCEPTION WHEN
invalid_text_representation` handler means it always returns either a valid UUID or `NULL` —
and `tenant_id = NULL` is simply `false` (zero rows visible or writable), never an error.

`app_current_tenant_id()` reads `app.tenant_id`, which is set — **only** by `TenantDataRouter`, only
from the server-resolved `tenant_id` described above — via:

```sql
SELECT set_config('app.tenant_id', :tenant_id, true);
```

Not `SET LOCAL app.tenant_id = :tenant_id` — Postgres's `SET` command doesn't accept bind
parameters at all (this failed loudly and immediately the first time it was tried against real
Postgres, which is exactly why it's called out here). `set_config(name, value, true)` is a regular
SQL function and the `true` third argument gives it the same transaction-local scope `SET LOCAL`
would have.

Any connection that reaches these tables *without* going through `TenantDataRouter` — a stray
script, a misconfigured admin connection — sees and can write **zero rows**, not "whatever the last
transaction happened to set." Verified directly (not just through the API layer) in
`api/tests/test_tenant_isolation.py::test_rls_denies_cross_tenant_access_at_the_database_layer`.

## Object-level authorization / no enumeration

For `GET /api/v1/animals/{id}` etc., a cross-tenant lookup and a nonexistent ID are
indistinguishable: RLS itself makes the row simply not exist in the query result set for a
different tenant's session, so the route's own "not found" path is the only path — there is no
separate "exists but forbidden" branch to accidentally implement inconsistently. See
`app/tenant_api/routes.py:_load_animal_or_404` and its test.

## Dedicated-database tenants

See ARCHITECTURE.md's Tenant Data Router section. The mechanism (`tenant_data_locator.mode`,
per-tenant engine cache, `connection_secret_ref` naming an env var rather than storing a raw
connection string) is implemented; it has not been exercised against an actual second database in
this environment.

## Farm scope and permissions

`TenantMembership` carries a `tenant_role` (`TENANT_OWNER` / `FARM_MANAGER` / `EMPLOYEE`).
`TENANT_OWNER`/`FARM_MANAGER` default to full farm visibility *until* explicit
`membership_farm_access` rows scope them down; `EMPLOYEE` has zero farm access until explicitly
granted (fail-closed default — see `TenantContext.has_farm_access`). Module permissions are a
free-form many-to-many (`membership_module_permission`, `"module_code:action"` pairs) so
"Animals + Feed + Inventory" or any other combination the product brief calls for is just rows, not
a fixed role enum. A Farm Manager can only grant a permission for a module the tenant is currently
entitled to — enforced server-side in `app/tenant_api/routes.py:create_membership`
(`MODULE_NOT_ENTITLED` otherwise), not just hidden in the UI.
