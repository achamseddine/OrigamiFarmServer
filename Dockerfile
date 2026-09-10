# Origami Server — one image containing both the API and the admin console.
#
# The console is a client-side app, so it is compiled to static files here
# and served by the API process itself. Nothing Node-related survives into
# the runtime image, and the container runs a single process.

# --- stage 1: compile the admin console --------------------------------

FROM node:20-slim AS console

WORKDIR /build

COPY admin-web/package.json admin-web/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY admin-web/ ./
# No NEXT_PUBLIC_API_BASE_URL: the console and the API share an origin in
# this image, so the API base stays empty and requests are relative.
RUN npm run build

# --- stage 2: runtime ---------------------------------------------------

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ADMIN_WEB_DIR=/app/admin-web

WORKDIR /app

# Every dependency ships a manylinux wheel — psycopg[binary] bundles libpq —
# so the image needs no compiler and no apt packages at all.
COPY api/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY api/ ./
COPY scripts/ ./scripts/
COPY --from=console /build/out ./admin-web
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["docker-entrypoint.sh"]
