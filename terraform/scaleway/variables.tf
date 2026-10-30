variable "organization_id" {
  description = "Scaleway organization ID (account already created — see docs/cloud-prerequisites.md #1)"
  type        = string
}

variable "project_id" {
  description = "Scaleway project ID"
  type        = string
}

variable "region" {
  type    = string
  default = "fr-par"
}

variable "cluster_name" {
  type    = string
  default = "kubeverdict-demo"
}

variable "node_type" {
  description = "Smallest instance type sized for the demo scale (docs/cloud-prerequisites.md #2: one small node pool is enough)"
  type        = string
  default     = "DEV1-M"
}

variable "kubernetes_version" {
  description = "Kapsule Kubernetes version, pinned (list them with `scw k8s version list`); no default, so a run never picks one silently"
  type        = string
}
