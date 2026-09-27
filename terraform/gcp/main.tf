# GKE Autopilot — docs/cloud-prerequisites.md #2: "fastest to stand up, managed
# node pools; avoid standard GKE unless node-level control is actually needed."
#
# Scope stops at the cluster. Everything on top (kube-verdict, Prometheus,
# Loki, Tempo, Grafana, Alloy) is the existing Helm charts / values.yaml
# overrides already used against rancher-desktop — no custom infra code for
# that layer, per the same doc's opening principle.

provider "google" {
  project = var.project_id
  region  = var.region
}

resource "google_project_service" "container" {
  project = var.project_id
  service = "container.googleapis.com"

  disable_on_destroy = false
}

resource "google_container_cluster" "autopilot" {
  name     = var.cluster_name
  location = var.region

  enable_autopilot = true

  # Autopilot manages node pools/scaling; deletion_protection off so
  # `terraform destroy` actually tears this down for a demo cluster (docs
  # cloud-prerequisites.md #7 — teardown must not be left running).
  deletion_protection = false

  depends_on = [google_project_service.container]
}
