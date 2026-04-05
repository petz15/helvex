#cloud-config
packages:
  - curl
  - netcat-openbsd

runcmd:
  # Install and join Tailscale (for admin access and potential future multi-node support)
  - |
    curl -fsSL https://tailscale.com/install.sh | sh
  - |
    tailscale up --authkey="${tailscale_auth_key}" --hostname="${node_name}" --accept-routes
  - 'until nc -z ${cp_ip} 6443; do echo "waiting for control plane..."; sleep 5; done'
  - |
    curl -sfL https://get.k3s.io | K3S_TOKEN="${token}" K3S_URL="https://${cp_ip}:6443" sh -s - agent \
      --node-ip=${private_ip} \
      --flannel-iface=enp7s0 \
      ${join(" ", [for l in node_labels : "--node-label=${l}"])}${length(node_taints) > 0 ? " \\\n      " : ""}${join(" ", [for t in node_taints : "--node-taint=${t}"])}
