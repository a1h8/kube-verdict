# Protocol 002 — the vector search, the models and the deterministic diagnosis, compared

> Written 2026-10-30, before any code or run of it. **Rules approved on 2026-10-30**, in the
> commit that adds this file, before any run; they change only by a dated amendment written
> before the run it affects (`docs/comparison-protocol-issues.md`). Its pilot is block B of
> `docs/live-validation-001.md` (amendment 2026-10-30).

## Questions

Three, each answered on the same cases, every arm reading the same evidence:

1. **The model against automation.** Does a model (LLM or small LLM) name the cause more often
   than the deterministic diagnosis, and at what cost in false alarms and compute?
2. **The vector search against the model.** Does the search alone — the record or the object
   it ranks first — point to the cause as often as a model that reads the evidence?
3. **The top N in the prompt.** When the search's first N hits are added to the model's
   context, does the model name the cause more often, and is that gain due to what the search
   found, or to more text?

Protocols 001 and R1 asked part of 2 and 3 on 19 in-sample cases with stores of 4 to 30
vectors, where the graph's own context already holds the cause (R1: M2 = 0 of 17). Their
results are not extended to larger cases: this protocol asks the questions where the search
could matter.

## Arms

| Id | What names the cause | Search hits in the prompt | Model |
|---|---|---|---|
| D | the structured diagnosis (`structured`) | — | none |
| Vc | the root cause of the top-ranked incident record (R0's solver) | — | none |
| Vi | the top 3 objects of the investigation's store (R1's solver) | — | none |
| L₀ / S₀ | the model on the pipeline's context without the diagnosis (`context:`) | 0 | LLM / sLLM |
| L_N / S_N | as L₀ / S₀ | the search's first N hits, N ∈ {1, 3, 5, 10} | LLM / sLLM |

- **Models.** LLM: `mistral` (7B); sLLM: `qwen2.5:3b-instruct` (3B). Both local, served by one
  Ollama, digests recorded. A larger reference model may be added only by an amendment written
  before any run.
- **What is searched.** The product's search: the investigation's store (`index_node`: graph,
  chart anchors) and the incident corpus, fused as `hybrid_search` fuses them. Hits are added
  as the context builder adds them, in rank order, each with its source.
- **N = 0 is the control of every N.** L₀ and S₀ are the same runs as block B's `context:`
  configurations when the cases and commit are the same; otherwise they are re-run.

## Controls on the hits (at N = 3, for both models)

| Id | What replaces the search's hits | What it separates |
|---|---|---|
| L_rand / S_rand | 3 hits drawn at random from the same store (fixed seed per case) | relevance from more text |
| L_pad / S_pad | neutral text of the same token count as the 3 real hits | the prompt's length alone |
| L_oracle / S_oracle | the record or object that carries the cause, placed first | a search that misses from a model that cannot use it |
| L_mis / S_mis | a plausible record of the same family with a wrong cause, placed first | how often the model follows a wrong hit |

N = 3 is fixed now because it is the k of R1. The controls run at no other N.

## Cases

Three sets, reported **separately**; no number pools them.

- **S1 — the native cases** (19, `tests/integration/cases/`, frozen at block B's commit). The
  rules were written on them: every result on S1 is an upper bound, labelled in-sample.
- **S2 — blind spots of the graph.** Cases where no edge links the cause to the symptom, the
  only place where the search can add what the graph lacks (R1, *What R1 does not measure*):
  ResourceQuota, LimitRange, NetworkPolicy, HPA, a dependency in another namespace, a mutating
  webhook, a shared ConfigMap. At least 2 cases per family, written from a spec per family
  **before** any rule or fixture for it, with tens of objects and several drifts of which one
  explains the symptom. Each family has one negative case (the same objects, no fault).
- **S3 — ITBench**, the primary set: incident snapshots we did not write, ground truth as cause
  objects (`kind/namespace/name`). An adapter (snapshot → the case loader → each arm → cause
  objects) is specified on its own before any S3 run; it fixes the subset used, the format and
  the score, from ITBench's own documentation, read then. Until that spec exists, the number
  and kind of S3 tasks are not stated here.
- **The incident corpus**, for Vc and the corpus hits: at least 50 records from at least 3
  sources, built and excluded as `docs/incident-corpus.md` and protocol 001 fix (no record may
  name a resource of a graded case), its hash frozen before the first run. Below 50, Vc and the
  corpus hits are reported as not measurable.

## Measures

- **Primary: the cause.** On S1 and S2, the case's grader (`tools/veracity.py`, concepts of
  every cause); on S3, the score the adapter spec fixes. A case passes an arm with a model when
  at least 2 of its 3 runs pass; a missing, truncated or invalid run counts FAIL, as in
  protocol 001.
