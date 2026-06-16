resource "hcloud_firewall" "this" {
  name = var.name

  rule {
    direction  = "in"
    protocol   = "tcp"
    port       = "22"
    source_ips = var.admin_cidrs
  }

  rule {
    direction  = "in"
    protocol   = "tcp"
    port       = "6443"
    source_ips = var.admin_cidrs
  }

  # Allow worker nodes on private subnet to join/control-plane API.
  rule {
    direction  = "in"
    protocol   = "tcp"
    port       = "6443"
    source_ips = [var.cluster_private_cidr]
  }

  # Flannel VXLAN node-to-node traffic.
  rule {
    direction  = "in"
    protocol   = "udp"
    port       = "8472"
    source_ips = [var.cluster_private_cidr]
  }

  # Kubelet API used by control-plane for logs/exec and node operations.
  rule {
    direction  = "in"
    protocol   = "tcp"
    port       = "10250"
    source_ips = [var.cluster_private_cidr]
  }

  rule {
    direction  = "in"
    protocol   = "tcp"
    port       = "80"
    source_ips = ["0.0.0.0/0", "::/0"]
  }

  rule {
    direction  = "in"
    protocol   = "tcp"
    port       = "443"
    source_ips = ["0.0.0.0/0", "::/0"]
  }

  # ICMP restricted to admins only — public 0.0.0.0/0 ICMP enables trivial
  # host discovery (ping sweeps) with no operational benefit.
  rule {
    direction  = "in"
    protocol   = "icmp"
    source_ips = var.admin_cidrs
  }

  # ── Egress ──────────────────────────────────────────────────────────────
  # Outbound is scoped to what the app/cluster actually needs, instead of
  # 1-65535/0.0.0.0/0. A compromised pod or node can otherwise reach any
  # port on any host (C2 beaconing, data exfil, scanning). Node-to-node
  # cluster ports (6443/8472/10250) are scoped to the private subnet; public
  # internet egress is limited to HTTPS/DNS/NTP, which covers GHCR pulls,
  # Anthropic/Serper/Zefix APIs, Let's Encrypt, and S3-compatible backups.

  rule {
    direction       = "out"
    protocol        = "tcp"
    port            = "443"
    destination_ips = ["0.0.0.0/0", "::/0"]
  }

  rule {
    direction       = "out"
    protocol        = "tcp"
    port            = "53"
    destination_ips = ["0.0.0.0/0", "::/0"]
  }

  rule {
    direction       = "out"
    protocol        = "udp"
    port            = "53"
    destination_ips = ["0.0.0.0/0", "::/0"]
  }

  rule {
    direction       = "out"
    protocol        = "udp"
    port            = "123"
    destination_ips = ["0.0.0.0/0", "::/0"]
  }

  rule {
    direction       = "out"
    protocol        = "tcp"
    port            = "6443"
    destination_ips = [var.cluster_private_cidr]
  }

  rule {
    direction       = "out"
    protocol        = "udp"
    port            = "8472"
    destination_ips = [var.cluster_private_cidr]
  }

  rule {
    direction       = "out"
    protocol        = "tcp"
    port            = "10250"
    destination_ips = [var.cluster_private_cidr]
  }

  # SSH out to GitHub for the cloud-init `git clone` step and any future
  # git-over-ssh use from the nodes.
  rule {
    direction       = "out"
    protocol        = "tcp"
    port            = "22"
    destination_ips = ["0.0.0.0/0", "::/0"]
  }

  # Hetzner Storage Box SFTP (weekly export / restore-point-sync jobs) — default port 23.
  rule {
    direction       = "out"
    protocol        = "tcp"
    port            = "23"
    destination_ips = ["0.0.0.0/0", "::/0"]
  }

  rule {
    direction       = "out"
    protocol        = "icmp"
    destination_ips = var.admin_cidrs
  }
}
