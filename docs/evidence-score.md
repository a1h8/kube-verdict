# Evidence score — the verdict must rest on evidence, not on the LLM's label

> Spec (2026-10-14), not implemented. Review before any code.

## Why

The stated design is evidence-first: "ranks root-cause hypotheses from deterministic
evidence *before* the LLM explains them" (`docs/use-cases.md`), "it does not guess from
live symptoms" (`README.md`). The policy gate has never followed it:

- **#43 (2026-05-27)** introduced the gate with a docstring saying "Score source:
  pre_llm_confidence.score (preferred)" while the code below it mapped the LLM label
  (`score = _LLM_SCORE.get(llm_conf, 0.65)`).
- **#57 (2026-06-02)** aligned the docstring to the code ("Score source: the LLM
  diagnosis confidence label") instead of the code to the design.

The first LLM baseline (`docs/target/veracity.md` § *First LLM baseline*) shows the
consequence: the label is MEDIUM on 53/54 runs, right or wrong, so the gate decides on
an empty signal, and the verdict changes between runs on 9/18 cases.

Going back to today's pre-LLM score is not enough. `compute_confidence`
(`rca/confidence.py`) gives 0.70 of its weight to context shape, not evidence:

| Component | Weight | What it measures |
|---|---|---|
| BFS depth | 0.25 | the *configured* `BFS_MAX_DEPTH`: a constant |
| Jaccard diversity | 0.25 | how many context chunks survived dedup |
| TF-IDF features | 0.20 | how many chunks were included |
| Anchors, signals, drift, policies | rest | evidence, but counted without a link to the hypothesis |

It rises when Loki or Tempo is down (h014 / h015: 0.49 → 0.54, 2026-10-09), and every
Helm drift item adds +0.07, so the spurious `helm_drift` decoy of the fixtures *raises*
confidence.

## What the score is

`rca/evidence_score.py`, deterministic, computed after `analyze` for the **retained
hypothesis** (the root cause the run ends on), not for the context as a whole.

1. **Evidence items** are collected from the graph and context, each tied to the
   entities it is about: a Warning event naming a cause (`secret "x" not found`,
   `OOMKilled`, `FailedScheduling`…), a `missing.*` / `netpol.*` annotation, an anchor
   violation, a Helm drift field, a correlated firing alert, an error log line, an error
   trace, a policy FAIL, a container state reason.
2. **Support.** An item supports the hypothesis when it is about one of the
   hypothesis's affected entities (or their owner / dependency in the graph) **and** its
   kind matches the hypothesis family (e.g. OOM ← `OOMKilled` state, memory drift,
   memory alert; missing config ← `missing.*`, `not found` event). The match table is
   data, one row per rule family, reviewed with the rules.
3. **Score** = coverage of the independent evidence families that support it (events,
   spec / annotations, drift, metrics, logs, traces, policies), with a strength per item
   kind. One generic item cannot reach a high score on its own: a Helm drift with no
   symptom linked to the drifted field is capped low. That removes the
   `helm_drift` decoy's weight.
4. **Missing evidence is not neutral.** A collector in `fallback: true` that could have
   carried evidence for this hypothesis family lowers the attainable maximum (h014 with
   Loki down cannot reach HIGH). Contradicting evidence (e.g. memory far below the limit
   for an OOM hypothesis) subtracts.
5. **Agreement with the LLM.** The LLM's root-cause text is checked against the
   hypothesis's family keywords. Disagreement caps the score at MEDIUM and adds a reason.
   The LLM label itself is recorded but no longer used by the gate.

Output: `EvidenceScore(score: float, label: LOW | MEDIUM | HIGH, supporting: [items],
missing_families: [...], reasons: [...])`, in `report_dict["evidence_score"]`.

## What changes in the decision

- `log_policy_decision` and `IncidentReport.from_rca` pass `evidence_score.score` to the
  gate instead of the LLM label mapping. The thresholds (`AUTO ≥ 0.85`,
  `HUMAN ≥ 0.60`) are kept at first and re-set from the calibration below.
- The two fail-closed rules stay (unknown namespace, no command → never AUTO).
- `compute_confidence` stays as a context-quality diagnostic shown in the Decision
  Journey, renamed in the UI "context quality", never used by the gate.

## Calibration — measured on the replay corpus

The replay records gain `evidence_score` and its label. The score is kept only if, on the
baseline corpus:
- runs graded PASS have a clearly higher score than runs graded FAIL (report the means
  and the overlap per tier);
- on the same case, the verdict no longer changes between runs whose retained
  hypothesis is the same (the 9/18 instability should drop to the cases where the LLM
  really ends on different hypotheses);
- h022 (nothing broken) gets LOW, h014 with Loki cut cannot reach HIGH.

The baseline records do not need a re-run for a first look: the score can be recomputed
offline from each case's fixtures and the recorded root cause.

## Prerequisites (from the baseline analysis)

1. Grading with synonym groups (`missing|absent|not created|not found`…), so PASS / FAIL
   is trustworthy enough to calibrate against.
2. Fixtures without unintended Helm drift (`values.yaml` vs `release.json` differences
   that are not part of the scenario), audited case by case.
3. `answerable` computed on the evidence sections only, not on the injected hypothesis.

## Tests

- Per family: supporting items found, generic-only capped, contradiction subtracts,
  missing collector lowers the maximum, LLM disagreement caps.
- h001–h024 offline: expected labels written in each `expect.json`
  (`evidence_label`), h022 LOW.
- Gate: the verdict uses `evidence_score`, not the LLM label; golden guard updated with
  the reason.
- Stability: the same case run 3 times with the scripted LLM ending on the same
  hypothesis gives the same verdict.

## Out of scope

The sLLM, retrieval changes (`--store faiss`), prompt v2. They are measured after this,
on a gate whose decision means something.
