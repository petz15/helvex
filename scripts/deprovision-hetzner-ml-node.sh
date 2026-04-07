#!/usr/bin/env bash
# deprovision-hetzner-ml-node.sh
#
# Safely removes a Terraform-unmanaged Hetzner ML node:
# 1) cordon + drain node from Kubernetes
# 2) delete Kubernetes node object
# 3) delete Hetzner server
#
# Usage:
#   bash scripts/deprovision-hetzner-ml-node.sh \
#     --name helvex-ml-1 \
#     --cp-host ubuntu@<app1-public-ip>
#
# Options:
#   --skip-drain         Skip kubectl cordon/drain/delete-node (dangerous)
#   --yes                Non-interactive mode

set -euo pipefail

NAME=""
CP_HOST=""
SKIP_DRAIN="false"
AUTO_YES="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --name) NAME="$2"; shift 2 ;;
    --cp-host) CP_HOST="$2"; shift 2 ;;
    --skip-drain) SKIP_DRAIN="true"; shift 1 ;;
    --yes) AUTO_YES="true"; shift 1 ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

if [[ -z "$NAME" ]]; then
  echo "Missing required argument --name" >&2
  exit 1
fi

if [[ "$SKIP_DRAIN" != "true" && -z "$CP_HOST" ]]; then
  echo "Missing required argument --cp-host (unless --skip-drain is set)" >&2
  exit 1
fi

if ! command -v hcloud >/dev/null 2>&1; then
  echo "hcloud CLI not found." >&2
  exit 1
fi

if [[ "$AUTO_YES" != "true" ]]; then
  echo "About to deprovision node '$NAME'."
  echo "This will remove workloads and then delete the Hetzner server."
  read -r -p "Continue? [y/N] " answer
  if [[ "$answer" != "y" && "$answer" != "Y" ]]; then
    echo "Cancelled."
    exit 0
  fi
fi

if [[ "$SKIP_DRAIN" != "true" ]]; then
  echo "Cordoning and draining Kubernetes node '$NAME'..."
  ssh "$CP_HOST" "kubectl cordon '$NAME' || true"
  ssh "$CP_HOST" "kubectl drain '$NAME' --ignore-daemonsets --delete-emptydir-data --force --grace-period=60 --timeout=10m || true"
  ssh "$CP_HOST" "kubectl delete node '$NAME' || true"
fi

echo "Deleting Hetzner server '$NAME'..."
hcloud server delete "$NAME"

echo "Done."
if [[ "$SKIP_DRAIN" != "true" ]]; then
  echo "Verify remaining nodes:"
  echo "  ssh $CP_HOST \"kubectl get nodes -o wide\""
fi
