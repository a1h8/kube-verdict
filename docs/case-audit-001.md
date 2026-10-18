# Case audit 001 — the 18 native cases, one by one

> 2026-10-17. Each native case (`tests/integration/cases/h0NN_*`) checked against a grid
> set before reading the cases. Facts were collected by script (expect.json, the graph
> the loader builds, the 2026-10-14 mistral baseline, the rules replay, `demo/manifests/`)
> and by reading the fixtures. Protocol 001 runs on the cases as corrected here, not before.

## Grid

1. **Realistic** — would a real cluster produce these objects, events and messages?
2. **Live evidence** — is the evidence the case relies on produced by the live collectors,
   or only by the test loader?
3. **Unintended gaps** — a drift, a decoy or a second cause nobody meant.
4. **Grading** — do the keywords require the *cause*, or can the symptom alone pass?
5. **Live reproducible** — does a `demo/manifests/` file reproduce *this* case on `k3d-dev`?

Verdict: **keep**, **correct** (what, below) or **retire**.

## Cross-cutting findings

- **F1 — Grading lets the symptom pass.** A group passes with half of its keywords
  (`tools/veracity.py` `_group_passes`). When the list mixes cause words and symptom
  words, an answer that only restates the symptom passes. h015: `deadline`, `timeout`,
  `readiness` are in the readiness-probe event, so "the readiness probe times out" passes
  without naming etcd. Same shape for h013 (`latency`, `slo`, `burn`, `p99`).
  Correction: each case gets a **cause group** that must match (`root_cause_contains_all`),
  symptom words go in a separate optional group or are dropped.
- **F2 — The live manifests do not reproduce the cases.** Only `09` and `10` claim a case
  (h014, h015). `01` (DB connection exits 1) is not h001 (missing Secret); `02` (limit too
  low, no Helm release) is not h003 (declared 512Mi vs deployed 128Mi); `03`/`04` differ
  from h004/h002 in names and cause; `05` (node selector) matches no case.
- **F3 — The live h014 / h015 ground truth is staged.** In `09` and `10` the pod is
  NotReady because its readiness probe targets a port nothing listens on; the `x509` log
  lines and the etcd error spans are printed by the container or a sidecar, they cause
  nothing. An answer naming the closed probe port is *true* and is graded FAIL. The live
  0/2 (2026-09-26) measured agreement with a script, not diagnosis.
- **F4 — `answerable` is uninformative**, as already recorded in `docs/veracity-replay.md`:
  computed on the whole prompt, it is true for every case.
- **F5 — h012 does not exercise its own claim in the replay.** The harness runs with
  `GITOPS_ENABLED=False`, so the render-vs-live diff never enters the graph (no `drift.*`);
  the case is tested as such only by `test_render_vs_live_h012.py`.

- **F6 — Quotas and LimitRanges exist only in the test graph.** `ResourceQuota` and
  `LimitRange` are ontology entities with `QUOTA_BLOCKS` / `LIMITS` relations, but only the
  test loader builds `QUOTA_BLOCKS` (`case_loader.py:190`); the live collector neither
  types quotas nor wires the edge, and `LIMITS` is wired nowhere. h010 passes offline on
  evidence a live run does not build (same class as the `missing.*` annotations before
  the live-parity fix).
- **F7 — A proposed fix is never checked against quotas.** Raising h003's memory limit to
  512Mi can exceed the namespace quota (new requests × replicas + the rollout surge) or a
  LimitRange maximum. `--dry-run=server` validates the Deployment, not the pods its
  ReplicaSet creates afterwards, so the rollout would stall: one more incident.
  Raised 2026-10-17 by the maintainer.

- **F8 — The model never received the whole prompt.** `OLLAMA_NUM_CTX` defaults to 2048
  (`config.py`), and neither the replay nor production sets it. The Ollama logs show prompts
  of ~6 200 tokens cut to ~1 027 (`msg="truncating input prompt" limit=1027 prompt=6212`,
  2026-10-17). Ollama keeps the first 5 tokens and the end: the tail of the evidence block,
  the injected hypothesis and the answer instructions. The evidence sections and the context
  quality score are lost. The 2026-10-14 baseline and its re-gradings were measured under this cut
  (2026-10-17 logs show it on prompts of the same size); the 2026-09-26 live captures ran
  with the same default, their logs are gone; `answerable` was computed on the prompt
  sent, not the prompt read. Found 2026-10-17.

