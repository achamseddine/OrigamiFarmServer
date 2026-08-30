# Deployment Notes

These are engineering notes for taking this foundation toward staging/production, not a finished
runbook — no contractual SLA should be derived from anything below until it has been validated
against real infrastructure (see ARCHITECTURE.md "What's real vs. scaffolded").

## Environments

Four environments are assumed, per the technical spec: local, development, staging, production.
Each needs its own `CONTROL_DATABASE_URL` / `TENANT_DATABASE_URL`, its own
`infrastructure/keys/license_lease_*.pem` keypair (never shared across environments — a lease
signed in staging must not verify in production), its own `APP_SECRET_KEY`, and its own OIDC
realm/client. `AUTH_DEV_MODE` must be `false` (the default) everywhere except local/CI —
`app/main.py` refuses to boot with it `true` when `ENVIRONMENT=production`.

## Migrations

Two independent Alembic environments, run separately and in this order (control has no foreign
keys into tenant data, but tenant-side code assumes control-plane tables like `tenant` and
`tenant_data_locator` exist for lookups):

```bash
alembic -c alembic_control.ini upgrade head
alembic -c alembic_tenant.ini upgrade head
```

CI should run both `upgrade head` against a fresh database on every change to `api/migrations/**`
to catch a migration that doesn't apply cleanly before it reaches staging (see
`.github/workflows/ci.yml`).

## Secrets

Never in git (`.gitignore` already excludes `.env*` except `.env.example`, and
`infrastructure/keys/`). In a real deployment these belong in the platform's secret manager
(e.g. cloud provider secrets service, Vault) and are injected as environment variables — the app
only ever reads them via `app/config/settings.py:Settings`, never hardcoded.

## Object storage

`S3_ENDPOINT_URL` points at MinIO locally and at a real S3-compatible endpoint in staging/prod.
Buckets must not be public — `app/files/routes.py` only ever hands out short-lived presigned URLs
after authorization; there is no code path that constructs a public object URL.

## Backups

The `backup_job` / `tenant_export` API and data model are implemented; the job execution that
actually produces a database backup or a tenant export package is not (see `workers/main.py`'s
docstring and ARCHITECTURE.md). Before any commercial commitment on RPO/RTO:

1. Wire a real scheduled job (Celery/Dramatiq/ARQ, per the technical spec's stack choice) that
   claims `PENDING` `backup_job`/`tenant_export` rows, does the work, and writes back
   `status`/`storage_key`/`error_message`.
2. Actually run a restore drill into an isolated environment and confirm data comes back correct —
   "the backup job succeeded" is not sufficient evidence a restore works (this is called out
   explicitly in the product brief and is still true here).
3. Only then set an RPO/RTO number anyone can be held to.

## CI/CD

`.github/workflows/ci.yml` runs, on every push/PR: Python dependency install, `ruff` lint, `mypy`
(both blocking — the codebase is clean under both as of this commit), Alembic upgrade-head against
fresh Postgres services (control + tenant), the pytest suite (including the mandatory isolation/
entitlement/device/sync tests) against those same databases, and the admin-web `tsc --noEmit` +
`next build`. A target hosting environment now exists (Azure Web App for Containers) — see
[AZURE_DEPLOYMENT.md](AZURE_DEPLOYMENT.md) for the manual `az` CLI deploy steps, and
`.github/workflows/deploy-azure.yml` (manual `workflow_dispatch` only, not yet wired to run
automatically on push) for the same as a repeatable build-push-deploy workflow.

## Reverse proxy / TLS

Not part of this repo. In front of `api` and `admin-web`, terminate TLS and apply rate limiting at
Nginx/Caddy/Traefik/a managed gateway — `docker-compose.yml`'s services are meant to sit behind
one in any non-local environment.
