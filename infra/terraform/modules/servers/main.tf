locals {
  # The control plane's private IP — workers need it to join
  cp_ip = one([for k, v in var.servers : v.private_ip if v.role == "k3s-control-plane"])

  # DB nodes: those with the database node label
  db_servers = {
    for k, v in var.servers : k => v
    if contains(v.node_labels, "helvex.io/role=database")
  }

  # Per-server cloud-init user_data
  user_data = {
    for k, v in var.servers : k => (
      v.role == "k3s-control-plane"
      ? templatefile("${path.module}/templates/control-plane.yaml.tpl", {
          token      = var.k3s_token
          private_ip = v.private_ip
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