- **F9 — The GitOps resource diff is blind on a live cluster** (found 2026-10-18, raised by an
  outside review and checked in the code). `ingestion/manifest_differ.py` `_diff_resources`
  compares the rendered limits/requests with an annotation `gitops.resources.<container>.<field>`
  that no production code writes; limits and requests also share one key (`memory`), so one
  overwrites the other; `_diff_replicas` flags any `spec.replicas` difference, including one an HPA
  manages. The Helm path (`ingestion/helm_drift.py`, values vs deployed release) is not affected.
  *Status:* resources fixed 2026-10-18 (§ Correction F9); the HPA case stays open.
- **F10 — The h012 proof and `make demo` supply the observed side themselves.**
  `test_render_vs_live_h012.py` writes the observed memory limit into the graph; `demo/real_run.py`
  computes its own diff (not `ManifestDiffer`) and prints fixed verdict lines. The README said
  "the same code that runs in CI. No mocks"; corrected 2026-10-18.
  *Status:* the test no longer supplies the observed side (F9); the demo still computes its own
  diff and prints fixed verdict lines.
- **F11 — On a live cluster the Helm detector sees neither memory, image nor probes** (found
  2026-10-18). `ingestion/helm_drift.py` compares the values Helm deployed with the live objects
  and emits only `spec.replicas`, status fields, PVC storage, container state, restart count and
  subchart conditions. The `drift.helm.<field>` items that h002, h003, h009 and h024 rely on
  (values.yaml vs deployed values) are made by the test loader alone
  (`case_loader.py` `_annotate_value_drift`). So four of the fourteen cases the structured
  diagnosis passes rest on evidence a live run does not build. On a live cluster only the
  GitOps render-vs-live diff can see those gaps, and it is not in the measured pipeline.

## Per case

| Case | 1 Realistic | 2 Live evidence | 3 Unintended | 4 Grading | 5 Live | Verdict |
|---|---|---|---|---|---|---|
| h001 missing Secret → CrashLoop | yes (`secret "…" not found` is a real kubelet event) | yes | — | ok | no (F2) | **keep**; live manifest to write |
| h002 private image 401 + tag drift | yes | yes | **two causes** under one keyword list (auth 401 and tag drift) | ok | no (F2) | **correct**: one cause, or `root_cause_contains_all` with two groups |
| h003 OOM, declared 512Mi vs deployed 128Mi | yes (an out-of-band edit) | yes | — | ok (`drift` is the cause here) | no (F2) | **keep**; live manifest with a Helm release |
| h004 missing ConfigMap / Secret | yes | yes | — | ok after synonyms | no (F2) | **keep** |
| h005 SA without ClusterRoleBinding | **no**: Kubernetes emits no Event when an app is denied by RBAC; the `forbidden` text lives in the pod's logs | **no** (`missing.rbac.*` offline only) | — | ok | no | **correct**: move the `forbidden` lines to `loki/`, collect RoleBindings live |
| h006 NetworkPolicy blocks egress | **no**: an event message says "UDP/53 egress blocked" — the cause written where Kubernetes never writes it | **no** (`netpol.*` offline only) | the message leaks the answer | ok | no | **correct**: realistic messages, NetworkPolicies collected live |
| h007 HPA without metrics | yes | yes | — | ok | no | **keep** |
| h008 init container migration exits 1 | yes | yes | — | ok | no | **keep** |
| h009 liveness timeout drift | yes | yes | — | ok | no | **keep** |
| h010 ResourceQuota exceeded | **inconsistent**: the pod is Pending on "3 Insufficient cpu" (node capacity) while the quota rejects another pod | yes | **second cause** (node CPU) | ok | no | **correct**: one cause — either the quota (no Pending pod, `FailedCreate` on the ReplicaSet) or node capacity |
| h011 StatefulSet PVC stuck | known broken (wired, never passing) | — | — | — | no | **retire**, replaced by legacy `019_statefulset_update_stuck` (`docs/scenarios-v2.md` triage) |
| h012 render-vs-live OOM | yes | render path off in replay (F5) | — | ok | no | **correct**: enable the render path for h012 in the replay, or keep it out of the replay corpus |
| h013 SLO burn, k8s healthy | yes | yes (Prometheus) | — | **F1**: symptom words pass | no | **correct** grading: the dependency must be named |
| h014 expired certificate | yes offline | yes (Loki) | — | ok | **staged** (F3) | **keep** offline; **redo** live: a real TLS upstream with an expired certificate and a probe that does the handshake |
| h015 etcd compaction | yes offline | yes (OTel) | — | **F1**: passes without "etcd" | **staged** (F3) | **correct** grading; **redo** live |
| h016 Postgres connection exhaustion | yes | yes | — | ok | no | **keep** |
| h022 no incident | yes | yes | — | ok (negative) | `06` is close (healthy app) | **keep** — negative control |
| h024 two independent faults | yes | yes | — | ok (two groups) | no | **keep** |

