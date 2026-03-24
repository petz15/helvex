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

output "lb_ipv4" {
  description = "Load balancer public IPv4 — point your DNS A record here"
  value       = module.loadbalancer.lb_ipv4
}

output "lb_ipv6" {
  description = "Load balancer public IPv6 — point your DNS AAAA record here"
  value       = module.loadbalancer.lb_ipv6
}
