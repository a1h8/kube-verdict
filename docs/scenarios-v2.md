# Scenario catalog v2 — scenarios that measure veracity

> Status: spec (2026-10-11). **Grading v2 implemented** (`tools/veracity.py`, see
> `docs/veracity-benchmark.md` § Grading v2). Cases h017–h025 and the replay harness: not yet. Comes before any live run on
> GCP / Scaleway (`docs/cloud-prerequisites.md` §5).

## Why

- **Live veracity is 0/2.** The only graded live captures (`tests/veracity/real_003`,
  `real_004`, see `docs/veracity-benchmark.md`) are both FAIL: h014 answered "memory issue"
  for an expired certificate, h015 answered "incompatible container image" for etcd
  compaction latency. Neither file records the confidence those wrong answers came with.
- **Offline runs do not measure veracity.** h001–h016 run against a mock LLM. They prove
  that the evidence reaches the prompt. They say nothing about whether a model names the
  right cause.
- **Most cases are too easy.** In h001–h010 the Kubernetes event already says the cause
  (`ImagePullBackOff`, `OOMKilled`, `CreateContainerConfigError`…). A rule finds it
  without any reasoning, so these cases cannot show what a model adds.

## Decisions (2026-10-11)

1. **Widen the scenarios before moving to real infra.** Real infra checks that the
   collectors work on live backends. It does not add scenarios. The corpus is built
   offline first, then validated live once.
2. **Measure veracity by replaying fixtures through a real model.** A real model (local
   Ollama, no cluster) runs on the same `tests/integration/cases/` fixtures. This is not
   a live session (`feedback_no_piecemeal_live_sessions`): no infra is touched, and it
   is reproducible per model.
3. **The corpus does not depend on the solver.** The question "without an LLM, or with a
   small model we train?" is answered by grading every solver on the same corpus
   (§ Solvers). Without that corpus, the phase-2 specialized model
   (`project_gcp_sovereign_cloud_goal`) cannot be evaluated.

## What makes a scenario relevant

A v2 scenario meets at least one of these:

- **The cause is not where the symptom is**: another workload, the node, the control
  plane, or a change made earlier.
- **There is a plausible decoy**: a loud, real anomaly that is not the cause.
- **There is nothing to find**: healthy, or not enough evidence. The right answer is low
  confidence, not an invented cause.
- **Several causes at once.**

Existing cases, by tier:

| Tier | Cases | What it tests |
|---|---|---|
| A — stated cause | h001–h010 | Kubernetes event or drift names the cause. Rules baseline. |
| B — cause in one signal | h012–h016 | The cause is only in one signal (metric, log, trace, render). |
| C — reasoning (v2) | h017–h025 | Cascade, decoy, negative, multi-cause (below). |

h011 stays open (`docs/test-cases.md`, ⚠️ row) and is not part of this spec.

## Grading v2

Each `expect.json` gets two fields in addition to `root_cause_contains`:

- **`root_cause_must_not_contain`**: the decoy's keywords. If any of them appears in the
  root cause, the answer FAILs even when it also matches `root_cause_contains`. For
  example, `["memory", "oom"]` for h014 and `["image", "pull"]` for h015, taken from the
  failed live answers.
- **`expected_outcome`**: `cause` (default), `no_incident` or `insufficient_evidence`.
  For the last two, `root_cause_contains` is empty. The answer PASSes when the LLM label
  is LOW and no `must_not_contain` keyword appears.

Multi-cause cases use `root_cause_contains_all`, a list of keyword lists. Each list is
graded with the existing rule (≥50%, min 1), and all of them must pass.

Each graded run also records `pre_llm_confidence.score`, the LLM confidence label, the
verdict and `ingestion_stats` fallbacks. This shows whether a wrong answer came with
HIGH confidence. It links to *Evidence-aware pre-LLM score* (`docs/roadmap.md`).

## Solvers

The same corpus and grading apply to every solver:

| Solver | Root cause it is graded on |
|---|---|
| `rules` | Top hypothesis from evidence-first generation (ontology chains, anchors, `RemediationEngine`), before any LLM call |
| `llm:<model>` | LLM `root_cause`, e.g. a generic Ollama model |
| `slm:<model>` | Phase 2: a small specialized model, same prompt |

This answers the "LLM or not" question with numbers rather than opinion:

- Where `rules` already PASSes (tier A, probably), the LLM adds wording, not
  diagnosis.
