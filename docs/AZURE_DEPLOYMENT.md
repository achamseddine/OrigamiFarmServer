# Deploying to Azure Web App for Containers

Concrete steps for getting the `api` image running on an existing Azure Web App for Containers —
written for `leb-container-test.azurewebsites.net`, adjust names/values for anything else. This is
a **test deployment**: dev-mode login (`AUTH_DEV_MODE=true`), no reverse proxy to add (Azure
already terminates HTTPS for you on `*.azurewebsites.net`), no Keycloak. See "Going beyond a test
deployment" at the bottom for what changes before this is production-grade.

`*.azurewebsites.net` is a **single-container PaaS app**, not a Docker Compose host — it runs one
image, and Postgres/Redis/etc. need to be separate managed Azure resources, not sibling containers.
`docker-compose.yml` at the repo root is for local development only; it isn't what gets deployed
here.

## What you need before starting

- The Azure CLI (`az`), logged in: `az login`.
- The resource group and name of the existing Web App. Find them:
  ```bash
  az webapp list --query "[?name=='leb-container-test'].{name:name, resourceGroup:resourceGroup, location:location}" -o table
  ```
  The rest of this doc uses `$RG` (resource group) and `$LOCATION` for whatever that prints —
  export them as shell variables so the commands below can be copy-pasted:
  ```bash
  RG=<resourceGroup from above>
  LOCATION=<location from above>
  APP_NAME=leb-container-test
  ```
- Docker Desktop, if building locally (the commands below default to this — it's what most
  people already have a habit for). If you'd rather not run Docker locally at all, `az acr build`
  builds the same image inside Azure itself — see the alternative at the end of step 1.

## 1. Build and push the image (Azure Container Registry)

Create a registry, if you don't already have one you want to reuse for this project:

```bash
ACR_NAME=origamiacr$RANDOM   # must be globally unique; note whatever this prints
az acr create --resource-group "$RG" --name "$ACR_NAME" --sku Basic
```

Build and push with Docker Desktop. **The build context is `./api`, not `.`** — `api/Dockerfile`'s
`COPY requirements.txt .` expects `requirements.txt` at the context root, and that file lives at
`api/requirements.txt`, not the repo root:

```bash
az acr login --name "$ACR_NAME"

docker build -f ./api/Dockerfile -t "$ACR_NAME.azurecr.io/origami-api:latest" ./api
docker push "$ACR_NAME.azurecr.io/origami-api:latest"
```

If Docker Desktop is on Apple Silicon (M-series Mac), it builds `arm64` by default — Azure App
Service Linux containers run on `amd64`, and a plain `arm64` push there fails at container start
(exec-format error), not at build/push time, so it's a confusing one to hit blind. Add
`--platform linux/amd64` to the `docker build` above if that's your machine.

(Cloud-build alternative, no local Docker needed: `az acr build --registry "$ACR_NAME" --image
origami-api:latest ./api` — uploads `./api` and builds it server-side.)

### If the build fails

**Anything mentioning `apt-get`, `NO_PUBKEY`, `is not signed`, or
`APT::Update::Post-Invoke`.** The current Dockerfile doesn't run `apt-get`
at all, so any of these means you're building an older revision — `git
pull` and check with `grep -c apt-get api/Dockerfile` (expect `0`) and
`docker build` reporting more than 8 steps. Earlier revisions installed
`gcc`/`libpq-dev`, which turned out to be unnecessary: every dependency
ships a prebuilt wheel and `psycopg[binary]` vendors its own libpq, so the
package manager was removed rather than kept working. That step was by far
the most fragile part of the build — it broke three different ways on one
machine (full Docker VM, a seccomp/base-image mismatch, then archive key
verification) and none of them can recur now.

**`No space left on device`.** Docker Desktop's VM has its own virtual disk
that fills with old images and build cache:

```bash
docker system df            # what's actually using the space
docker builder prune        # build cache only — safest, usually the biggest win
docker image prune -a       # images not used by any container
```

Only reach for `docker system prune -a --volumes` if that isn't enough, and
read it twice first: `--volumes` deletes named volumes, which is where
container databases live — it can wipe another project's local Postgres
data. Or raise the ceiling instead of clearing it: Docker Desktop →
Settings → Resources → Virtual disk limit.

**`RuntimeError: can't start new thread`**, or a step failing because a
subprocess/shell couldn't start (e.g. `E: Sub-process returned an error
code` from a hook that ends in `|| true`, which can't fail any other way).
These mean the Docker VM can't create threads or processes — resource
starvation, not a bug in the step that reported it. The Dockerfile avoids
the known trigger (`pip --progress-bar off`), but the VM still needs fixing
or it will resurface at runtime, where uvicorn forks its workers:

- Docker Desktop → Settings → Resources: raise **Memory** (4 GB+) and CPUs.
- Restart Docker Desktop — the VM accumulates pressure over a long session.
- Confirm with `docker run --rm python:3.12-slim-bookworm python -c "import
  threading; t=threading.Thread(target=lambda:None); t.start(); t.join();
  print('threads OK')"`.

