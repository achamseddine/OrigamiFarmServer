# Multi-stage build: the `builder` stage has the compiler toolchain
# psycopg[binary]/cryptography need to install; the final image only ships
# the installed packages and app code, not gcc/libpq-dev — smaller image,
# smaller attack surface. Suitable for a single-container PaaS deployment
# (e.g. Azure Web App for Containers) as well as docker-compose — see
# ../docs/AZURE_DEPLOYMENT.md.
#
# Dependencies install to /opt/venv, not the app user's home directory:
# Azure Web App for Containers (Linux) auto-mounts persistent storage at
# /home inside the container, which would silently shadow anything baked
# into the image there. /opt is never touched by that mount.

# Debian release pinned deliberately, for two reasons:
#
# 1. Reproducibility: `python:3.12-slim` follows whatever Debian is current
#    (it has since rolled to trixie/13), so an unpinned base means the
#    build environment can change under you between two builds of the same
#    commit.
# 2. trixie was observed failing to build on an older Docker Engine (one
#    old enough to still default to the classic builder), during apt-get:
#      E: Problem executing scripts APT::Update::Post-Invoke 'rm -f ... || true'
#      E: Sub-process returned an error code
#    Note the `|| true`: that can only return non-zero if the shell itself
#    was killed or couldn't start, which points at a syscall the engine's
#    seccomp profile doesn't allow rather than anything about the command.
#    Newer base + older engine is the general shape of it; bookworm avoids
#    it. Updating Docker is the other fix if you want to move to trixie.
FROM python:3.12-slim-bookworm AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


FROM python:3.12-slim-bookworm AS final

# libpq5 is psycopg[binary]'s runtime dependency (not libpq-dev/gcc — those
# were only needed to build it, in the stage above). curl backs the
# HEALTHCHECK below.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 1000 --shell /usr/sbin/nologin --no-create-home origami

COPY --from=builder /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY --chown=origami:origami . .
# Plain COPY + chmod rather than `COPY --chmod=`: the latter needs
# BuildKit, and this has to build on the classic builder too (which is
# still what `docker build` uses on some setups — you'll see "Sending
# build context to Docker daemon" rather than "[+] Building").
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 755 /usr/local/bin/docker-entrypoint.sh

# /app (code + wherever license keys land by default) is owned by origami;
# root still owns /opt/venv, which is fine — origami only needs to read it.
RUN chown origami:origami /app

USER origami

EXPOSE 8000

# `/health` is deliberately unauthenticated and does no real work (see
# app/main.py) — safe and cheap for both this and a platform's own health
# probe (e.g. Azure's WEBSITES_PORT-based ping).
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS "http://localhost:${PORT:-8000}/health" || exit 1

ENTRYPOINT ["docker-entrypoint.sh"]

# ${PORT:-8000}: falls back to 8000 (this repo's convention everywhere
# else — README, .env.example, admin-web) but honors PORT when the
# platform injects one (e.g. Cloud Run, Heroku-style PaaS). Azure Web App
# for Containers doesn't inject PORT; point its WEBSITES_PORT setting at
# 8000 instead — see ../docs/AZURE_DEPLOYMENT.md.
# ${UVICORN_WORKERS:-2}: a starting point for a small test deployment, not
# a tuned number — see that same doc for how to size it for real load.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${UVICORN_WORKERS:-2}"]
