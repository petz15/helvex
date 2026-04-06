#cloud-config
packages:
  - curl
  - netcat-openbsd

runcmd:
  - |
    # Install Tailscale for admin access — non-fatal, does not block k3s agent join
    curl -fsSL https://tailscale.com/install.sh | sh
    tailscale up --authkey="${tailscale_auth_key}" --hostname="${node_name}" || true
    # Advertise the Hetzner private subnet for home-node Tailscale subnet routing.
    # Requires one-time approval in admin.tailscale.com → Machines → Edit route settings.
    TAILSCALE_IP=$(tailscale ip -4 2>/dev/null || true)
    [ -n "$TAILSCALE_IP" ] && tailscale set --advertise-routes=${subnet_cidr} || true
  - 'until nc -z ${cp_ip} 6443; do echo "waiting for control plane..."; sleep 5; done'
  - |
    curl -sfL https://get.k3s.io | K3S_TOKEN="${token}" K3S_URL="https://${cp_ip}:6443" sh -s - agent \
      --node-ip=${private_ip} \
      --flannel-iface=enp7s0 \
      ${join(" ", [for l in node_labels : "--node-label=${l}"])}${length(node_taints) > 0 ? " \\\n      " : ""}${join(" ", [for t in node_taints : "--node-taint=${t}"])}