**`the --chmod option requires BuildKit`** or other unknown-flag errors.
The Dockerfile is written to build on the classic builder as well as
BuildKit, so this shouldn't happen on a current checkout — but it's the
same tell: if `docker build` prints "Sending build context to Docker
daemon" rather than `[+] Building`, you're on the classic builder, and an
older Dockerfile revision would fail here.

**When local Docker just won't cooperate**, build in Azure instead and skip
it entirely — same image, no local daemon involved:

```bash
az acr build --registry "$ACR_NAME" --image origami-api:latest ./api
```

## 2. Two PostgreSQL databases

The app is architected around two logically separate databases — control plane (tenants,
entitlements, devices, audit) and tenant/farm data (see `ARCHITECTURE.md`, `TENANCY.md`). For a
test deployment, one Flexible Server hosting both databases is enough; split them onto separate
servers later if you need to test that isolation specifically.

```bash
PG_SERVER=origami-pg-test
PG_ADMIN_PASSWORD='<pick a strong password>'

az postgres flexible-server create \
  --resource-group "$RG" --name "$PG_SERVER" --location "$LOCATION" \
  --admin-user origami --admin-password "$PG_ADMIN_PASSWORD" \
  --sku-name Standard_B1ms --tier Burstable --storage-size 32 --version 16 \
  --public-access 0.0.0.0-255.255.255.255   # test-only: opens to all Azure IPs; see note below

az postgres flexible-server db create --resource-group "$RG" --server-name "$PG_SERVER" --database-name origami_control
az postgres flexible-server db create --resource-group "$RG" --server-name "$PG_SERVER" --database-name origami_tenant_shared
```

`--public-access 0.0.0.0-255.255.255.255` is the fastest way to get a test deployment reachable
from an App Service instance without also setting up VNet integration, and it's genuinely wide —
tighten it once this is more than a test (see bottom of this doc). Narrower alternative right now:
`--public-access AzureCloud` restricts it to Azure's own IP ranges (App Service included), which is
already meaningfully tighter than "all of the internet."

