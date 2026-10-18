# Fix plan 001 — closing the holes found by the audit

> 2026-10-18. One PR, one commit per action, each with a test that passes and a control that can
> fail, and the structured replay before/after in its message. Findings: `docs/case-audit-001.md`
> (F*), `docs/reasoning-path-tests.md` (P*), `docs/structured-diagnosis.md` (S*); the others are
> stated here. Corrections only: no new capability.
>
> Expected effect: the structured number (14/19 on the development set) **goes down** at steps 2
> and 4 — evidence made by the test loader and lenient fixtures are removed. That is the real
> number.

## 1. One pipeline

- [ ] **H3 — four entry points, four pipelines.** The CLI (`main.py`, used by the CronJob) calls
  `RCAAnalyzer` directly and is the only one to collect policies; the API runs the LangGraph
  workflow without policies; the replay runs it with GitOps off; the MCP server has its own drift
  paths. → CLI, API and MCP run the same workflow. Test: the same fixture through the three entry
  points gives the same evidence.
- [ ] **H2 — `PolicyCollector` only in the CLI.** → collected in the workflow.
- [ ] **H4 — the product does not use the structured diagnosis.** `rca/diagnosis.py` is called only
  by the measurement tools. → the workflow runs `extract_signals` + `diagnose`; its outcome is the
  report's diagnosis; the LLM, when enabled, words it.
- [ ] **Two rule engines.** The old `RemediationEngine` still injects the hypothesis into the LLM
  prompt and backs the reasoning-path "rule" step. → the structured diagnosis replaces it there.

## 2. Real evidence

- [ ] **F11 — on a live cluster the Helm detector sees no memory, image or probe drift**; those
  drifts (h002, h003, h009, h024) are made by the test loader. → the GitOps render-vs-live path
  (chart rendered locally, F9 already reads the live containers) is wired into the workflow and
  produces `drift.config` with its provenance; the loader stops making `drift.helm.*`.
- [ ] **h012 end to end** in the replay through that path, nothing injected.
- [ ] **H1 — `env.*` is never written in production**: the GitOps env diff is blind live, and the
  database checks built from `env.DB_HOST` never appear. → read env from the live containers.
- [ ] **H5 — collection failures read as absence.** `ontology/discovery.py:101`,
  `ingestion/helm_collector.py:203`, `ingestion/git_provider.py:114,120` swallow errors and return
  empty. → each reports `fallback: true` like Prometheus/Loki/OTel, and a diagnosis does not
  abstain as `no_incident` when a collector that could carry evidence failed.

## 3. Measurement

- [ ] **R1 — a replay child that dies leaves no trace** (`_run_in_child` ignores the exit code).
  → the failure is printed and appended to `failures.jsonl`; `--resume` retries it.
- [ ] **H7 — rates are computed on recorded runs, not expected ones.** → `REPORT.md` counts
  expected runs; a missing run is a FAIL.
- [ ] **A4 — "not present" is not accepted for "missing"** (found reading the 2026-10-17 mistral
  answers, h004). → dated amendment to `tests/veracity/synonyms.yaml`, re-grade.

## 4. Cases and claims

- [ ] **D2.1 — h016's replica drift is attached to the wrong Deployment** (catalog-api, the decoy).
  → release-to-workload matching fixed.
- [ ] **Fixtures from the audit:** h002 and h010 (two causes each), h005 (RBAC denial as an Event
  Kubernetes never emits), h006 (a kubelet message that names the cause); **h011** retired.
- [ ] **F10 — `make demo` prints fixed verdict lines** and computes its own diff. → it prints what
  the diagnosis computes.
- [ ] **H8 — public claims outside the README**: `SKILL.md` (twice) says "air-gapped by default";
  the plugin descriptions say the live cluster is diffed. → aligned with the README.
- [ ] **H6 — tests that cannot fail**: `test_hybrid_pipeline_005.py::test_any_hypothesis_fires`
  (prints only), `test_workflow.py:1007` and `test_workflow_nodes_coverage.py:372` (accept any
  result). → real assertions.
- [ ] `VECTOR_STORE_TYPE` is read nowhere. → removed.

## Not in this PR (open work, not corrections)

HPAs, ResourceQuotas, LimitRanges, NetworkPolicies and RoleBindings collected live as typed objects
(needed for the HPA case of F9, F6 and h005/h006 live); Monte Carlo and the synthetic PatchTST
series out of the decision path; a dependency lockfile.

## Measurement after the PR

Block B, on the merged code: rules, structured, mistral × 3, qwen2.5:3b × 3, the 19 native cases;
a block counts only when complete (`docs/veracity-replay.md`).
