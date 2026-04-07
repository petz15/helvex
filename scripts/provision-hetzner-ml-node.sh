#!/usr/bin/env bash
# provision-hetzner-ml-node.sh
#
# Creates a Hetzner Cloud server, attaches it to the private network,
# then joins it to k3s as an ML worker node.
#
# Requirements:
#   - hcloud CLI configured (HCLOUD_TOKEN set or hcloud context configured)
#   - ssh/scp available locally
#   - private key for the target SSH key available locally
#
# Usage:
#   bash scripts/provision-hetzner-ml-node.sh \
#     --name helvex-ml-1 \
#     --type cpx31 \
#     --image ubuntu-24.04 \
#     --location nbg1 \
#     --network helvex-prod-net \
#     --private-ip 10.0.1.21 \
#     --ssh-key-name helvex_prod_sshkey_v1 \
#     --ssh-user ubuntu \
#     --cp-host ubuntu@<app1-public-ip> \
#     --cp-private-ip 10.0.1.10

set -euo pipefail

NAME=""
TYPE="cpx31"
IMAGE="ubuntu-24.04"
LOCATION="nbg1"
NETWORK_NAME=""
PRIVATE_IP=""
SSH_KEY_NAME=""
SSH_USER="ubuntu"
CP_HOST=""
CP_PRIVATE_IP=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --name) NAME="$2"; shift 2 ;;
    --type) TYPE="$2"; shift 2 ;;
    --image) IMAGE="$2"; shift 2 ;;
    --location) LOCATION="$2"; shift 2 ;;
    --network) NETWORK_NAME="$2"; shift 2 ;;
    --private-ip) PRIVATE_IP="$2"; shift 2 ;;
    --ssh-key-name) SSH_KEY_NAME="$2"; shift 2 ;;
    --ssh-user) SSH_USER="$2"; shift 2 ;;
    --cp-host) CP_HOST="$2"; shift 2 ;;
    --cp-private-ip) CP_PRIVATE_IP="$2"; shift 2 ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

if [[ -z "$NAME" || -z "$NETWORK_NAME" || -z "$PRIVATE_IP" || -z "$SSH_KEY_NAME" || -z "$CP_HOST" || -z "$CP_PRIVATE_IP" ]]; then
  echo "Missing required arguments." >&2
  exit 1
fi

if ! command -v hcloud >/dev/null 2>&1; then
  echo "hcloud CLI not found." >&2
  exit 1
fi

if ! command -v ssh >/dev/null 2>&1 || ! command -v scp >/dev/null 2>&1; then
  echo "ssh and scp are required." >&2
  exit 1
fi

echo "Creating Hetzner server '$NAME'..."
SERVER_ID=$(hcloud server create \
  --name "$NAME" \
  --type "$TYPE" \
  --image "$IMAGE" \
  --location "$LOCATION" \
  --ssh-key "$SSH_KEY_NAME" \
  --output columns=id --no-header)

echo "Attaching '$NAME' to network '$NETWORK_NAME' with IP $PRIVATE_IP..."
hcloud server add-to-network "$NAME" --network "$NETWORK_NAME" --ip "$PRIVATE_IP"

PUBLIC_IP=$(hcloud server describe "$NAME" --output columns=ipv4 --no-header)

if [[ -z "$PUBLIC_IP" || "$PUBLIC_IP" == "null" ]]; then
  echo "Could not determine public IP for '$NAME'." >&2
  exit 1
fi

echo "Waiting for SSH on $PUBLIC_IP..."
until ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 "$SSH_USER@$PUBLIC_IP" "echo ready" >/dev/null 2>&1; do
  sleep 5
done

echo "Reading join token from control plane..."
K3S_TOKEN=$(ssh "$CP_HOST" "sudo cat /var/lib/rancher/k3s/server/node-token")

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JOIN_SCRIPT="$SCRIPT_DIR/join-home-node.sh"

if [[ ! -f "$JOIN_SCRIPT" ]]; then
  echo "join-home-node.sh not found at $JOIN_SCRIPT" >&2
  exit 1
fi

echo "Copying join script..."
scp "$JOIN_SCRIPT" "$SSH_USER@$PUBLIC_IP:/tmp/join-home-node.sh"

echo "Joining node to cluster..."
ssh "$SSH_USER@$PUBLIC_IP" "sudo bash /tmp/join-home-node.sh '$CP_PRIVATE_IP' '$K3S_TOKEN' '$PRIVATE_IP' '$NAME'"

echo "Done. Verify on control plane:"
echo "  kubectl get nodes -o wide"
echo "  kubectl describe node $NAME | grep -E 'Taints|workload=|location='"
