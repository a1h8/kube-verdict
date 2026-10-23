# The structured diagnosis in the product

> Spec, 2026-10-18 — `docs/fix-plan-001.md` H4, H17, "two rule engines", H20 (done), H5's
> abstention. Builds on `docs/structured-diagnosis.md` (§ 3: the LLM words a diagnosis, it does
> not make it). Written before the code; rules and controls fixed here, before any number.

## The problem

`rca/signals.py` + `rca/diagnosis.py` are called only by the measurement tools. The product —
API, CLI, MCP, the CronJob — runs the old path: `RemediationEngine` hypotheses injected into the
prompt, the LLM decides the cause and its confidence label, the policy gate scores that label.
The measured number (15/19 structured) describes a pipeline nobody runs; the old engine claims
precise causes on broad clues (H22) and proposes fixed commands (H21).

## The pipeline

```
collect … index ──► diagnose ──► (llm) word ──► blast_radius ──► policy gate ──► dry_run / review
                     signals                    explain only
                     diagnoses
                     outcome
```

1. **`diagnose` node**, after `index`: `extract_signals(graph)` then `diagnose(signals)`. Its
   `Outcome` (ranked diagnoses, or an abstention) is stored in the state as plain data
   (`diagnosis`: outcome, and per diagnosis family / cause / location / score / label / rule /
   checks / supporting signal ids).
2. **The report's cause is the diagnosis.** `root_cause` = the top diagnosis' cause sentence
   (all reported diagnoses when several causes are reported); `affected` = their locations;
   `confidence` = the top diagnosis' label (`HIGH` ≥ 0.75, `MEDIUM` ≥ 0.5 = `REPORT_MIN`, else
   `LOW`); `diagnostics` = the rules' read-only `checks`. An abstention reports
   `no_incident` or `insufficient_evidence` with confidence `LOW`.
3. **Abstention when a collector failed (H5).** `no_incident` becomes `insufficient_evidence`
   when a collector that could carry evidence reported `fallback: true` (`_ingestion_failures`):
   the report names those collectors. A clean collection keeps `no_incident`.
4. **The LLM words it, when one is configured.** The analyze prompt receives the ranked
   diagnoses and their supporting raw evidence and is asked to explain them and propose a
   remediation. It may not change the family, the location, the ranking or the label: if its
   text names another cause (the cause concepts of the top diagnosis absent from its root
   cause), the structured diagnosis stands and the disagreement is recorded in the state
   (`llm_disagreement`) and the report. Its remediation commands are kept as proposals; they go
   through the dry-run gate and the human gate as today.
5. **No LLM is a supported mode.** `LLM_PROVIDER=none`: the report is the diagnosis; no
   remediation command is proposed (the policy gate's "nothing to apply — never AUTO" holds).
   This is the "nothing" of the comparison LLM vs no LLM vs sLLM.
6. **The policy gate scores the diagnosis**, not the LLM's label: `score` = the top diagnosis'
   score. The LLM's own label is recorded, unused by the gate. An abstention has no action to
   score: the verdict is `HUMAN_REVIEW` — nothing to approve and nothing to block, the operator
   reviews the finding (the h022 decision of 2026-10-12, `expected_verdict`). *Amended
   2026-10-19: the first version scored an abstention 0, which made it `NO_GO`.*
7. **One engine.** `RemediationEngine` no longer feeds the prompt, the LOW fallback, the
   hypotheses or the reasoning-path "rule" step; the structured diagnoses replace it there. Its
   rule-specific remediation templates (H21) are removed with it. The confidence router's
   retry / next-path loop runs over the ranked diagnoses (`candidate_paths`) instead of the old
   hypotheses; with no LLM there is no loop.
8. **One expected-state path (H17).** The structured diagnosis reads the declared / applied /
   live comparisons (`docs/three-states.md`); the chart-store text anchors stay retrieval
   context only, and the MCP tools `helm_drift` / `expected_state_drift` call the same
   comparisons instead of their own.

## Entry points

API sessions, `/investigate`, the CLI and the MCP `kube_rca` tool all run the workflow, so they
all get the node. The Streamlit UI keeps its own pipeline (a fifth entry point, H46 family); it
is listed, not changed here.

## Tests (controls that can fail)

| Test | Passes when | Control |
|---|---|---|
| report from diagnosis | h003 replayed through the workflow: `root_cause` = `oom.memory_drift`'s cause, confidence from its score | the old `RemediationEngine` mocked to raise → same report |
| abstention on failure | healthy graph + `prometheus` fallback → `insufficient_evidence` naming `prometheus` | healthy graph, clean collection → `no_incident` |
| LLM cannot override | scripted LLM names another cause → report keeps the diagnosis, `llm_disagreement` set | scripted LLM agreeing → no disagreement |
| no LLM | `LLM_PROVIDER=none` → report = diagnosis, no LLM call, verdict never AUTO | LLM configured → one analyze call per path |
| gate on diagnosis | LLM says `HIGH`, diagnosis score 0.5 → gate score 0.5 | — |
| one engine | `RemediationEngine` is imported by no module of the workflow, API, CLI or MCP | — |

## Measurement

- The structured replay is unchanged (it calls `diagnose` directly); a new check asserts that
  the workflow's report equals the structured solver's answer on every case (the product is
  what is measured).
- The LLM replays (`--solver llm:<model>`) now grade an LLM that words a diagnosis; their runs
  are recorded with a new prompt version and never compared with earlier runs. That is the
  comparison asked on 2026-10-18 — **structured alone vs LLM vs sLLM**, same cases, same runs,
  after the new full diagnosis.

## Not in this spec

New diagnosis families, the UI's own pipeline, Monte Carlo (it scores the old pre-LLM score; it
leaves the decision path with the old engine and is removed if nothing reads it).
