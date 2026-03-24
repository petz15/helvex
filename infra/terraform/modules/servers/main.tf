locals {
  # The control plane's private IP — workers need it to join
  cp_ip = one([for k, v in var.servers : v.private_ip if v.role == "k3s-control-plane"])

  # DB nodes: those with the database node label
  db_servers = {
    for k, v in var.servers : k => v
    if contains(v.node_labels, "helvex.io/role=database")
  }

  # Control-plane nodes get a pre-allocated primary IP for stable TLS SAN
  cp_servers = {
    for k, v in var.servers : k => v
    if v.role == "k3s-control-plane"
  }
}

# Pre-allocate a static public IP for each control-plane node.
# Known before server creation — used in cloud-init for --tls-san.
resource "hcloud_primary_ip" "cp" {
  for_each = local.cp_servers

  name          = "${var.name_prefix}-${each.key}-ip"
  type          = "ipv4"
  location      = var.location
  assignee_type = "server"
  auto_delete   = false
}

locals {
  # Per-server cloud-init user_data
  user_data = {
    for k, v in var.servers : k => (
      v.role == "k3s-control-plane"
      ? templatefile("${path.module}/templates/control-plane.yaml.tpl", {
          token      = var.k3s_token
          private_ip = v.private_ip
          public_ip  = hcloud_primary_ip.cp[k].ip_address
        })
      : templatefile("${path.module}/templates/worker.yaml.tpl", {
          token       = var.k3s_token
          private_ip  = v.private_ip
          cp_ip       = local.cp_ip
          node_labels = v.node_labels
          node_taints = v.node_taints
        })
    )
  }
}

resource "hcloud_server" "this" {
  for_each = var.servers

  name        = "${var.name_prefix}-${each.key}"
  server_type = each.value.server_type
  image       = var.image
  location    = var.location
  ssh_keys    = var.ssh_keys
  user_data   = local.user_data[each.key]

  labels = {
    role = each.value.role
  }

  # Assign pre-allocated primary IP to control-plane nodes
  dynamic "public_net" {
    for_each = each.value.role == "k3s-control-plane" ? [1] : []
    content {
      ipv4_enabled = true
      ipv4         = hcloud_primary_ip.cp[each.key].id
    }
  }

  network {
    # subnet_id in the network block creates an implicit dependency:
    # Hetzner subnet must exist before servers can be assigned IPs on it.
    network_id = var.network_id
    ip         = each.value.private_ip
  }

  firewall_ids = [var.firewall_id]
}

# Persistent data volume for database nodes — survives server replacement
resource "hcloud_volume" "db" {
  for_each = var.db_volume_size_gb > 0 ? local.db_servers : {}

  name     = "${var.name_prefix}-${each.key}-data"
  size     = var.db_volume_size_gb
  location = var.location
  format   = "ext4"

  labels = {
    role = "database-data"
  }

}

resource "hcloud_volume_attachment" "db" {
  for_each = var.db_volume_size_gb > 0 ? local.db_servers : {}

  volume_id = hcloud_volume.db[each.key].id
  server_id = hcloud_server.this[each.key].id
  automount = true
}