- **Secondary**, reported, never deciding alone:
  - false alarms: negative cases where the arm names a cause;
  - abstention: cases where the arm answers `insufficient_evidence` or `no_incident`, and
    whether the evidence was there;
  - follow rate (L_mis / S_mis): runs whose cause is the misleading record's;
  - cost: prompt tokens (`prompt_eval_count`), wall time per run, CPU-seconds per correct case.

## Statistics, fixed now

- **Paired design.** Every arm runs on the same cases; each comparison is a paired table of
  case outcomes.
- **Test:** exact McNemar, two-sided, on the discordant pairs of each comparison.
- **Primary comparisons**, per model and per set, Holm-corrected together at α = 0.05:
  D vs L₀; D vs S₀; Vc vs L₀; Vi vs L₀; L₃ vs L₀; S₃ vs S₀; L₃ vs L_rand; S₃ vs S_rand.
  Every other comparison (the N curve, oracle, padding, misleading, LLM vs sLLM) is reported
  as exploratory, with its interval and without a test.
- **Effect:** the difference in pass rate with a 95 % interval from a paired bootstrap that
  resamples **families**, not cases (variants of one case are not independent).
- **No equivalence without its margin.** "No difference" is written only when the interval lies
  within ±10 points; otherwise the result is inconclusive.
- **Volume.** From block B (the pilot), the share of discordant pairs between L₀ and C2 and
  between D and L₀ gives the number of cases each primary comparison of S2 and S3 needs for a
  power of 0.8 against a 15-point difference. That number is written in an amendment before
  any S2 or S3 run. A set below it is run and reported as exploratory, never as a decision.

## Predictions, written before any number (to approve with the rules)

1. On S1, D passes more cases than L₀ and S₀ (in-sample, expected); on S3, D passes fewer cases
   than L₀ and makes fewer false alarms.
2. Vc and Vi pass fewer cases than L₀ on every set (R0: 0 of 9; R1: chance).
3. On S1, L₃ − L₀ ≤ 0 and L₃ ≈ L_rand (R1: the search adds no object). On S2, L₃ − L₀ > 0.
4. S gains more from the oracle than L, and follows the misleading record more often than L.
5. The pass rate does not rise beyond N = 3 for either model.

A prediction that fails is reported as failed, beside the result.

## Decisions

- **The search stays in the model's default path** only if L₃ vs L₀ or S₃ vs S₀ is a significant
  gain on S2 or S3, L₃ (or S₃) beats its random control there, and false alarms do not rise.
  Otherwise it stays an opt-in, off by default (as R1 left it), and the README says on which
  sets it was measured.
- **The default N** is the smallest N within 2 points of the best one, on S2 and S3.
- **The model's place.** If D vs L₀ shows no significant difference on S3, the product keeps the
  diagnosis without a model as its default and the model as an explainer; if L₀ is significantly
  better on S3, the README says so and which families D misses. No result moves the human gate.

## Run rules

Those of `docs/veracity-replay.md` and `docs/live-validation-001.md`, unchanged: one Ollama,
nothing else on it; every prompt measured with the model's tokenizer before the run, a prompt
that does not fit is FAIL `did_not_fit`; every record names its commit, arm, model digest,
store, N, corpus hash and case hash; no real store read or written; temperature and
`num_predict` as block B. A code change during a block restarts the block on the new commit.

## Order

1. Block B with C2 (the pilot), then the volume amendment.
2. The corpus to 50 records; S2's family specs, then their cases; the ITBench adapter spec.
3. The code: N and the four controls in the replay, the adapter; each tested offline.
4. One live block per set, S3 last.

## Results

Not run.

## Amendments

None.
