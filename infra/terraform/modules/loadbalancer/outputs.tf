output "lb_id" {
  value = hcloud_load_balancer.this.id
}

output "lb_ipv4" {
  description = "Public IPv4 of the load balancer — use as DNS A record"
  value       = hcloud_load_balancer.this.ipv4
}

output "lb_ipv6" {
  description = "Public IPv6 of the load balancer — use as DNS AAAA record"
  value       = hcloud_load_balancer.this.ipv6
}
