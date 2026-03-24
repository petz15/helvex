variable "name" {
  type = string
}

variable "lb_type" {
  type    = string
  default = "lb11"
}

variable "location" {
  type = string
}

variable "network_id" {
  type = number
}

variable "server_ids" {
  type        = map(number)
  description = "Map of server name → server ID for LB targets (k3s nodes only, not DB)."
}
