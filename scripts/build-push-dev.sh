#!/usr/bin/env bash
# Build the Docker image and push to the local dev registry.
#
# Run this on the dev server (or from a machine that can reach DEV_SERVER_IP:5000).
# Usage: ./scripts/build-push-dev.sh [DEV_SERVER_IP]
set -euo pipefail

REGISTRY="${1:-192.168.1.100}:5000"
IMAGE="${REGISTRY}/helvex-app"
TAG="${2:-latest}"

BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
BUILD_GIT_SHA="$(git rev-parse --short=12 HEAD 2>/dev/null || echo unknown)"

echo "==> Building ${IMAGE}:${TAG}"
docker build \
  --build-arg BUILD_DATE="$BUILD_DATE" \
  --build-arg BUILD_GIT_SHA="$BUILD_GIT_SHA" \
  -t "${IMAGE}:${TAG}" \
  -t "${IMAGE}:${BUILD_GIT_SHA}" \
  .

echo "==> Pushing ${IMAGE}:${TAG}"
docker push "${IMAGE}:${TAG}"
docker push "${IMAGE}:${BUILD_GIT_SHA}"

echo "==> Done. Deploy with:"
echo "    cd infra && helmfile -e dev apply"
