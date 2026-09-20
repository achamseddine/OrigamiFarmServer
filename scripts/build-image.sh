#!/usr/bin/env bash
# Build the Origami image with its version stamped in.
#
# A plain `docker build -f api/Dockerfile .` works, but produces an image
# that reports version "unknown" from /health — which defeats the one
# thing the stamp exists for, telling a deployed build apart from the last
# one. This fills the build args in from git so that cannot happen by
# forgetting.
#
#   ./scripts/build-image.sh                          # -> origami-api:latest
#   ./scripts/build-image.sh unileb.azurecr.io/origami-api:latest
#
# Afterwards:
#   docker run --rm -p 8000:8000 -e RUN_MIGRATIONS=false <tag>
#   curl localhost:8000/health   # the version should be the commit below
set -euo pipefail

cd "$(dirname "$0")/.."

TAG="${1:-origami-api:latest}"

VERSION="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
# A build from a dirty tree is not the commit it claims to be, and finding
# that out from a confusing deployment later is expensive.
if ! git diff-index --quiet HEAD -- 2>/dev/null; then
    VERSION="${VERSION}-dirty"
fi
BUILT_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# linux/amd64 always: Azure App Service runs amd64, and an arm64 image
# built on an M-series Mac fails at container start rather than at build
# or push, which is a confusing one to hit blind.
echo "Building $TAG"
echo "  version:  $VERSION"
echo "  built at: $BUILT_AT"

docker build \
    --platform linux/amd64 \
    -f api/Dockerfile \
    --build-arg APP_VERSION="$VERSION" \
    --build-arg APP_BUILT_AT="$BUILT_AT" \
    -t "$TAG" \
    .

echo
echo "Built $TAG — /health will report version $VERSION"
