module "network" {
  source = "../../modules/network"

  name            = "${var.project_name}-net"
  ip_range        = var.network_cidr
  network_zone    = var.network_zone
  subnet_ip_range = var.subnet_cidr
}

module "firewall" {
  source = "../../modules/firewall"

  name        = "${var.project_name}-fw"
  admin_cidrs = var.admin_cidrs
}

module "servers" {
  source = "../../modules/servers"

  name_prefix        = var.project_name
  location           = var.location
  image              = var.image
  ssh_keys           = var.ssh_keys
  network_id         = module.network.network_id
  subnet_id          = module.network.subnet_id
  firewall_id        = module.firewall.firewall_id
  servers            = var.servers
  db_volume_size_gb  = 80
  k3s_token          = var.k3s_token
}

# Load balancer — targets only k3s nodes (not the DB node)
locals {
  lb_target_server_ids = {
    for k, v in var.servers : k => module.servers.server_ids[k]
    if v.role != "database"
  }
}

module "loadbalancer" {
  source = "../../modules/loadbalancer"

  name       = "${var.project_name}-lb"
  lb_type    = var.lb_type
  location   = var.location
  network_id = module.network.network_id
  server_ids = local.lb_target_server_ids
}