**Totals:** keep 9 (h001, h003, h004, h007, h008, h009, h016, h022, h024) · keep
offline + redo live 1 (h014) · correct 7 (h002, h005, h006, h010, h012, h013, h015) ·
retire 1 (h011).

## What this changes

- **F8 invalidates every LLM number recorded so far**: they are kept as records, not as
  measurements of the diagnosis.

- The 2026-10-14 baseline (30/54) was measured on cases of which 8 are to be corrected or
  retired, and with a grading that lets symptoms pass on at least two. It stays as
  recorded; it is not compared with later numbers as if the cases were the same.
- Live 0/2 is not evidence of a wrong diagnosis (F3).
- Protocol 001 runs after the corrections, on the corrected list, frozen at that commit.

## Order of corrections

1. Grading (F1): cause groups for every case — one change, re-grades recorded runs
   without re-running a model.
2. h010, h002: one cause each. h005, h006: realistic messages, evidence where Kubernetes
   puts it.
3. h011 retired, h012 decided (render path on, or out).
4. Live manifests matching the kept cases (F2), h014 / h015 redone (F3).

## Correction 1 — grading by cause concepts (rule fixed 2026-10-17, before any re-grade)

**Rule.** A case lists the **concepts of its cause**; an answer passes only if it names
**every** concept (no half-of-the-keywords rule). A concept is one string of accepted
wordings, `a|b|c`, extended by `tests/veracity/synonyms.yaml`. Keys in `expect.json`:
`root_cause_concepts` (one cause) or `root_cause_concepts_all` (one concept list per
cause). When present they replace `root_cause_contains` / `root_cause_contains_all` in
`grade()`; the old keys stay for the tests that read them.

**A concept may not be satisfied by the symptom.** A wording that appears in what the
case shows as its symptom (pod state, alert, probe message) is not accepted for a cause
concept (e.g. `128Mi` in h003/h012, `timeout` in h009, `deadline` in h015).

**Controls, per case** (`tests/unit/test_grading_controls.py`):
- *positive* — a correct answer, worded differently from the keywords, must PASS;
- *negative* — a symptom-only answer must FAIL: the workload state as `kubectl get pods`
  and the alert show it (CrashLoopBackOff, OOMKilled, NotReady, restarts, latency),
  without the messages that name the faulty object. For tier A, where an event states
  the cause, reading that event is part of the diagnosis, so it is not a symptom-only
  answer.

