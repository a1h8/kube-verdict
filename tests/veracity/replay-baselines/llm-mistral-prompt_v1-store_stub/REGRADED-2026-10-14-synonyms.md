# Re-graded runs

Records: `tests/veracity/replay-baselines/llm-mistral-prompt_v1-store_stub`. Graded again with the current `expect.json` files and `tests/veracity/synonyms.yaml`; the records themselves are unchanged.

| Tier / case | Pass before | Pass now |
|---|---|---|
| **Tier A** | 20/36 | 27/36 |
| h001_crashloopbackoff | 3/3 | 3/3 |
| h002_imagepullbackoff | 3/3 | 3/3 |
| h003_oomkilled | 2/3 | 2/3 |
| h004_missing_configmap | 0/3 | 3/3 |
| h005_rbac_forbidden | 0/3 | 3/3 |
| h006_networkpolicy_blocked | 1/3 | 1/3 |
| h007_hpa_no_metrics | 3/3 | 3/3 |
| h008_init_container_fail | 3/3 | 3/3 |
| h009_liveness_probe_loop | 3/3 | 3/3 |
| h010_resource_quota_exceeded | 2/3 | 2/3 |
| h011_statefulset_pvc_stuck | 0/3 | 1/3 |
| h012_gitops_render_vs_live | 0/3 | 0/3 |
| **Tier B** | 3/12 | 3/12 |
| h013_slo_error_budget_burn | 3/3 | 3/3 |
| h014_cert_expiry | 0/3 | 0/3 |
| h015_etcd_compaction | 0/3 | 0/3 |
| h016_multi_signal | 0/3 | 0/3 |
| **Tier C** | 0/6 | 0/6 |
| h022_no_incident | 0/3 | 0/3 |
| h024_multi_cause | 0/3 | 0/3 |

**All: 23/54 → 30/54**
