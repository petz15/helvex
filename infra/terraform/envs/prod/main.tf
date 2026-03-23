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

  name_prefix = var.project_name
  location    = var.location
  image       = var.image
  ssh_keys    = var.ssh_keys
  network_id  = module.network.network_id
  firewall_id = module.firewall.firewall_id
  servers     = var.servers
}
