# Deployment Notes

These are engineering notes for taking this foundation toward staging/production, not a finished
runbook — no contractual SLA should be derived from anything below until it has been validated
against real infrastructure (see ARCHITECTURE.md "What's real vs. scaffolded").

## The deployable unit

One image containing both halves of the product. The Dockerfile lives in `api/` but **builds from
the repository root**, because it needs `admin-web/` and `scripts/` too:

```bash
docker build -f api/Dockerfile -t origami-api .
docker run -p 8000:8000 --env-file .env origami-api
```

`admin-web` compiles to static files in a Node build stage; the runtime stage is Python only and
serves those files itself from `/`, so the container runs a single process with no Node runtime
and the console never needs its own hostname, CORS entry, or deployment. Anything the API doesn't
claim (`/api/v1/**`, `/platform/v1/**`, `/health`, `/docs`) falls through to the console.

`api/docker-entrypoint.sh` runs both migration chains, creates the license-lease keypair if it is
missing, then execs the image's command. Both steps can be turned off per-instance with
`RUN_MIGRATIONS=false` / `GENERATE_LICENSE_KEYS_IF_MISSING=false`, which is what the `workers`
service does so only one container races to migrate.

On a single-container host (Azure Web App for Containers, Cloud Run, Fly, a plain Docker host)
that is the whole deployment — see [AZURE_DEPLOYMENT.md](AZURE_DEPLOYMENT.md). Two settings
deserve attention there:

- **`LICENSE_LEASE_PRIVATE_KEY_PATH` / `LICENSE_LEASE_PUBLIC_KEY_PATH`** must point at persistent
  storage. The container filesystem is replaced on every deploy, and a regenerated keypair
  invalidates every offline lease already signed with the old one. Both must name a *file*, not
  the directory holding it.
- **`AUTH_DEV_MODE`** stays `false`. The console's sign-in calls `/api/v1/auth/dev-login`, which
  is disabled unless that flag is on, so a production console needs a real OIDC provider wired up
  (see `app/auth/providers.py`) rather than the dev path.

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

Not part of this repo. Terminate TLS and apply rate limiting in front of the container at
Nginx/Caddy/Traefik/a managed gateway — there is one origin to point it at, since the console and
the API share it.
