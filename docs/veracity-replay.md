# Veracity replay — what we measure offline, and how

> Offline counterpart of [veracity-benchmark.md](veracity-benchmark.md) (live captures).
> Harness: `tools/veracity_replay.py`; spec of the harness: [test-cases.md](test-cases.md)
> § *Veracity replay harness*.

## What we measured (2026-10-12)

| Claim | What actually proves it |
|---|---|
| "The evidence reaches the model" | ✅ h013–h016, h022, h024 offline: markers in every analyze prompt |
| "The decoy is filtered" | ✅ for wrong-namespace / not-ready decoys (h013–h016) |
| "The model names the right cause" | ❌ only `ScriptedLLM`, which answers HIGH when the markers are present: tautological |
| "Low confidence when evidence is missing" | ⚠️ with the scripted LLM only (degraded-path test); the pre-LLM score does **not** drop (h014/h015: 0.49 → 0.54) |
| Real-model veracity | 0/2 live (2026-09-26), never re-measured |

Grading v2 (`tools/veracity.py`) exists and is unit-tested, but it has graded only two
real answers so far.

**[amended 2026-10-15]** The table above is the state of 2026-10-12, kept as written.
Since then, "the model names the right cause" is measured with a real model offline:
mistral, 30/54 runs over the 18 native cases (§ *First LLM baseline*), and grading v2
has graded those 54 runs. Live is still 0/2 (2026-09-26), not re-measured.

## First LLM baseline (2026-10-14) — mistral, prompt v1, no retrieval

Records: `tests/veracity/replay-baselines/llm-mistral-prompt_v1-store_stub/` (54 runs).

| Tier | Pass | rules alone |
|---|---|---|
| A — stated cause (h001–h012) | 20/36 | 3/12 |
| B — cause in one signal (h013–h016) | 3/12 | 0/4 |
| C — reasoning (h022, h024) | 0/6 | 1/2 |
| **All** | **23/54 (43 %)** | 4/18 |

What it says, after reading the failing answers:

1. **The confidence label carries no information.** 53/54 runs answer MEDIUM, right or
   wrong (one run gave no label). h022 (nothing broken) fails 3/3 only because the model
   never says LOW. Overconfidence reads 0 only because HIGH never appears.
2. **The verdict is unstable**: 9 of 18 cases get both `NO_GO` and `HUMAN_REVIEW` across
   their 3 runs (26 `NO_GO`, 28 `HUMAN_REVIEW` overall). Same input, different decision.
