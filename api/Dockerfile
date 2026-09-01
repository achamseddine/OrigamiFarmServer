# Multi-stage build for the Origami Server API. Suitable for a
# single-container PaaS deployment (e.g. Azure Web App for Containers) as
# well as docker-compose — see ../docs/AZURE_DEPLOYMENT.md.
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

FROM python:3.12-slim-bookworm AS builder

RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --only-binary=:all: -r requirements.txt


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
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY --chown=origami:origami . .

# Plain COPY + chmod rather than `COPY --chmod=`: the latter needs
# BuildKit, and this has to build on the classic builder too (the tell is
# "Sending build context to Docker daemon" rather than "[+] Building").
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
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
