# Design choices and the evidence for them

> Each design choice of kube-verdict is a **claim that can be refuted**. This page says, for each,
> the pre-registered test that can refute it, its decision rule, the controls that can fail, and
> where it stands. Thesis, as in every protocol here: **the numbers do not decide; rules written
> before the numbers do.** A threshold set after seeing a result is post-hoc and voids the
> measurement.
> Protocols: [veracity-replay.md](veracity-replay.md) § Comparison (and amendments),
> [protocol-001-retrieval.md](protocol-001-retrieval.md). Defects of those protocols:
> [comparison-protocol-issues.md](comparison-protocol-issues.md).

## 0. Run status

```yaml
code:            complete offline for the comparison and protocol 001's C2
                 (records, isolation, cuts, tables, controls, decisions — make ci green)
runs:            none of the comparison or of protocol 001       # nothing below is measured live
blocking:
  - H45 (the chart reads no Helm release), H80 (steps of the live run): fix-plan-001.md
  - C3 (`--examples on`) and the split by corpus family in the tables (CPI-18)
known_bias:      the rules were written and tuned on the 19 cases — structured 19/19 is
                 in-sample: an upper bound of the pipeline's advantage, not a generalisation
```

## 1. The choices

| # | Choice | Claim | Refuted by (pre-registered) | Rule | Status |
|---|---|---|---|---|---|
| 1 | The ontology and the charts (three states: declared, applied, live) | they help a model find the cause | ctx-LLM / ctx-sLLM against raw-LLM / raw-sLLM, same model | ≥ 3 more cases (of 19): help; 1–2: inconclusive; ≤ 0: no measurable help | not measured |
| 2 | Deterministic rules over typed signals (the structured diagnosis) | they add to a model reading the same context | `structured` against the better `context:` | same three outcomes | not measured; in-sample |
| 3 | The whole pipeline | it beats a model handed every artifact | `structured` against the better `raw:` | ≥ 3 more: earns its place; ≤ 0: the README says so and the claim shrinks to what remains (abstention, provenance, gate) | not measured; in-sample |
| 4 | The LLM as explainer of the diagnosis | its explanation is right and agrees | `llm:mistral`, diagnosed runs | explanation correct ≥ 80 % and disagreement ≤ 10 %; otherwise `LLM_PROVIDER=none` is the default | not measured |
| 5 | A small local model (sLLM) | it is enough | `llm:qwen2.5:3b-instruct` against `llm:mistral` | within 5 points, no more disagreement, faster | not measured |
| 6 | Vector search over the investigation (FAISS) | it helps the model | protocol 001, C2 against C1, per model and over both | net gain ≥ 3: stays; 1–2: inconclusive; ≤ 0 or a new false alarm: leaves | **predicted: leaves** (CPI-13: it adds no knowledge) |
| 7 | Memory of resolved incidents | recognising a known incident helps | protocol 001, C3 against C2; R0 reported alone (CPI-23) | C3: as 6; R0: no outcome — predicted to pass covered cases only, none of the 8 uncovered | corpus v1 written (11 records, 9 cases covered); R0 coded, not run; C3 not coded |
| 8 | Abstention, provenance of each drift, the decision gate | they keep a wrong answer from acting | — | no pre-registered test yet | not measured |
| 9 | Reading the applied state from the cluster (Helm releases) | the three-state comparison runs live | live validation (H45 option A) | the applied state is read, or reported as not read — never as absent | not measured live |

Choices 8 and 9 have no comparison: they are claimed nowhere as measured.

## 2. Controls, and how each can fail

A control that cannot fail checks nothing. Each control below has a test that makes it fail
(a mutant), next to the one where it passes.

| Control | Applies to | Passes when | Fails when (tested) |
|---|---|---|---|
| Negative: h022, h026 stay `no_incident` | every configuration | both cases pass (2 of 3) | a case names a cause → the configuration fails, no decision is drawn (`test_a_false_alarm_fails_the_control_and_no_decision_is_drawn`) |
| Positive: h001 passes 2 of 3 | `context:` (C1, C2) | h001 passes | it does not → invalid, re-run once (`test_the_positive_control_makes_a_context_configuration_invalid`) |
| Collector cut: never more confident than connected | the comparison's configurations | each of the 8 cut runs ≤ its connected median | above it, missing or invalid (`test_the_collector_cut_control`, `test_a_configuration_without_cut_runs_fails_the_cut_control`) |
| Anchoring: the model's label follows the prompt's quality score | `context:` (C2 against C1) | not shown: fewer than 3/4 of the cases where the score's label changes see the model's label follow it, or no more than on same-prompt cases | shown → the model's labels are reported not informative (`test_a_model_that_copies_the_score_is_anchored`; not shown: `test_a_label_that_does_not_follow_the_score_is_not_anchored`, `test_a_label_that_moves_as_much_on_the_same_prompt_is_not_anchored`) |
| Noise: C2's prompt equals C1's | C2 against C1 | the 10 same-prompt cases agree | they differ → reported as run-to-run variation (`test_on_the_same_prompt_a_difference_is_noise`) |
| Truncation | every model run | tokens read + `num_predict` < `num_ctx` | reached → invalid, never a pass (`test_a_truncated_run_is_invalid_and_never_a_pass`) |
| No real store | every run, and the test suite | nothing outside the temporary directory changed | a write → invalid run (`test_a_run_that_writes_a_real_store_is_invalid`); the suite fails (`test_the_former_writers_leave_nothing_where_they_run`) |

## 3. Measured so far (offline, deterministic, in-sample)

| What | Result | Read as |
|---|---|---|
| `structured` on the 19 cases | 19/19, no false alarm | the rules on the cases they were written on: an upper bound |
| `structured` with a collector cut (8 runs) | none above its connected label: h005, h013, h014 MEDIUM → LOW; h015 HIGH → LOW; h006 MEDIUM → MEDIUM; h016 HIGH → HIGH ×3 | the control passes; h016 keeps HIGH on redundant evidence (0.95 / 0.80 / 0.80, CPI-15) |
| C2's prompt against C1's (19 cases) | 10 identical, 1 reordered, 8 with a list of objects already present and a changed score | the basis of the C2 prediction (CPI-13) |

Nothing else is measured. A claim of section 1 is made in the README only once its test has run
and its rule has decided.