Build the two connection strings (`+psycopg`, matching `app/config/settings.py`'s
`postgresql+psycopg://` scheme — not the bare `postgresql://` the Azure CLI's own output shows):

```bash
CONTROL_DATABASE_URL="postgresql+psycopg://origami:${PG_ADMIN_PASSWORD}@${PG_SERVER}.postgres.database.azure.com:5432/origami_control?sslmode=require"
TENANT_DATABASE_URL="postgresql+psycopg://origami:${PG_ADMIN_PASSWORD}@${PG_SERVER}.postgres.database.azure.com:5432/origami_tenant_shared?sslmode=require"
```

## 3. Point the Web App at the image

```bash
az webapp identity assign --resource-group "$RG" --name "$APP_NAME"
PRINCIPAL_ID=$(az webapp identity show --resource-group "$RG" --name "$APP_NAME" --query principalId -o tsv)
ACR_ID=$(az acr show --name "$ACR_NAME" --query id -o tsv)
az role assignment create --assignee "$PRINCIPAL_ID" --scope "$ACR_ID" --role AcrPull

az webapp config container set \
  --resource-group "$RG" --name "$APP_NAME" \
  --container-image-name "$ACR_NAME.azurecr.io/origami-api:latest" \
  --container-registry-url "https://$ACR_NAME.azurecr.io"
```

A managed identity + `AcrPull` role (rather than baking registry credentials into an app setting)
means nothing about registry access lives in plaintext config.

## 4. Application settings

Everything the app reads is an environment variable (`app/config/settings.py`) — nothing is
baked into the image. Generate a real secret rather than using the placeholder below:

```bash
APP_SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")

az webapp config appsettings set --resource-group "$RG" --name "$APP_NAME" --settings \
  WEBSITES_PORT=8000 \
  ENVIRONMENT=staging \
  AUTH_DEV_MODE=true \
  APP_SECRET_KEY="$APP_SECRET_KEY" \
  CONTROL_DATABASE_URL="$CONTROL_DATABASE_URL" \
  TENANT_DATABASE_URL="$TENANT_DATABASE_URL" \
  CORS_ALLOWED_ORIGINS="*" \
  LOG_LEVEL=INFO \
  UVICORN_WORKERS=2
```

Notes on specific settings:

- **`WEBSITES_PORT=8000`** — required. Azure doesn't inject a `PORT` env var the way some other
  platforms do; this tells its front door which port inside the container to route to. The image
  listens on 8000 by default (`api/Dockerfile`).
- **`ENVIRONMENT=staging`, not `production`** — `app/main.py` refuses to boot at all if
  `ENVIRONMENT=production` and `AUTH_DEV_MODE=true` at the same time (verified: this raises
  `RuntimeError` before the app even starts). Dev-mode login only works with a non-`production`
  environment name.
- **`AUTH_DEV_MODE=true`** — the FarmOS tablet contract's own `POST /auth/login` (email+password,
  seeded by `scripts/seed.py`, see step 6) doesn't depend on this flag at all — that login is a
  separate system from OIDC (see `docs/FARMOS_API.md` "Auth: a second, independent system"). This
  flag only affects the platform/admin-web OIDC login path; set it `true` here so that path works
  too without standing up Keycloak.
- **`CORS_ALLOWED_ORIGINS="*"`** — fine for API testing (curl, Postman, the tablet app talking to
  `https://leb-container-test.azurewebsites.net/api/v1`). Narrow it to real origins before anything
  browser-based (admin-web) is pointed at this in a way that matters.
- **Not set**: `REDIS_URL`, `S3_*`, `OIDC_*` — nothing in the API process reads Redis today (only
  the also-not-deployed-here `workers` scaffold would, and it doesn't yet do real work — see
  `ARCHITECTURE.md`), and S3 credentials are only touched inside the presigned-URL endpoint
  handler, not at startup, so leaving them at their harmless defaults doesn't block anything else
  from working. Add real ones when file upload or a background worker actually need to run.

## 5. License lease keys (device activation / offline license testing only)

`app/devices/lease.py` reads `LICENSE_LEASE_PRIVATE_KEY_PATH`/`..._PUBLIC_KEY_PATH` — by default
`./infrastructure/keys/license_lease_*.pem`, resolved inside the container at `/app/infrastructure/
keys/`. `api/docker-entrypoint.sh` generates a keypair there automatically on first boot if none
exists (verified: ran this exact generation code locally and it produces a valid keypair with the
private key at `0600`). Nothing else in the app touches this path at startup, so this never blocks
the API from serving traffic either way.

**For this test deployment, that's deliberately left as container-local, ephemeral storage** — a
restart or redeploy generates a fresh keypair, which invalidates any device leases issued against
the old one. That's fine as long as you're not yet testing device activation/offline licensing
specifically. When you are, mount real persistent storage (Azure Files, via the Web App's **Path
mappings** configuration) at a path outside `/app`, point `LICENSE_LEASE_PRIVATE_KEY_PATH` /
`..._PUBLIC_KEY_PATH` at it as app settings, and the same auto-generate-if-missing entrypoint logic
keeps working — it'll just generate the keypair once and it'll actually survive restarts.