| Case | Concepts (all required) |
|---|---|
| h001 | `secret` · `not found\|missing` |
| h002 | `image` · `tag\|drift\|declared` (cause chosen: the tag drift; fixture fixed in correction 2) |
| h003 | `memory` · `limit` · `drift\|512mi\|declared` |
| h004 | `configmap\|api-gateway-config` · `missing\|not found` |
| h005 | `serviceaccount\|service account\|audit-exporter-sa` · `rbac\|binding\|permission` |
| h006 | `networkpolicy\|network policy` |
| h007 | `metrics` · `hpa\|autoscal` |
| h008 | `migration` · `init` |
| h009 | `liveness` · `drift\|timeoutseconds\|declared\|too short\|too aggressive` |
| h010 | `quota` (cause chosen: the namespace quota; fixture fixed in correction 2) |
| h012 | `memory` · `drift\|512mi\|declared\|render\|expected state` |
| h013 | `payments-gateway\|payments gateway\|dependency\|downstream` · `network` |
| h014 | `certificate\|x509\|tls` · `expired\|expir\|renew` |
| h015 | `etcd` · `compaction\|compacted\|latency\|slow` |
| h016 | `max_connections\|connection pool\|pool` · `replica\|scale` |
| h024 | cause 1: `secret\|queue-credentials` · `missing\|not found` — cause 2: `memory\|oom` · `limit` · `drift\|1gi\|declared` |
| h011, h022 | unchanged (h011 retired in correction 3; h022 has no cause) |

### Amendments to correction 1 (2026-10-17, after reading the re-graded answers)

Found by reading every answer whose grade changed; each fixes a concept that rejected a
correct answer, and is applied the same way to every case it concerns. Not a threshold
change: the rule (every concept required) is unchanged.

- **A1 — h008** `init` → `init|db-migrate`. The three answers name "the db-migrate
  container" and the failed migration; db-migrate *is* the init container.
- **A2 — h013** `dependency` → `dependenc`. "a network issue between checkout-api and its
  dependencies" did not contain the singular.
- **A3 — h003, h024** concept `limit` removed. "drift between the declared and deployed
  memory resources" names the cause; "limit" vs "resources" is wording, not cause.
  The cause concepts become `memory` · `drift|512mi|declared` (h003) and
  `memory|oom` · `drift|1gi|declared` (h024, second cause).

Not amended: h006 (the answers name a network/DNS problem, never the NetworkPolicy — the
symptom) and h010 (the answers follow the fixture's node-CPU message; the fixture is fixed
in correction 2).

## Correction F8 — the context window fits the prompt (2026-10-17)

- `OllamaClient.context_for()` sizes `num_ctx` per call: estimated prompt tokens
  (characters ÷ 2.7) + `num_predict` + 256, rounded up to a multiple of 1024, never below
  `OLLAMA_NUM_CTX`. The ratio comes from two real case prompts read by mistral on
  2026-10-17: 3.09 (h001, 7 233 characters → 2 343 tokens) and 3.07 (h016, 14 011 → 4 569);
  2.7 over-estimates by about 12 %.
- Above `OLLAMA_NUM_CTX_MAX` (default 8192, bounded by the Ollama pod's memory) the prompt is
  refused (`PromptTooLongError`), never sent to be truncated.
- Every call records `num_ctx`, the estimate, `prompt_eval_count` (tokens read) and timings
  (`last_call`); the replay stores them per run (`llm_calls`).
- Tests (`tests/unit/test_ollama_context.py`): the window covers both measured prompts with the
  whole answer; the old 2048 did not fit even h001 (2 343 + 512); oversize is refused; every
  native case's analyze prompt fits under the default ceiling.
- Cost, measured: ~22 prompt tokens/s on the CPU-only pod — 103 s to read h001, 212 s for
  h016, before the answer.

## Correction F9 — the GitOps resource diff reads the live containers (2026-10-18, spec)

- `Deployment.containers` (typed: `name`, `image`, `resources.limits`, `resources.requests`) is
  filled from the live object by the collector (`sanitize_for_serialization`, camelCase) and from
  the fixture's spec by the loader — the same shape both ways; `raw` is not used (snake_case live,
  camelCase in fixtures).
- `_diff_resources` compares limits with limits and requests with requests, on distinct paths
  (`container.<c>.resources.limits.memory`); no annotation is read for the observed side.
- Control: `test_render_vs_live_h012.py` no longer writes the observed value; the differ must find
  512Mi → 128Mi from the Deployment alone.
- Not in this correction: `spec.replicas` managed by an HPA still reads as a drift. Knowing that an
  HPA targets a Deployment needs HPAs collected live (D1.2); left open.
