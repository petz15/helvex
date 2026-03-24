output "network_id" {
  value = hcloud_network.this.id
}

output "network_name" {
  value = hcloud_network.this.name
}

output "subnet_id" {
  value = hcloud_network_subnet.this.id
}
