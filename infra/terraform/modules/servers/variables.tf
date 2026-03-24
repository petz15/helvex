variable "name_prefix" {
  type = string
}

variable "location" {
  type = string
}

variable "image" {
  type = string
}

variable "ssh_keys" {
  type = list(string)
}

variable "network_id" {
  type = number
}

variable "subnet_id" {
  type        = string
  description = "Subnet ID — passed to create an implicit dependency so servers are not attached before the subnet exists."
}

variable "firewall_id" {
  type = number
}

variable "servers" {
  type = map(object({
    server_type  = string
    role         = string         # "k3s-control-plane" | "k3s-worker"
    private_ip   = string         # static IP within the subnet (e.g. "10.0.1.10")
    node_labels  = optional(list(string), [])
    node_taints  = optional(list(string), [])
  }))
  default = {
    app1 = {
      server_type = "cx32"
      role        = "k3s-control-plane"
      private_ip  = "10.0.1.10"
    }
    app2 = {
      server_type = "cx32"
      role        = "k3s-worker"
      private_ip  = "10.0.1.11"
    }
    db1 = {
      server_type = "cx22"
      role        = "k3s-worker"
      private_ip  = "10.0.1.12"
      node_labels = ["helvex.io/role=database"]
      node_taints = ["helvex.io/role=database:NoSchedule"]
    }
  }
}

variable "k3s_token" {
  type      = string
  sensitive = true
  description = "Shared secret used by K3s workers to join the cluster."
}

variable "db_volume_size_gb" {
  type        = number
  default     = 80
  description = "Size (GiB) of the persistent data volume attached to database-role nodes. Set to 0 to skip."
}
