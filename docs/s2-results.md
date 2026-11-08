# S2 — results of the first run (exploratory)

> Run 2026-11-08, commit `a1da285`, on the 21 cases frozen in `tests/s2/frozen.json`. Graded blind as
> `docs/s2/grading.md` fixed before the run. S2 is exploratory (protocol 002, amendment A3): 21 cases, no
> test, intervals only. Spec of the cases: `docs/s2-correlation-cases.md`.

## What was run

| Arm | What answers | Runs | Wall time |
|---|---|---|---|
| D | the structured diagnosis (graph built by the collectors, typed signals, rules; no model) | 1 per case | 30 s |
| L₀ | `context:mistral`, the same evidence as context | 3 per case | 5 h 07 |
| S₀ | `context:qwen2.5:3b-instruct`, the same evidence as context | 3 per case | 1 h 45 |

147 answers in all. Every record names set S2, commit `a1da285`, a graph built by the collectors and the stub
store. None was invalid or empty, and the Ollama pod did not restart during the run. 14 cases have a cause two
or more hops from the symptom, with decoys. 7 cases have no incident.

## Grading

- **Graders.** Grader A was a Claude model and grader B a ChatGPT model, each in a fresh session given only the
  pack. A third reading by a fresh Claude session settled the disputed runs.
- **Agreement.** The two graders agreed on 142 of 147 runs: raw agreement 0.97, Cohen's κ 0.93. All 5
  disagreements were NO against PARTIAL, so none could change a pass. The third reading made 4 of them NO and 1
  PARTIAL. No run stays disputed.
- **Files.** The pack, its key and the three tables are in `tests/s2/grading/`. The score comes from
  `tools/s2_grading_score.py`.

## Results

A case passes an arm when 2 of its 3 runs are YES (D: its one run). Intervals are Wilson 95 %.

| Arm | Causes found (of 14) | No incident recognised (of 7) | PARTIAL cases (of 14) |
|---|---|---|---|
| D | **0** (0–22 %) | **6** (49–97 %) | 2 |
| L₀ | **2** (4–40 %) | **0** (0–35 %) | 8 |
| S₀ | **1** (1–31 %) | **0** (0–35 %) | 6 |

**Keyword grader** (`tools/veracity.py`, secondary, never pooled with the blind grading): D finds 0 of 14 causes
and recognises 6 of 7 cases with no incident; L₀ finds 3 and 0; S₀ finds 2 and 0. It is a little more lenient
than the blind graders with the models' causes.

### Per case

D's single run, then each model's three runs ordered best first: Y = YES, P = PARTIAL, N = NO.

| Case | D | L₀ | S₀ |
|---|---|---|---|
| `a0_hpa_no_incident` | YES | NNN | NNN |
| `a1_hpa_custom_metrics_api_points_away` | NO | PPP | PNN |
| `a2_hpa_scale_up_policy_slowed` | NO | PNN | NNN |
| `c0_configmap_no_incident` | YES | NNN | NNN |
| `c1_configmap_env_changed` | NO | PPP | PPN |
| `c2_configmap_invalid_file` | NO | YYY | PPP |
| `d0_dependency_no_incident` | YES | NNN | NNN |
| `d1_dependency_service_wrong_port` | NO | PPP | PPP |
| `d2_dependency_redis_full` | NO | PPP | NNN |
| `l0_limitrange_no_incident` | YES | NNN | NNN |
| `l1_limitrange_default_injected` | PARTIAL | PNN | NNN |
| `l2_limitrange_max_lowered` | NO | PPN | PPN |
| `n0_netpol_no_incident` | YES | NNN | NNN |
| `n1_netpol_namespace_relabel` | NO | NNN | NNN |
| `n2_netpol_pod_labels_changed` | NO | NNN | NNN |
| `q0_quota_no_incident` | NO | NNN | NNN |
| `q1_quota_filled_by_request_drift` | NO | NNN | NNN |
| `q2_quota_filled_by_stuck_jobs` | NO | NNN | NNN |
| `w0_webhook_no_incident` | YES | NNN | NNN |
| `w1_webhook_injects_missing_image` | PARTIAL | PPP | PPP |
| `w2_webhook_fails_closed_expired_cert` | NO | YYY | YYY |

## What D answered on the 14 cases with a cause

| D's answer | Cases | What it shows |
|---|---|---|
| "Insufficient evidence: fault signals exist but no rule explains them." | a2, c1, c2, d1, n1, n2 | It sees the symptom, links nothing, and says so. |
| "No incident: no fault signal in the evidence." | a1, l2, w2 | The fault is never typed as a signal: an admission refusal, an unavailable aggregated metrics API, a webhook that fails closed. |
| A decoy named as the cause | q1, q2, d2 | An OOMKill resolved 5 h before the symptom (q1, q2), a replica drift with no effect (d2). The time order is not used. |
| The first hop only | l1, w1 | The OOMKill without the LimitRange that set the limit; the failed image pull without the webhook that injected the image. |

On the cases with no incident, D fails only q0, where it names the same old OOMKill.

## What this shows

- **D does not correlate across hops yet.** It never reaches a cause two hops away, but it rarely invents one:
  6 of 7 cases with no incident are recognised, and 6 of its 14 failures say plainly that no rule explains the
  signals.
- **The models never say there is no incident.** L₀ and S₀ name a fault on all 7 cases with no incident. On
  the cases with a cause, they often stop at the first hop (PARTIAL). Each finds the expired webhook
  certificate; L₀ also finds the invalid ConfigMap file.
- **No arm finds a quota or a NetworkPolicy chain.** These are the longest chains, and the ones where the graph
  has no edge today (`docs/s2/family-quota.md`, `docs/s2/family-netpol.md`).

## Limits

- 21 cases and one run of D: the intervals are wide. No difference between arms is claimed as significant.
- The graders were given the ground truth we wrote, so a mistake in a case's truth is a mistake in its grading.
- A Claude model helped write the cases, and graders A and C were Claude models. Grader B, from another
  provider, agreed with grader A on 142 of 147 runs.
- A model grader may recognise D by its terse style.
- The cases were written knowing the ontology's gaps (`docs/s2-correlation-cases.md`), so D's 0 of 14 measures
  those gaps and does not estimate a rate on clusters in general.

## Next

These results are the baseline of the correlation work. It is specified next, then measured on these same 21
frozen cases and on new cases written after it.
