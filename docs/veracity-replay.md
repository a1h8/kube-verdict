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

**First full-prompt LLM run (2026-10-17/18).** Records and conditions:
`tests/veracity/replay-baselines/llm-mistral-prompt_v1-store_stub-ctxfit-2026-10-17/README.md`
(code `9bb83ba`, every call read its whole prompt, the 19 native cases, 3 runs each). Result:
31/57 runs, **11/19 cases** by majority. The structured diagnosis (no LLM, code of `e6e8844`)
passes **14/19**:

| | Cases |
|---|---|
| both pass | 9 — h001, h002, h003, h005, h007, h009, h011, h014, h024 |
| LLM only | 2 — h010 (names the quota; the fixture's node-CPU message misleads the rules), h013 (no SLO rule exists) |
| structured only | 5 — h004, h008, h015, h022, h026 |
| neither | 3 — h006, h012, h016 |

Read with its limits: the rules were written knowing these cases, the LLM was not; on h004 the
LLM's answer is right but worded "not present", which the synonyms do not accept (a grading
defect, to amend before any re-grade); on the two healthy cases (h022, h026) the LLM names a
cause 6/6 times (MEDIUM), the structured diagnosis abstains; the LLM takes 242 s median per run,
the structured diagnosis 3.7 s per case. Both ran with the loader-made drifts of F11. No
conclusion on keeping the LLM is drawn from 19 cases the rules were written on.

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
