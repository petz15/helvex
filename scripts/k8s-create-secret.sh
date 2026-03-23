#!/usr/bin/env bash
# Creates (or updates) the helvex-env K8s Secret from a .env file.
#
# Usage:
#   ./scripts/k8s-create-secret.sh dev          # reads .env, targets helvex-dev namespace
#   ./scripts/k8s-create-secret.sh prod .env.prod
set -euo pipefail

ENV=${1:-dev}
ENV_FILE=${2:-.env}
NAMESPACE="helvex-${ENV}"
SECRET_NAME="helvex-env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: env file '$ENV_FILE' not found" >&2
  exit 1
fi

declare -a ARGS=()
while IFS= read -r line || [[ -n "$line" ]]; do
  # Skip comments and blank lines
  [[ "$line" =~ ^[[:space:]]*# ]] && continue
  [[ -z "${line//[[:space:]]/}" ]] && continue
  # Split on first =
  key="${line%%=*}"
  value="${line#*=}"
  # Strip surrounding single or double quotes and Windows \r
  value="${value#\'}" ; value="${value%\'}"
  value="${value#\"}" ; value="${value%\"}"
  value="${value%$'\r'}"
  ARGS+=("--from-literal=${key}=${value}")
done < "$ENV_FILE"

kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

kubectl create secret generic "$SECRET_NAME" \
  --namespace "$NAMESPACE" \
  --dry-run=client -o yaml \
  "${ARGS[@]}" | kubectl apply -f -

echo "Secret '$SECRET_NAME' applied to namespace '$NAMESPACE'"
