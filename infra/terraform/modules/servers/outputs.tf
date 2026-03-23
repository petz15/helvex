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
