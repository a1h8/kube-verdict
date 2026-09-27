# Kapsule (managed K8s) — the sovereign/self-hosted counterpart to GKE
# Autopilot, per docs/cloud-prerequisites.md #2. Scope stops at the cluster +
# one small node pool; the app/observability layer is the existing Helm
# charts, unchanged across clouds.

provider "scaleway" {
  organization_id = var.organization_id
  project_id      = var.project_id
  region           = var.region
}

resource "scaleway_k8s_cluster" "kapsule" {
  name    = var.cluster_name
  type    = "kapsule"
  version = "latest"
  cni     = "cilium"

  delete_additional_resources = true
}

resource "scaleway_k8s_pool" "default" {
  cluster_id = scaleway_k8s_cluster.kapsule.id
  name       = "default"
  node_type  = var.node_type
  size       = 2
  autoscale  = false
  autohealing = true
}
