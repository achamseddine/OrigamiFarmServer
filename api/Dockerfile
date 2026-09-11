# Multi-stage build for Origami Server — the API and the admin console in
# one image. Suitable for a single-container PaaS deployment (e.g. Azure Web
# App for Containers) as well as docker-compose — see
# ../docs/AZURE_DEPLOYMENT.md.
#
# BUILD CONTEXT IS THE REPOSITORY ROOT, not ./api, because the image needs
# admin-web/ and scripts/ as well:
#   docker build -f api/Dockerfile -t origami-api .
#
# NO apt-get ANYWHERE IN THIS FILE, AND IT SHOULD STAY THAT WAY.
#
# Every dependency in requirements.txt resolves to a prebuilt manylinux
# wheel for cp312/x86_64 — verified with:
#   pip download -r requirements.txt --only-binary=:all: \
#     --platform manylinux2014_x86_64 --python-version 3.12 \
#     --implementation cp --abi cp312 -d /tmp/wheels
# so there is nothing to compile and no need for gcc or libpq-dev. In
# particular psycopg[binary] vendors its own libpq (plus OpenSSL, Kerberos,
# LDAP, SASL) inside psycopg_binary.libs/, so no system libpq5 is needed
# either — `ldd` on its extension resolves everything to bundled copies.
#
# This isn't just tidiness: `apt-get update` is the single most fragile
# step in a Docker build (archive key rotation, mirror hiccups, a full
# Docker VM, seccomp differences between engine versions — all seen on
# this project). Not needing the network or the package manager at build
# time removes that whole class of failure, and makes the build faster and
# the image smaller as a bonus.
#
# The `--only-binary=:all:` flag below enforces this: if a future
# dependency would need compiling, pip fails loudly here rather than the
# build mysteriously breaking on a missing compiler.
#
# Debian release is pinned rather than floating (`python:3.12-slim` follows
# whatever Debian is current) so the same commit builds the same way later.
# The Node stage is pinned to the same Debian release for the same reason,
# and needs no apt either — npm ships in the base image.

FROM node:20-bookworm-slim AS console

# Node builds a V8 worker pool sized to the CPU count before it runs a
# single line, and on a thread-starved Docker VM that abort()s at startup:
#   Assertion failed: (0) == (uv_thread_create(...))
# which kills `npm ci` with exit 134 and looks like a broken install rather
# than a starved builder. Pool size 0 runs those tasks on the main thread.
# Same class of failure as pip's progress-bar thread further down, and the
# same caveat: this makes the build survive a squeezed VM, it does not fix
# one — see ../docs/AZURE_DEPLOYMENT.md.
ENV NODE_OPTIONS=--v8-pool-size=0

WORKDIR /build
COPY admin-web/package.json admin-web/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY admin-web/ ./
# No NEXT_PUBLIC_API_BASE_URL on purpose: the console is served by the API
# on the same origin here, so its API base stays empty and every request is
# relative. Baking a hostname in would tie the image to one deployment.
RUN npm run build


FROM python:3.12-slim-bookworm AS builder

RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

WORKDIR /app
COPY api/requirements.txt .
# --progress-bar off: pip's default progress bar (rich) starts a background
# refresh thread, which fails with "RuntimeError: can't start new thread" on
# a resource-starved Docker VM and takes the whole build down with it. The
# bar is useless in a non-TTY build log anyway. --only-binary=:all: keeps
# the no-apt guarantee above honest: a dependency that would need compiling
# fails here, loudly, instead of on a missing gcc.
RUN pip install --no-cache-dir --only-binary=:all: --progress-bar off -r requirements.txt


FROM python:3.12-slim-bookworm AS final

# `useradd` ships in the base image (passwd) — no package install needed.
RUN useradd --uid 1000 --shell /usr/sbin/nologin --no-create-home origami

# Dependencies live in /opt, never under /home: Azure Web App for
# Containers (Linux) auto-mounts persistent storage at /home inside the
# container, which would silently shadow anything baked into the image
# there.
COPY --from=builder /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ADMIN_WEB_DIR=/app/admin-web

WORKDIR /app
COPY --chown=origami:origami api/ .
COPY --chown=origami:origami scripts/ ./scripts/

# The compiled console. app/main.py mounts this at / after every router, so
# the API keeps its own paths and everything else falls through to here —
# no Node runtime in this image, and one process serving both.
COPY --from=console --chown=origami:origami /build/out ./admin-web

# Plain COPY + chmod rather than `COPY --chmod=`: the latter needs
# BuildKit, and this has to build on the classic builder too (the tell is
# "Sending build context to Docker daemon" rather than "[+] Building").
COPY api/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 755 /usr/local/bin/docker-entrypoint.sh \
    && chown origami:origami /app

USER origami

EXPOSE 8000

# Uses the interpreter that's already here rather than pulling in curl —
# see the no-apt note at the top. `/health` is unauthenticated and does no
# real work (app/main.py), so it's safe and cheap for this and for a
# platform's own probe (e.g. Azure's WEBSITES_PORT-based ping).
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import os,sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://localhost:'+os.environ.get('PORT','8000')+'/health', timeout=4).status==200 else 1)"]

ENTRYPOINT ["docker-entrypoint.sh"]

# ${PORT:-8000}: falls back to 8000 (this repo's convention everywhere
# else — README, .env.example, admin-web) but honors PORT when the
# platform injects one (e.g. Cloud Run, Heroku-style PaaS). Azure Web App
# for Containers doesn't inject PORT; point its WEBSITES_PORT setting at
# 8000 instead — see ../docs/AZURE_DEPLOYMENT.md.
# ${UVICORN_WORKERS:-2}: a starting point for a small test deployment, not
# a tuned number — see that same doc for how to size it for real load.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${UVICORN_WORKERS:-2}"]
