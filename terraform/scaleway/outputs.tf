output "cluster_name" {
  value = scaleway_k8s_cluster.kapsule.name
}

output "get_kubeconfig_command" {
  description = "Run this to populate kubeconfig for kubectl/helm"
  value       = "scw k8s kubeconfig install ${scaleway_k8s_cluster.kapsule.id}"
}
