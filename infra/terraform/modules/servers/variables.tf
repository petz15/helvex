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

variable "firewall_id" {
  type = number
}

variable "servers" {
  type = map(object({
    server_type = string
    role        = string
  }))
}
