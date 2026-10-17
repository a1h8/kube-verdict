# Re-graded runs

Records: `tests/veracity/replay/rules`. Graded again with the current `expect.json` files and `tests/veracity/synonyms.yaml`; the records themselves are unchanged.

| Tier / case | Pass before | Pass now |
|---|---|---|
| **Tier A** | 3/12 | 2/12 |
| h001_crashloopbackoff | 1/1 | 1/1 |
| h002_imagepullbackoff | 0/1 | 0/1 |
| h003_oomkilled | 0/1 | 0/1 |
| h004_missing_configmap | 1/1 | 1/1 |
| h005_rbac_forbidden | 0/1 | 0/1 |
| h006_networkpolicy_blocked | 0/1 | 0/1 |
| h007_hpa_no_metrics | 0/1 | 0/1 |
| h008_init_container_fail | 0/1 | 0/1 |
| h009_liveness_probe_loop | 0/1 | 0/1 |
| h010_resource_quota_exceeded | 1/1 | 0/1 |
| h011_statefulset_pvc_stuck | 0/1 | 0/1 |
| h012_gitops_render_vs_live | 0/1 | 0/1 |
| **Tier B** | 0/4 | 0/4 |
| h013_slo_error_budget_burn | 0/1 | 0/1 |
| h014_cert_expiry | 0/1 | 0/1 |
| h015_etcd_compaction | 0/1 | 0/1 |
| h016_multi_signal | 0/1 | 0/1 |
| **Tier C** | 1/2 | 1/2 |
| h022_no_incident | 1/1 | 1/1 |
| h024_multi_cause | 0/1 | 0/1 |

**All: 4/18 → 3/18**
