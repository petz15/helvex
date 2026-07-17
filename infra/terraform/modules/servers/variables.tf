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
      server_type = "cx33"
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
  description = "Shared secret used by K3s workers to join the cluster."
}

variable "k3s_version" {
  type        = string
  description = "Pinned K3s release (e.g. v1.35.5+k3s1). Never install 'latest' on a server — an upstream compromise or breaking release would roll out untested. Check current stable: https://update.k3s.io/v1-release/channels/stable"
}

variable "admin_cidrs" {
  type        = list(string)
  description = "CIDRs allowed to SSH in. Also used to scope sudo on the control-plane node via pam_access, as defense-in-depth behind the firewall rule."
}

variable "cluster_private_cidr" {
  type        = string
  description = "Private subnet CIDR (e.g. 10.0.1.0/24). Configured as Traefik's trusted PROXY protocol / forwarded-headers source, since the Hetzner LB connects to nodes over this network."
}

variable "db_volume_size_gb" {
  type        = number
  default     = 80
  description = "Size (GiB) of the persistent data volume attached to database-role nodes. Set to 0 to skip."
}
