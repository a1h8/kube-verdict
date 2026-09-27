# Terraform — cluster provisioning only

Scope: **the K8s cluster itself**, nothing on top. kube-verdict, Prometheus,
Loki, Tempo, Grafana, Alloy stay the existing Helm charts / `values.yaml`
overrides already proven against `rancher-desktop` (`demo/observability/*.yaml`,
`helm/kube-verdict/`) — see [../docs/cloud-prerequisites.md](../docs/cloud-prerequisites.md),
whose checklist this implements for step 2 ("Cluster choice").

Not applied anywhere yet — no GCP project or Scaleway account/billing exists
for this to target (per `docs/cloud-prerequisites.md` #1). Written and
reviewable now so applying it is the only remaining step once an account
exists, not a design exercise done under time pressure.

**Not validated with `terraform validate`** — the `terraform` CLI isn't
installed in this dev environment. Reviewed by eye for correct HCL syntax and
provider resource shapes; run `terraform validate` yourself before the first
`apply`.

## GCP (`gcp/`) — GKE Autopilot

```sh
cd terraform/gcp
terraform init
terraform plan  -var="project_id=<your-gcp-project>"
terraform apply -var="project_id=<your-gcp-project>"
# then:
$(terraform output -raw get_credentials_command)
```

## Scaleway (`scaleway/`) — Kapsule

```sh
cd terraform/scaleway
terraform init
terraform plan  -var="organization_id=<org-id>" -var="project_id=<project-id>"
terraform apply -var="organization_id=<org-id>" -var="project_id=<project-id>"
# then:
$(terraform output -raw get_kubeconfig_command)
```

## After either cluster exists

Continue with `docs/cloud-prerequisites.md` #3 onward (observability
backends via Helm, network/secrets, then the validation gate) — same steps,
same values files, on either cloud.

## Teardown

```sh
terraform destroy   # in the same directory, same -var flags as apply
```

Matches `docs/cloud-prerequisites.md` #7 — don't leave demo infra running
between sessions.
