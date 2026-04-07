#cloud-config
packages:
  - curl
  - netcat-openbsd

runcmd:
  - |
    # ============= NETWORKING MODE: ${networking_mode} =============
    # This variable controls whether the cluster uses Tailscale IPs or Hetzner private IPs.
    # To switch modes, update the cluster_networking_mode variable in terraform.
    # ================================================================

    if [ "${networking_mode}" = "tailscale" ]; then
      # TAILSCALE MODE: Use only Tailscale for all K3s networking
      curl -fsSL https://tailscale.com/install.sh | sh || { echo "Tailscale install failed"; sleep 5; }
      tailscale up --authkey="${tailscale_auth_key}" --hostname="${node_name}" || { echo "Tailscale up failed"; sleep 5; }
      
      # Wait up to 60s for Tailscale IP
      TAILSCALE_IP=""
      for i in $(seq 1 12); do
        TAILSCALE_IP=$(tailscale ip -4 2>/dev/null || true)
        if [ -n "$TAILSCALE_IP" ]; then
          echo "Tailscale IP assigned: $TAILSCALE_IP"
          break
        fi
        echo "Waiting for Tailscale IP... ($i/12)"
        sleep 5
      done
      
      if [ -z "$TAILSCALE_IP" ]; then
        echo "ERROR: Worker Tailscale IP not assigned after 60s. Agent join will fail."
        exit 1
      fi
      
      # Advertise private subnet
      tailscale set --advertise-routes=${subnet_cidr} || true
      
      # Wait for control plane on Tailscale
      echo "Waiting for control plane on Tailscale (${cp_ip}:6443)..."
      until nc -z "${cp_ip}" 6443 2>/dev/null; do
        echo "Control plane not yet ready..."
        sleep 5
      done
      
      # Join cluster using Tailscale networking
      curl -sfL https://get.k3s.io | K3S_TOKEN="${token}" K3S_URL="https://${cp_ip}:6443" sh -s - agent \
        --node-ip=$TAILSCALE_IP \
        --flannel-iface=tailscale0 \
        ${join(" ", [for l in node_labels : "--node-label=${l}"])}${length(node_taints) > 0 ? " \\\n        " : ""}${join(" ", [for t in node_taints : "--node-taint=${t}"])}
    else
      # PRIVATE IP MODE: Use Hetzner private IPs (original behavior)
      curl -fsSL https://tailscale.com/install.sh | sh || true
      tailscale up --authkey="${tailscale_auth_key}" --hostname="${node_name}" || true
      
      # Advertise the Hetzner private subnet for home-node Tailscale subnet routing.
      TAILSCALE_IP=$(tailscale ip -4 2>/dev/null || true)
      [ -n "$TAILSCALE_IP" ] && tailscale set --advertise-routes=${subnet_cidr} || true
      
      # Wait for control plane on private IP
      until nc -z ${cp_ip} 6443; do echo "waiting for control plane..."; sleep 5; done
      
      # Join cluster using private IP
      curl -sfL https://get.k3s.io | K3S_TOKEN="${token}" K3S_URL="https://${cp_ip}:6443" sh -s - agent \
        --node-ip=${private_ip} \
        --flannel-iface=enp7s0 \
        ${join(" ", [for l in node_labels : "--node-label=${l}"])}${length(node_taints) > 0 ? " \\\n        " : ""}${join(" ", [for t in node_taints : "--node-taint=${t}"])}
    fi
