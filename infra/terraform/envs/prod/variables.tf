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
  default = "10.42.0.0/16"
}

variable "subnet_cidr" {
  type    = string
  default = "10.42.1.0/24"
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
  }))
  default = {
    app1 = {
      server_type = "cx32"
      role        = "k3s-control-plane"
    }
    app2 = {
      server_type = "cx32"
      role        = "k3s-worker"
    }
    db1 = {
      server_type = "cx22"
      role        = "database"
    }
  }
}