- Where `rules` FAILs and `llm` PASSes (tiers B and C), the LLM earns its cost.
- Where both FAIL, an SLM is worth training, or the evidence is not in the context
  (a collector problem, not a model problem).

### Train / held-out split (required before any SLM training)

Training a model on these scenarios and then grading it on them proves nothing. So:

- Every scenario belongs to a **family** (e.g. `cascade`, `decoy`, `negative`).
- Training data is generated as variants of a family (other names, namespaces, values,
  log wording). It is never the case files themselves.
- At least one family is **held out**: never used for training, only for grading.
- Each graded SLM run records which families were in its training set.

## Catalog v2 (tier C)

Same fixture format as today (`case_loader.py`: `kube/`, `helm/`, `prometheus/`,
`loki/`, `otel/`, `expect.json`). Cases are numbered in proposal order. Each one gets its
own detailed spec section in `docs/test-cases.md` (§ *Scenario catalog v2 — tier C cases*) before its fixtures are written.

| Case | Family | Symptom seen | Real cause | Decoy | Where the cause is | must_not_contain |
|---|---|---|---|---|---|---|
| h017 | cascade | `session-api` in CrashLoopBackOff | upstream `redis-master` StatefulSet pod OOMKilled and restarting | the crashing pod itself | upstream pod status + api logs `connection refused` to `redis-master:6379` | image, configmap, secret |
| h018 | cascade | timeouts on 3 services in 2 namespaces | CoreDNS pods crashlooping in `kube-system` | NetworkPolicy present in one namespace | kube-system pods + logs `lookup … i/o timeout` | networkpolicy, egress |
| h019 | change | 5xx rising on `checkout` | ConfigMap change (wrong `DB_URL`) rolled out 20 min before | HighCPU alert on the same pod | new ReplicaSet revision + configmap drift | cpu, limit |
| h020 | decoy | p99 latency SLO burn | new image tag (regression) | loud memory-pressure alert on a pod in another namespace | ReplicaSet image change + traces slower since rollout | memory, oom |
| h021 | node | random latency on `search` | batch job in another namespace saturating the node CPU | the app's own CPU limits | node metrics + co-located pods | limit, throttl |
| h022 | negative | one old warning event, all pods Ready | nothing: resolved transient restart | the warning event | — (`expected_outcome: no_incident`) | crash, oom, image |
| h023 | negative | pods not Ready, no event names a cause, Prometheus / Loki / OTel unreachable | unknown | — | — (`expected_outcome: insufficient_evidence`) | any specific cause from tiers A/B |
| h024 | multi | `worker` CrashLoop + `api` OOMKilled | missing Secret on worker **and** memory limit drift on api | — | events + helm drift | — (`root_cause_contains_all`) |
| h025 | change | node upgrade stuck, drain never finishes | PodDisruptionBudget `minAvailable` = replicas | node NotReady events | PDB object + eviction events | notready, kubelet |

Retrofit: h014 and h015 get `root_cause_must_not_contain` from their failed live answers.

## Replay harness (proposed)

`tools/veracity_replay.py --solver rules|llm:<model> [--cases h0NN,...]`:

- loads each case through the existing harness (`run_case`), with the real model instead
  of the mock (or no LLM for `rules`);
- writes `tests/veracity/replay/<solver>/<case>.json` with the fields above;
- prints a table: PASS rate per tier and per solver, and **overconfidence** (FAIL with a
  HIGH label) and **false-alarm** rate (negative cases that name a cause).

It is not part of CI (it needs a model). The `rules` solver is deterministic and can be
in CI. `tests/veracity/replay/` stays disjoint from `data/examples/`, for the same reason
as `docs/veracity-benchmark.md` § *What this does not touch*.

## Order

1. This spec. Then one detailed spec section per case in `docs/test-cases.md`.
2. Grading v2 in `grade_veracity()` (`must_not_contain`, `expected_outcome`,
   `contains_all`) + h014/h015 retrofit, with unit tests.
3. Fixtures h017–h025, in tier order, each with its offline evidence-path test (as h013–h016).
4. Replay harness, `rules` solver first (CI), then `llm:<model>` locally.
5. Only then: the single live validation on GCP / Scaleway, graded with the same rules.

## Open questions

- Which Ollama models form the baseline (size and quantization that fit the sovereign
  target)?
- Is h022/h023 graded only on the LLM label, or also on the verdict (`HUMAN_REVIEW`
  expected)?
- How many variants per family are needed before SLM training is worth it?
