# R1 — the investigation's vector search alone (commit `3fb889034920833e39dc7610210cd3cc165a1420`)

docs/protocol-001-retrieval.md, amendment 2026-10-27. No model; deterministic.

- M1: **6/17** pass at top 3; expected by chance **6.03**; difference **-0.03** (reported)
- M2: **0** case(s) where C2's prompt passes and C1's does not (none)
- Rule: **leaves**

| Case | Vectors | M1 top 3 | Chance | C1 | C2 | M2 |
|---|---|---|---|---|---|---|
| h001_crashloopbackoff | 6 | PASS | 0.5 | PASS | PASS | — |
| h002_imagepullbackoff | 5 | FAIL | 0.0 | PASS | PASS | — |
| h003_oomkilled | 4 | FAIL | 0.0 | PASS | PASS | — |
| h004_missing_configmap | 6 | PASS | 0.65 | PASS | PASS | — |
| h005_rbac_forbidden | 15 | FAIL | 0.0 | FAIL | FAIL | — |
| h006_networkpolicy_blocked | 19 | FAIL | 0.0 | PASS | PASS | — |
| h007_hpa_no_metrics | 5 | PASS | 0.9 | PASS | PASS | — |
| h008_init_container_fail | 5 | PASS | 0.6 | PASS | PASS | — |
| h009_liveness_probe_loop | 6 | FAIL | 0.0 | PASS | PASS | — |
| h010_resource_quota_exceeded | 4 | PASS | 0.75 | PASS | PASS | — |
| h011_statefulset_pvc_stuck | 5 | PASS | 0.6 | PASS | PASS | — |
| h012_gitops_render_vs_live | 5 | FAIL | 0.0 | PASS | PASS | — |
| h013_slo_error_budget_burn | 8 | FAIL | 0.0 | FAIL | FAIL | — |
| h014_cert_expiry | 18 | FAIL | 0.7304 | PASS | PASS | — |
| h015_etcd_compaction | 10 | FAIL | 0.8333 | FAIL | FAIL | — |
| h016_multi_signal | 30 | FAIL | 0.4655 | PASS | PASS | — |
| h022_no_incident (negative, not counted) | 0 | FAIL | 0.0 | FAIL | FAIL | — |
| h024_multi_cause | 7 | FAIL | 0.0 | PASS | PASS | — |
| h026_rollout_in_progress (negative, not counted) | 0 | FAIL | 0.0 | FAIL | FAIL | — |
