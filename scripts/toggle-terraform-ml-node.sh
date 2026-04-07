#!/usr/bin/env bash
# toggle-terraform-ml-node.sh
#
# Tiny helper to enable/disable one Terraform-managed ML node.
# It writes infra/terraform/envs/prod/ml_nodes.auto.tfvars.json
# and optionally runs terraform apply.
#
# Usage:
#   bash scripts/toggle-terraform-ml-node.sh enable \
#     --name helvex-ml-1 \
#     --private-ip 10.0.1.21
#
#   bash scripts/toggle-terraform-ml-node.sh disable --name helvex-ml-1
#
# Notes:
# - This helper manages exactly one node in its own auto tfvars file.
# - Existing nodes in terraform.tfvars (ml_nodes) are not modified.

set -euo pipefail

ACTION="${1:-}"
if [[ "$ACTION" != "enable" && "$ACTION" != "disable" ]]; then
  echo "Usage: $0 <enable|disable> [options]" >&2
  exit 1
fi
shift

NAME=""
PRIVATE_IP=""
SERVER_TYPE="cpx31"
KEY="ml1"
TF_DIR="infra/terraform/envs/prod"
NO_APPLY="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --name) NAME="$2"; shift 2 ;;
    --private-ip) PRIVATE_IP="$2"; shift 2 ;;
    --type) SERVER_TYPE="$2"; shift 2 ;;
    --key) KEY="$2"; shift 2 ;;
    --tf-dir) TF_DIR="$2"; shift 2 ;;
    --no-apply) NO_APPLY="true"; shift 1 ;;
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

TFVARS_FILE="$TF_DIR/ml_nodes.auto.tfvars.json"
mkdir -p "$TF_DIR"

if [[ "$ACTION" == "enable" ]]; then
  if [[ -z "$PRIVATE_IP" ]]; then
    echo "Missing required argument --private-ip for enable" >&2
    exit 1
  fi

  cat > "$TFVARS_FILE" <<EOF
{
  "ml_nodes": {
    "$KEY": {
      "server_type": "$SERVER_TYPE",
      "role": "k3s-worker",
      "private_ip": "$PRIVATE_IP",
      "node_labels": ["workload=ml", "location=cloud"],
      "node_taints": ["workload=ml:NoSchedule"]
    }
  }
}
EOF

  echo "Wrote $TFVARS_FILE with ML node '$NAME' (key '$KEY')."
else
  cat > "$TFVARS_FILE" <<EOF
{
  "ml_nodes": {}
}
EOF

  echo "Wrote $TFVARS_FILE with ml_nodes disabled for helper-managed node '$NAME'."
fi

if [[ "$NO_APPLY" == "true" ]]; then
  echo "Skipping terraform apply (--no-apply set)."
  exit 0
fi

if ! command -v terraform >/dev/null 2>&1; then
  echo "terraform CLI not found. Run apply manually in $TF_DIR." >&2
  exit 1
fi

echo "Running terraform apply in $TF_DIR ..."
terraform -chdir="$TF_DIR" apply
