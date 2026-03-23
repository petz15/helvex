resource "hcloud_server" "this" {
  for_each = var.servers

  name        = "${var.name_prefix}-${each.key}"
  server_type = each.value.server_type
  image       = var.image
  location    = var.location
  ssh_keys    = var.ssh_keys

  labels = {
    role = each.value.role
  }

  network {
    network_id = var.network_id
  }

  firewall_ids = [var.firewall_id]
}
