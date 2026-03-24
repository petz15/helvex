variable "hcloud_token" {
  type      = string
  sensitive = true
}

variable "project_name" {
  type    = string
  default = "helvex"
}

variable "location" {
  type    = string
  default = "fsn1"
}

variable "image" {
  type    = string
  default = "ubuntu-24.04"
}

variable "network_cidr" {
  type    = string
  # 10.42.0.0/16 is K3s's default pod CIDR — using it for the node network causes
  # routing conflicts. Use a separate range for the Hetzner private network.
  default = "10.0.0.0/16"
}

variable "subnet_cidr" {
  type    = string
  default = "10.0.1.0/24"
}

variable "lb_type" {
  type    = string
  default = "lb11"
}

variable "network_zone" {
  type    = string
  default = "eu-central"
}

variable "admin_cidrs" {
  type = list(string)
}

variable "ssh_keys" {
  type = list(string)
}

variable "servers" {
  type = map(object({
    server_type = string
    role        = string
    private_ip  = string
    node_labels = optional(list(string), [])
    node_taints = optional(list(string), [])
  }))
  default = {
    app1 = {
      server_type = "cx23"
      role        = "k3s-control-plane"
      private_ip  = "10.0.1.10"
    }
    db1 = {
      server_type = "cx33"
      role        = "k3s-worker"
      private_ip  = "10.0.1.11"
      node_labels = ["helvex.io/role=database"]
      node_taints = ["helvex.io/role=database:NoSchedule"]
    }
  }
}

variable "k3s_token" {
  type      = string
  sensitive = true
  description = "Shared secret for K3s cluster — generate with: openssl rand -hex 32"
}
