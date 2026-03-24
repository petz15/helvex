resource "hcloud_load_balancer" "this" {
  name               = var.name
  load_balancer_type = var.lb_type
  location           = var.location
}

# Attach LB to the private network so it reaches servers via private IPs
resource "hcloud_load_balancer_network" "this" {
  load_balancer_id = hcloud_load_balancer.this.id
  network_id       = var.network_id
}

# Add each k3s node as a target — traffic routed via private IP
resource "hcloud_load_balancer_target" "this" {
  for_each = var.server_ids

  type             = "server"
  load_balancer_id = hcloud_load_balancer.this.id
  server_id        = each.value
  use_private_ip   = true

  # Private IP routing requires the LB to be on the network first
  depends_on = [hcloud_load_balancer_network.this]
}

# HTTP — Traefik listens on port 80 via hostPort on all k3s nodes
resource "hcloud_load_balancer_service" "http" {
  load_balancer_id = hcloud_load_balancer.this.id
  protocol         = "tcp"
  listen_port      = 80
  destination_port = 80

  health_check {
    protocol = "tcp"
    port     = 80
    interval = 15
    timeout  = 10
    retries  = 3
  }
}

# HTTPS — Traefik handles TLS termination; LB is TCP passthrough
resource "hcloud_load_balancer_service" "https" {
  load_balancer_id = hcloud_load_balancer.this.id
  protocol         = "tcp"
  listen_port      = 443
  destination_port = 443

  health_check {
    protocol = "tcp"
    port     = 443
    interval = 15
    timeout  = 10
    retries  = 3
  }
}
