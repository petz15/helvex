output "server_public_ipv4" {
  value = module.servers.public_ipv4
}

output "server_private_ipv4" {
  value = module.servers.private_ipv4
}

output "server_ids" {
  value = module.servers.server_ids
}

output "network_id" {
  value = module.network.network_id
}
