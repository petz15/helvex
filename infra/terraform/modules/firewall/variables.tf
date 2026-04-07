variable "name" {
  type = string
}

variable "admin_cidrs" {
  type = list(string)
}

variable "cluster_private_cidr" {
  type        = string
  description = "Private subnet CIDR used by K3s nodes (e.g. 10.0.1.0/24)."
}