3. **Keyword grading under-counts tier A.** h004 ("the necessary ConfigMap and Secret
   have not been created") and h005 ("the service account does not have the necessary
   permissions") are right but FAIL: no `missing` / `forbidden` / `rbac` token. The
   grader needs synonym groups (e.g. `missing|absent|not created|not found`); records
   keep the answer text, so they can be re-graded without re-running.
4. **The dominant wrong answer is the `helm_drift` decoy.** h014, h015, h016 answer
   "Helm drift / replicaCount", h024 "Helm drift … memory". That is the rules' top
   hypothesis on 9/18 cases, and the workflow injects it into the analyze prompt
   (`HYPOTHESIS TO INVESTIGATE: …`). The fixtures' small `values.yaml` / `release.json`
   differences create a spurious drift and the model follows it. Two levers: fixtures
   without unintended drift, and a rule weight that does not let a generic drift
   outrank a specific signal.
5. **`answerable` is 100 % and therefore uninformative**: the injected hypothesis text
   alone can put ground-truth keywords in the prompt. It must be computed on the
   evidence sections only, not on the whole prompt.

**Re-graded with synonyms (2026-10-14, prerequisite 1 of `docs/evidence-score.md`).**
Synonyms live in `tests/veracity/synonyms.yaml` (data, not code; `VERACITY_SYNONYMS`
overrides it), and `tools/veracity_replay.py --regrade DIR` grades recorded runs again
without re-running a model (`REGRADED.md` next to the records).
- All: 23/54 → **30/54 (56 %)**. Tier A 20/36 → **27/36**: h004 0/3 → 3/3 ("the
  absence of the ConfigMap"), h005 0/3 → 3/3 ("lacks the necessary permissions"),
  h011 0/3 → 1/3.
- Tier B 3/12 and tier C 0/6 do not move: those failures are real (the `helm_drift`
  decoy, no LOW on h022), not grading.
- Borderline: h011 run 1 passes through `pending ← unschedulable`, but its cause ("volume
  node affinity conflict") is only close to the scenario's (a PVC still bound to the old
  pod). Counted, flagged.

**[amended 2026-10-17] Re-graded with cause concepts** (`docs/case-audit-001.md`
§ Correction 1 and amendments A1–A3): every concept of the cause must be named, and a
concept may not be satisfied by the symptom. Same 54 records, nothing re-run:
**28/54** (tier A 25/36, B 3/12, C 0/6); rules alone 3/18. The total moves little but not
on the same cases: h006 and h010 lose the runs that restated the symptom or rode on a
leaked message; h004 and h005 gain correct answers the keyword threshold rejected.
Records: `REGRADED.md` (this grading) and `REGRADED-2026-10-14-synonyms.md` (the previous
one), next to the records. These numbers are on cases of which eight are still to be
corrected or retired; they are not compared with later runs as if the cases were the same.

**[amended 2026-10-17] Invalidated: the model read ~17 % of each prompt.** The replay
created `OllamaClient` without `num_ctx`, so the default 2048 applied; Ollama truncated the
prompts (~6 200 tokens → ~1 027, kept from the end). The 54 runs above, and every grade of
them, measure mistral on the tail of the prompt, not on the evidence. They stay as records.
The next measurement sets `num_ctx` to fit the prompt and records the tokens actually read
(`docs/case-audit-001.md` F8).

Next measurements (decided 2026-10-14): `--store faiss` (production retrieval), then
prompt v2 (section 7), on the same cases, each frozen under its own configuration name.

## How a case reaches the model (clarified 2026-10-14)

A case is **not** a document in a vector base. Its fixtures become a graph
(`case_loader` / the collector), and `ContextBuilder` injects the graph's evidence
**directly** into the prompt as sections: unhealthy entities, drift, events, alerts,
logs, traces, Helm. The vector store only adds to it. When a real store is present, the
graph *of this investigation* is indexed in FAISS, and `hybrid_search` adds a "related"
section, anchor-based hypotheses and the re-ranking after a failed path.

- CI tests and the replay so far use `StubStore`: no retrieval at all.
- **Production has two store configurations that never combine** (corrected
  2026-10-14, read from `api/app.py` and `workflow/nodes.py` `index_node`):
  - *Through the API with a persisted index* (`index.faiss`, or rebuilt from the DB):
    the store holds the knowledge base (runbooks, K8s docs, Helm charts, examples) and
    is passed to every session. `index_node` sees a store and **skips indexing**: the
    investigation's graph and its anchor violations are never indexed, so
    `hybrid_search` finds neither the entities at fault nor the `anchor:` hypotheses
    (RRF-on-anchors does not run through the API).
    *Fixed 2026-10-18 (`docs/fix-plan-001.md`, H40, H39): the API loads no store; every
    investigation indexes its graph, anchors, charts, examples and documents.*
  - *Without a persisted index* (CLI, `test_native_helm_dialogue`): `index_node`
    builds a FAISS on the graph and its anchors only, with no knowledge base.
- **The graph is rebuilt for every investigation**: a snapshot taken by the collector,
  with no watch / informer and no persistence. Fresh by construction, but there is no
  history ("what changed 20 minutes ago", needed by h019, h020, M2, M7 and P3), and
  nothing for a resolved example to attach to. A persistent, watch-updated, timestamped
  graph is an architecture question for the next checkpoint.
- The 54 local `data/examples/` entries (gitignored, not in CI) all have the query
  `pods oom`: repeated approvals, and the store does not deduplicate.
- No corpus of all the cases exists, on purpose. If the cases were retrievable, the
  model would find the answer as a "similar incident" and veracity would be inflated.
  The only retrieval corpus is `data/examples/` (human-approved resolutions).
- For the sLLM, the cases and their generated variants are the **training data**, split
  by family into train / held-out. That split is what will need metadata filtering
  (pgvector, Qdrant or Weaviate), not retrieval speed.

Consequence: the first `llm` baseline (2026-10-13/14) is the **no-retrieval** point
(`store: stub`). The production configuration is measured next with `--store faiss`.

## Evidence only the test loader produces (found 2026-10-13)

`missing.secret.*`, `missing.configmap.*`, `missing.pvc.*`, `missing.serviceaccount.*`
and `netpol.*` annotations are produced **only** by `_detect_missing_deps()` in
`tests/integration/cases/case_loader.py`. `rca/context_builder.py` turns them into fix
hints and context, but no production code creates them: on a live cluster they never
exist. So h001–h006 pass offline on evidence the live pipeline cannot produce, and
the `rules` baseline (4/18) is optimistic for those cases. `docs/roadmap.md` lists
"Deployment readiness detection — `_detect_missing_deps()`" as done.

Also to verify: in `ingestion/k8s_collector.py`, pods are collected before ConfigMaps
and Secrets, and `_lazy_edge` only links to entities already in the graph, so the
pod → ConfigMap / Secret edges may never be built live.

Done 2026-10-14 (`docs/test-cases.md` § *Live parity*): the detector is in
`ingestion/readiness.py` and both the live collector and the fixture loader call it.
The pod-dependency edges and container-state shape are fixed too. Still offline-only:
`missing.rbac.*` and `netpol.*` (RoleBindings and NetworkPolicies are not collected as
typed objects live).

## The loop

```
native cases ──► run_case(real model) ──► grade() ──► tests/veracity/replay/<model>/<case>.json
      ▲                                                        │
      └──────── new scenario / fix ◄── read the table ◄────────┘
```

1. **Replay harness** — `tools/veracity_replay.py`. It is `run_case()` (the same
   offline workflow and fixtures as the CI tests) with the real `OllamaClient` in place
   of `ScriptedLLM`. No cluster is needed, so this is not a live session.
2. **Two numbers per failure, not one.** For each case, record whether the ground truth
   was **answerable**: the `root_cause_contains` groups pass `grade()` on the analyze
   prompt itself (the check h024 already does). This separates the two failure modes:
   - answerable and FAIL → **the model** is wrong (prompt, model size, specialization);
   - not answerable → **the pipeline** did not deliver the evidence (collector, context
     budget, retrieval).

   Without this split we cannot tell whether to work on PatchTST, on the context builder
   or on the model.
3. **Repeat.** A local model is not deterministic. Each case runs 3 times and the
   record keeps all 3, as a pass rate.
4. **Per solver.** `rules` (top pre-LLM hypothesis, no LLM) and `llm:<model>` on the
   same corpus (`docs/scenarios-v2.md` § Solvers). If `rules` already passes tier A, the
   LLM adds wording there, not diagnosis.

## Comparison — structured alone vs LLM vs sLLM (protocol fixed 2026-10-20, before any run)

Asked on 2026-10-18; runs after fix plan 001 is closed and the new full diagnosis is done.
Everything below is fixed now; a change after the first run is a dated amendment.

**Configurations**, one commit, one frozen case list (the native cases after the audit's
fixture corrections), `EXAMPLE_LOOKUP_DISABLED=1`, stub store:

| Name | `--solver` | Model | Runs per case |
|---|---|---|---|
| none | `structured` (= `LLM_PROVIDER=none`, `tests/unit/test_structured_in_product.py`) | — | 1 (deterministic) |
| LLM | `llm:mistral` | mistral 7.2B Q4_K_M | 3 |
| sLLM | `llm:qwen2.5:3b-instruct` | qwen2.5 3.1B Q4_K_M | 3 |

**Tables** (`tools/compare_solvers.py`, generated from the records, never hand-written):

1. *Per configuration*, over all expected runs (a missing run is a FAIL, H7):
   report correct (%) · explanation correct (%) · LLM disagrees with the diagnosis (%) ·
   remediation proposed (% of diagnosed runs with ≥ 1 command) · remediation without an
   irreversible command (%) · missing runs · median wall s · median prompt tokens read.
2. *Per tier* (A / B / C): the same columns.
3. *Per case*: report and explanation, `x/3` per configuration.

"Report correct" is the same for the three configurations by construction (the diagnosis
decides); the columns that can differ are the explanation, the disagreement and the remediation
— that is what an LLM adds or breaks.

**Decision rules, fixed now:**
- The LLM is worth running as explainer if, on the diagnosed runs, its explanation is correct
  in ≥ 80 % and it disagrees in ≤ 10 %; otherwise `LLM_PROVIDER=none` is the recommended default.
- The sLLM is preferred to the LLM if its explanation-correct rate is within 5 points of the
  LLM's, its disagreement rate is not higher, and its median wall time is lower.
- Negative controls, every configuration: h022 and h026 stay `no_incident`; a run with the
  case's collector cut is never more confident than the connected run. A configuration that
  fails a control is reported as failing, whatever its rates.

### Amendment 2026-10-22 — the raw model, and the model on the pipeline's context

Asked on 2026-10-22, before any run of the comparison. The design rests on three pillars: the
Kubernetes ontology (the typed graph), the charts (declared in Git, applied by Helm, live), and
resolved use cases (the incident base). This amendment measures what the first two add over
handing a model every artifact the case has, and separates them from the rules.

| Name | `--solver` | Model | What the model is given | Runs per case |
|---|---|---|---|---|
| raw-LLM | `raw:mistral` | mistral 7.2B Q4_K_M | every artifact of the case, raw | 3 |
| raw-sLLM | `raw:qwen2.5:3b-instruct` | qwen2.5 3.1B Q4_K_M | every artifact of the case, raw | 3 |
| ctx-LLM | `context:mistral` | mistral 7.2B Q4_K_M | the pipeline's context (ontology + charts), no diagnosis | 3 |
| ctx-sLLM | `context:qwen2.5:3b-instruct` | qwen2.5 3.1B Q4_K_M | the pipeline's context (ontology + charts), no diagnosis | 3 |

What each comparison isolates, on the same model:
- **ctx against raw** — what the ontology and the charts add to the model, the rules aside;
- **structured against ctx** — what the rules add over a model reading the same context;
- **structured against raw** — the three together.

**Raw input, fixed now.**
- The fixture files under `kube/`, `helm/`, `chart/`, `policy/`, `prometheus/`, `loki/`,
  `otel/`, each under a header naming its path relative to the case (`kube/pod.yaml`), in
  sorted order — nothing else in the case directory: never `expect.json`, never a `README.md`
  (h012's explains the case), never the case's directory name (it names the cause).
- Never `rendered/`: it is Git's `helm template` of the case's chart, which the loader reads
  instead of rendering. The raw model receives `chart/` and `helm/`, the sources of that
  render; rendering is a step of the pipeline being compared.
- YAML is parsed and written back, so its comments are dropped: comments name the cause in at
  least 9 cases (h004, h005, h006, h010, h011, h012, h024, and the negative h022 and h026), and
  the collectors never read them. The data is the data the collectors read.
- The query is the case's `query`, as for the other configurations.
- One fixed prompt (in the code, versioned like the analyze prompt): an SRE is asked for the
  root cause, the affected resources, a remediation and a LOW / MEDIUM / HIGH confidence, and
  told to answer "no incident" when nothing is wrong; the answer uses the analyze prompt's
  section headers, so the same parser and `grade()` read it.
- No retrieval, no examples, the same Ollama, temperature 0.1, `num_predict` 512. The context
  ceiling is 16 384 tokens for the raw configurations (h016, the largest case, is measured with
  the model's tokenizer before the first run; estimates give 5 300 to 7 800 tokens). A prompt
  that still does not fit is recorded as a run, FAIL, reason `did_not_fit`, with its size — not
  as a lost run.
- No real store is read or written: `DocStore` and the example store point at empty temporary
  directories (`./data/docs` and `./data/examples` are relative paths), and no run reaches
  `save_example`. The same holds for the context configurations.

**Context input, fixed now.** The case goes through the same collectors and graph as the other
configurations; the model gets the analyze prompt (v6) built by `ContextBuilder` — unhealthy
entities, drift with its provenance, events, alerts, logs, traces, anchors, Helm releases — with
the case's query alone: no `HYPOTHESIS TO INVESTIGATE`, no structured diagnosis, no rule output
anywhere in the prompt. The prompt keeps `anchor_fix_hints` and `policy_fix_hints`: they come
from the ontology's annotations and the policy reports, not from the rules, and are part of the
context being measured. Its own root cause is graded, not the report's. Same Ollama, temperature,
`num_predict` and context ceiling as the LLM configurations above.

**Grading.** `grade()` on the answer's root cause, with the cases' cause concepts and expected
outcomes — what "report correct" grades for the other configurations. A case passes a raw or
context configuration when at least 2 of its 3 runs pass.

**Decision rules, fixed now.**
- The structured diagnosis earns its place over the raw model if it passes at least **3 more
  cases** (of 19) than the better raw configuration, and the raw configuration does not have
  fewer false alarms on h022 and h026.
- If the better raw configuration passes **as many cases or more**, the README says so, and the
  claim of the pipeline is limited to what remains (abstention, provenance of each drift, the
  decision gate) until an out-of-sample benchmark says otherwise.
- **1 or 2 cases** apart: inconclusive, reported as such.
- raw-sLLM against the structured diagnosis is reported on its own line: it is the question of
  a small local model, with or without the pipeline.
- **The ontology and the charts help the model** if, on the same model, the context
  configuration passes at least **3 more cases** than the raw one with no more false alarms on
  h022 and h026; **no more** cases: no measurable help, said as such; **1 or 2**: inconclusive.
- **The rules add to the context** if the structured diagnosis passes at least **3 more cases**
  than the better context configuration; otherwise the same three outcomes as above.

**The third pillar is not measured here.** The incident base is empty (its only content was
the pollution removed on 2026-10-17) and the corpus of `docs/incident-corpus.md` is not built.
No configuration uses resolved use cases; their value is measured by protocol 001 (R0, C3) once
the corpus exists, and claimed nowhere before.

**Known bias, stated before the numbers.** The rules were written and tuned on these 19 cases:
the structured 19/19 is in-sample, so this comparison is an upper bound of the pipeline's
advantage. It can show that the pipeline adds nothing; it cannot by itself show that it
generalises — an out-of-sample benchmark can.

**Cost, estimated.** About 4 min per raw-LLM run on the CPU-only pod (≈ 22 prompt tokens/s): ≈ 4 h
for raw-LLM, ≈ 3 h for ctx-LLM, ≈ 1.5–2 h each for raw-sLLM and ctx-sLLM — about 10 h on top of
the three configurations above (≈ 4.5 h), so the whole block runs overnight, resumable.

### Amendment 2026-10-23 — the collector-cut control, defined

Written before any run of the comparison. The negative control "a run with the case's collector
cut is never more confident than the connected run" (2026-10-20) named no collector and no run;
no code applied it. It is defined here; no threshold of this section changes.

- **The case's collector** is the one `docs/test-cases.md` § *Broadened offline coverage* §3
  already cuts: an HTTP backend the investigation reads and can lose while it keeps running —
  Prometheus, Loki or OTel — for which the case has fixtures. Six cases have one: h005, h006,
  h014 (Loki), h013 (Prometheus), h015 (OTel), and h016 (all three, each cut in turn: three cut
  runs). The 13 others carry their evidence in `kube/`, `helm/` and `chart/`: cutting the
  Kubernetes API leaves no investigation, and the replay has no cut for Helm or Git. The control
  does not apply to them, and the report says so case by case; it is not extended to them later
  without a dated amendment.
- **The cut**, per configuration, on the same commit and cases:
  - `structured`, `llm:`, `context:`: `run_case(key, cut=<collector>)` — the backend is made
    unreachable (`requests.get` raises `ConnectionError` in the collector's module), so the real
    collector code reports `fallback: true`, as in production;
  - `raw:`: the collector's directory (`prometheus/`, `loki/`, `otel/`) is left out of the raw
    input; nothing else changes.
- **Runs:** one cut run per case, collector and configuration — 8 per configuration (one for
  each of the five single-collector cases, three for h016), recorded like the others with the
  cut collector named in the record.
- **The check:** the cut run's confidence label is compared with the **median** label of the
  case's connected runs (its single run for `structured`), with no label < LOW < MEDIUM < HIGH.
  A cut run above that median fails the control; a configuration with one failing cut run fails
  the control, whatever its rates (rule of 2026-10-20). A cut run that still names the case's
  cause is reported, not failed: some causes are visible from the Kubernetes objects alone.
- **Cost, estimated:** 8 runs × 6 model configurations ≈ 2 to 2.5 h on the CPU-only pod, on top
  of the block; `structured` cut runs are offline and deterministic.

### Amendment 2026-10-23 — what every run records, and where

Written before any run of the comparison, after a read-only pass that found rules no code
applied (`docs/fix-plan-001.md` H79, H81). No threshold changes.

- **Isolation, every solver.** The 2026-10-22 rule "no real store is read or written" is
  extended from the `raw:` and `context:` configurations to all of them: `structured` and `llm:`
  also run in a temporary directory with the example and chart stores pointed inside it. On the
  AUTO path `save_example` writes the run's answer into the example store; run where the replay
  was started, it would write a case's answer into the incident base. A check after each run
  marks it invalid if a real store or database changed; it sees writes only — a read leaves no
  reliable trace, and reads are prevented by the isolation, not detected.
- **Recorded in every run:** the commit, a hash of the case's fixtures and `expect.json` (the
  frozen case list is checked, not assumed), the hashes of `tools/veracity.py` and
  `tests/veracity/synonyms.yaml`, the model's digest, and for each model call `num_ctx`, the
  estimate, the tokens read, `num_predict` and the temperature.
- **Truncated runs.** A run whose tokens read plus `num_predict` reach `num_ctx` may have been
  cut by Ollama without an error; it is recorded **invalid**, not FAIL, and reported apart.
- **Token sizes, measured.** The size estimate (2.7 characters per token) was calibrated on
  mistral and the analyze prompt only, never on raw YAML nor on qwen2.5. Offline, every
  `context:` and `llm:` prompt fits under 8 192 tokens by the estimate (the largest, h016, with
  about 11 % of margin); the raw ones are unknown. Before the first run, every prompt of the
  `raw:` and `context:` configurations is measured with each model's own tokenizer, through the
  Ollama that runs them — not h016 alone as written on 2026-10-22.
- **Records.** Each configuration writes to
  `tests/veracity/replay-baselines/<solver>[-<model>]-prompt_<v>-store_<store>-examples_<on|off>/`,
  model and prompt left out for the solvers that have none (`structured`, `rules`). The solver
  is in the name because `llm:mistral` and `context:mistral` share a model and a prompt version
  (v6): without it, their records would share a directory and one report would overwrite the
  other (`docs/protocol-001-retrieval.md`, amendment of the same day).

### Amendment 2026-10-23 — how the tables read the controls

Written before any run, with `tools/compare_solvers.py`. It fixes readings the rules above left
open; no threshold changes.

- **The label** compared by the collector-cut control is each record's `confidence`: the
  structured diagnosis's for `structured`, the workflow's final label for `llm:`, the model's own
  label for `raw:` and `context:`. The **median** of the connected runs is the lower median when
  two runs are valid (`statistics.median_low`), so a lost run never raises it. A cut run that is
  missing or invalid fails the control, as a missing run fails a case (H7).
- **The negative controls** (h022 and h026 stay `no_incident`) are read at the case level, with
  the 2-of-3 rule of every other case; the runs that named a cause are reported beside it.
- **Protocol 001's positive control** (h001 passes at least 2 of 3) applies to the `context:`
  configurations, its C1; a configuration that misses it is reported invalid — re-run once, then
  recorded invalid — and no decision is drawn from it.
- **A decision is not drawn** from a configuration that fails a control or is invalid: the table
  says which control, whatever the rates. That is also how the clauses "no more false alarms on
  h022 and h026" apply: one false alarm fails the negative control. "N more cases" is the
  difference of the cases passed,
  which equals protocol 001's net gain (cases only one passes minus cases only the other passes);
  both sets are listed.

## What an LLM run grades (2026-10-19)

The `rules` solver keeps grading the old `RemediationEngine`, which no longer runs in the
product (`docs/structured-in-product.md`, rule 7): it is the historical baseline, not the
product's answer — `structured` is.

Since `docs/structured-in-product.md` the report's cause is the structured diagnosis; an LLM run
grades the same `root_cause` as the structured solver plus what the model adds:
`explanation_veracity` (its own text, graded with the same cause concepts) and
`llm_disagreement` (its text names none of the diagnosed locations). Runs are recorded with
prompt `v4` and never compared with earlier prompts. The comparison asked on 2026-10-18 —
structured alone (`LLM_PROVIDER=none`), an LLM, an sLLM — reads these fields on the same cases.

## Report

`tests/veracity/replay/REPORT.md`, regenerated by the harness:

| Metric | Definition |
|---|---|
| Pass rate per tier | A (stated cause), B (one signal), C (reasoning), M (misdirection, `docs/scenarios-v2.md` § Tier M) |
| Answerable rate | share of cases whose ground truth is in the prompt |
| Model error rate | answerable but FAIL |
| Overconfidence | FAIL with a HIGH label |
| False alarm | negative case that names a cause |
| Wall time per case | to know what a full run costs on CPU |

## Practical constraints

- CPU-only Ollama took about 3 min per analyze call (b13 notes). 18 cases × 3 runs ×
  several calls is hours. The harness is resumable (one file per case and run) and runs
  overnight; `--cases` and `--runs` limit it.
- Model: the configured `OLLAMA_MODEL` first, as the baseline. The same table later
  compares the sovereign-sized models and the phase-2 SLM.
- `tests/veracity/replay/` never feeds `data/examples/` (same rule as the live corpus).