(Don't use Azure's own `/home` auto-persistence trick for this: `api/Dockerfile` deliberately keeps
everything the app needs at `/opt` and `/app`, not under `/home`, specifically because that
persistent mount can shadow whatever was baked into the image there.)

## 6. Restart, migrate, seed, smoke-test

```bash
az webapp restart --resource-group "$RG" --name "$APP_NAME"
```

`api/docker-entrypoint.sh` runs both Alembic upgrades (`alembic -c alembic_control.ini upgrade
head`, then `alembic -c alembic_tenant.ini upgrade head`) on every container start before the
server begins accepting traffic — verified locally against a fresh database: it applies cleanly,
and re-running it is a no-op. Nothing further is required for the schema to be ready.

Watch it come up:

```bash
az webapp log tail --resource-group "$RG" --name "$APP_NAME"
```

Then seed demo data (a platform admin, two demo tenants, and the FarmOS tablet contract's own
seeded users — verified locally, prints the exact credentials it creates):

```bash
az webapp ssh --resource-group "$RG" --name "$APP_NAME"
# inside the container's shell:
python ../scripts/seed.py
```

That prints login credentials for the FarmOS tablet contract (`POST /api/v1/auth/login`) directly —
copy them from its output rather than assuming a fixed password; it's re-run-safe (looks up by
natural key before creating).

Smoke test from your own machine:

```bash
curl https://leb-container-test.azurewebsites.net/health
# {"status":"ok"}

curl -X POST https://leb-container-test.azurewebsites.net/api/v1/auth/login \
  -H 'content-type: application/json' \
  -d '{"email":"<owner email from seed output>","password":"<password from seed output>"}'
# {"access_token":"...","token_type":"bearer"}
```

Both of those — plus the full login → `/auth/me` round trip — are exactly what was verified
locally against a throwaway Postgres instance while building this: same entrypoint, same
`ENVIRONMENT=staging` + `AUTH_DEV_MODE=true` combination, same result.

## 7. Point the tablet app at it

In the OrigamiFarmOS app, Settings → Server connection → Server URL:

```
https://leb-container-test.azurewebsites.net/api/v1
```

(`AppConfig.defaultApiBaseUrl` in that repo defaults to an Android-emulator-local address — this
is what the in-app Settings override, added alongside the app's own data-engine work, is for.)

## Going beyond a test deployment

Not done here, and worth doing before this carries anything that matters:

- **Real auth.** `AUTH_DEV_MODE` must never be `true` where `ENVIRONMENT=production` — the app
  itself refuses to boot that combination. Stand up Keycloak (or another OIDC provider) and point
  `OIDC_ISSUER`/`OIDC_AUDIENCE`/`OIDC_JWKS_URL` at it. The FarmOS tablet contract's own
  email+password login is unaffected either way — it's the platform/admin-web OIDC path this gates.
- **Narrow `CORS_ALLOWED_ORIGINS`** to real origins, and the Postgres `--public-access` rule to
  what actually needs to reach it (ideally VNet integration + private access, not a public IP
  range at all).
- **Split control/tenant databases onto separate servers** if you want to exercise that isolation
  boundary the way `TENANCY.md` describes it, and turn on Postgres backups/HA.
- **Scale beyond one instance.** `RUN_MIGRATIONS=true` (the default) is safe for a single instance
  but would race if multiple instances started concurrently and both tried to migrate. Move
  migrations to a one-off deploy step (`az webapp ssh` + `alembic upgrade head`, or a CI job) and
  set `RUN_MIGRATIONS=false` before scaling out.
- **Real object storage** (`S3_*` settings) once file upload/presigned URLs (`app/files/`) need to
  actually work — Azure Blob Storage's S3-compatible gateway, or real AWS S3.
- **A real backup/export worker** — see `ARCHITECTURE.md` and `workers/main.py`'s own docstring;
  the API/data model for `backup_job`/`tenant_export` exists, the job that actually produces one
  doesn't yet, and `workers/main.py` isn't part of this single-container deployment at all.
