variable "project_id" {
  description = "GCP project ID (billing already enabled — see docs/cloud-prerequisites.md #1)"
  type        = string
}

variable "region" {
  description = "GCP region for the Autopilot cluster"
  type        = string
  default     = "europe-west1"
}

variable "cluster_name" {
  type    = string
  default = "kubeverdict-demo"
}
