output "public_ipv4" {
  value = {
    for k, s in hcloud_server.this :
    k => s.ipv4_address
  }
}

output "private_ipv4" {
  value = {
    for k, s in hcloud_server.this :
    k => s.network[0].ip
  }
}

output "server_ids" {
  value = {
    for k, s in hcloud_server.this :
    k => s.id
  }
}

output "volume_ids" {
  description = "Persistent data volume IDs keyed by server name (database nodes only)"
  value = {
    for k, v in hcloud_volume.db :
    k => v.id
  }
}
