#!/bin/bash
set -e

# Build and push pgvector PostgreSQL 16 image
# This script compiles pgvector on top of the official CloudNativePG PostgreSQL 16 image
# with a semantic version tag that satisfies CloudNativePG admission webhook validation.
#
# Usage:
#   bash infra/build-pgvector-image.sh ghcr.io/myusername/pgvector-postgresql 16.3.0
#
# Then update values.yaml to set postgres.imageName to the image reference, e.g.:
#   postgres:
#     imageName: ghcr.io/myusername/pgvector-postgresql:16.3.0

REGISTRY=${1:-ghcr.io/$(whoami)/pgvector-postgresql}
VERSION=${2:-16.3.0}

IMAGE="${REGISTRY}:${VERSION}"

echo "Building ${IMAGE}..."
docker build -f infra/Dockerfile.pgvector -t "${IMAGE}" .

echo ""
echo "Push image with:"
echo "  docker push ${IMAGE}"
echo ""
echo "Then set in values.yaml:"
echo "  postgres:"
echo "    imageName: ${IMAGE}"
echo ""
echo "Or pass via helmfile:"
echo "  helmfile -e prod apply -l release=postgres --state-values-set postgres.imageName=${IMAGE}"
