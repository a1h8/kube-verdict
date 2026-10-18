# Fix plan 001 — closing the holes found by the audit

> 2026-10-18. One PR, one commit per action, each with a test that passes and a control that can
> fail, and the structured replay before/after in its message. Findings: `docs/case-audit-001.md`
> (F*), `docs/reasoning-path-tests.md` (P*), `docs/structured-diagnosis.md` (S*); the others are
> stated here. Corrections only: no new capability.
>
> Expected effect: the structured number (14/19 on the development set) **goes down** at steps 2
> and 4 — evidence made by the test loader and lenient fixtures are removed. That is the real
> number. Measured offline on 2026-10-18 by dropping the signals whose evidence only the loader
> writes: **10/19**; 8/18 once h005 (an Event Kubernetes does not emit) and h011 are set aside.
>
> H9–H46 were added on 2026-10-18 by a file-by-file pass over every module; the order below is the
> order of the commits.

## 0. Safety

- [x] **H15 — the dry-run ran commands kubectl cannot dry-run.** `_exec_dry_run` appended
  `--dry-run=server` to every kubectl line and ran it, before the human gate. → only verbs that
  have a server-side dry-run (checked against `kubectl <verb> --help`, v1.37) are run; any other
  line, an explicit `--dry-run` other than `server`, `--` or a flag that changes the target
  cluster or identity is not run and is left for manual review. Tests:
  `tests/unit/test_dry_run_safety.py` (nothing runs for each refused line; control: the allowed
  verbs run with `--dry-run=server`).
- [x] **H42 — `propose_patch` accepts any `file_path`.** The path is joined to the clone and read
  as is. → a path that resolves outside the clone is refused.
- [ ] **H41 — the Google client puts its key in the URL**, so a failed request carries it into
  the error message and the session state. → the key goes in a header; error messages are
  redacted. With it, **H47**: `GoogleClient.chat` was defined at module level (indentation), so
  the client had no `chat`.
- [x] **H31 — the Git token is part of the clone URL**, so a failed clone logs it and the clone's
  `.git/config` keeps it. → the token is passed to git through an environment-scoped credential
  helper, never in the URL; command errors are redacted.
- [ ] **H16 — `GET /{id}/state`, `GET /{id}/stream` and `DELETE /{id}` are not behind
  `require_auth`.** `/stream` resumes the graph. → the three routes require auth, like the other
  session routes.
- [ ] **H44 — OIDC with only a JWKS URL accepts a token issued for any application of the
  provider**, and `/feedback` does not record who approved. → OIDC requires an issuer and an
  audience (startup error otherwise); the approver's subject is stored with the decision.
- [ ] **H46 — the Streamlit UI runs remediation commands outside the decision gate**, with any
  binary. → the UI runs only what the dry-run gate accepts (`_kubectl_dry_run_refusal` and the
  helm equivalent) and only when the session's verdict is not NO_GO.
- [ ] **H18 — an approval never reaches remediation.** `log_human_decision` runs before
  `human_review`, so `human_router` always reads "reject". → the decision is logged after the
  interrupt; test: resume with `approve` reaches `remediation`, `reject` does not.
- [ ] **H19 — AUTO accepts an irreversible change.** One inverse among N commands marks the whole
  set reversible, and `apply -f X` is "rolled back" with `delete -f X`. → every command needs its
  own inverse; `apply` has none we can compute; `delete` of a PVC, namespace or StatefulSet is
  irreversible.
- [ ] **H23 — Kubernetes annotations and kube-verdict's evidence share one dictionary.** An
  object annotated `drift.…` or `missing.…` by anyone produces a signal and prompt text. →
  annotations read from the cluster are kept apart (`k8s_annotations`); evidence keys are written
  only by collectors. Text from logs, events, traces and alerts is marked as untrusted data in the
  prompt.
- [ ] **H35 — the OTLP receiver binds `0.0.0.0` by default, without a body limit.** → default
  `127.0.0.1`, a body limit, an optional bearer token.
- [ ] **H36 — `ChartParser.from_tgz` extracts without a filter.** → `filter="data"`.
- [ ] **H13 — the remediation diff shown for approval can start from empty values** when
  `helm get values` fails. → the failure is shown, no diff is computed.

## 1. One pipeline

- [x] **H3 — the CLI runs its own pipeline.** `main.py` (the CronJob's entry point) collected
  K8s/Helm/Helmfile itself, policies only with `--policy`, then called `RCAAnalyzer` directly —
  no Prometheus, Loki, OTel, GitOps, anchors or decision. The API and the MCP `kube_rca` tool
  already run the canonical `services.investigation_service`. → the CLI runs it too; `--stream`
  prints the pipeline's steps; `--policy` is a no-op kept for existing callers. Tests:
  `tests/unit/test_cli.py` (the service is called; control: the CLI module no longer has its
  own collectors or analyzer).
- [x] **H2 — `PolicyCollector` only in the CLI.** → collected in the workflow as its own `policy`
  step (so a failure counts in `_ingestion_failures`, H12), with the K8s collector's API client
  (impersonation applies), and a refused listing is reported (`last_error`), not read as "no
  violation".
- [ ] **H9 — the graph is cached per thread id, never evicted.** The API uses the session id, so
  a second investigation on a session (or a `/feedback` re-run) diagnoses the previous snapshot.
  → the cache lives for one run only.
- [ ] **H10 — the rendered-manifest anchors never run.** `gitops_node` stores the Git provider in
  `config`, which LangGraph does not pass to the next node. → the provider is rebuilt from the
  state where it is needed.
- [ ] **H11 — `GIT_REPOS_JSON` is read only by the UI**, and malformed JSON is ignored silently.
  → the workflow reads it; a parse error is logged.
- [ ] **H40 — the API searches a vector store from an earlier run** (any cluster) and puts its
  texts in the prompt. → the cluster index is built per investigation; only the knowledge base
  (examples, docs) persists.
- [ ] **H39 — resolved incidents and documents are indexed only by the UI.** → indexed by the
  workflow's `index` step for every entry point.
- [ ] **H17 — three "expected state" paths; the only real render-vs-live diff runs outside the
  workflow** (MCP tools). → one path, in the workflow (with F11).
- [ ] **H4 — the product does not use the structured diagnosis.** `rca/diagnosis.py` is called only
  by the measurement tools. → the workflow runs `extract_signals` + `diagnose`; its outcome is the
  report's diagnosis; the LLM, when enabled, words it.
- [ ] **Two rule engines.** The old `RemediationEngine` still injects the hypothesis into the LLM
  prompt and backs the reasoning-path "rule" step; its rules claim a precise cause on a broad clue
  (any Pending pod, any "not found" event, H22) and propose fixed commands (`demo/charts/…`, H21).
  → the structured diagnosis replaces it there.
- [ ] **H20 — the product reads the LLM's raw confidence word** (`MEDIUM:`), the measurement the
  normalised one. → one normaliser for both.

## 2. Real evidence

- [ ] **F11 — on a live cluster the Helm detector sees no memory, image or probe drift**; those
  drifts (h002, h003, h009, h024) are made by the test loader. → the GitOps render-vs-live path
  (chart rendered locally, F9 already reads the live containers) is wired into the workflow and
  produces `drift.config` with its provenance; the loader stops making `drift.helm.*`.
- [ ] **H28 — the render-vs-live drift is written as `gitops.*`, which neither the prompt nor the
  signals read.** → part of F11: the signals read it.
- [ ] **H32 — `helm template` gets the values as `--set`**: lists are dropped, commas split a
  value, strings become numbers. → a values file.
- [ ] **H30 — with a GitHub URL the GitOps path never reads Git** (`GithubProvider` has no local
  checkout). → GitHub repositories are cloned like any other.
- [ ] **H29 — rendered objects without a namespace match a live object in any namespace.** → the
  release namespace is used.
- [ ] **H26 — every `managed-by=Helm` object is linked to every release of its namespace**: two
  healthy releases make two false replica drifts. → linked by `meta.helm.sh/release-name` and the
  `app.kubernetes.io/instance` label only. Likely cause of **D2.1** (h016).
- [ ] **H25 — a pod is tied to its workload by name prefix.** → owner references
  (Pod → ReplicaSet → Deployment) are linked and used.
- [ ] **H27 — Helmfile CLI mode returns no release** although it logs a fallback. → the fallback
  parses the YAML.
- [ ] **h012 end to end** in the replay through that path, nothing injected.
- [ ] **H1 — `env.*` is never written in production**: the GitOps env diff is blind live, and the
  database checks built from `env.DB_HOST` never appear. → read env from the live containers.
- [ ] **H5 — collection failures read as absence.** `ingestion/helm_collector.py:203`,
  `ingestion/git_provider.py:114,120`, the K8s collector's listings other than ConfigMaps / PVCs /
  ServiceAccounts (H24), metrics-server, and the discovery's refused groups and CRDs (logged at
  debug only) return empty. → each reports `fallback: true` like Prometheus/Loki/OTel, and a
  diagnosis does not abstain as `no_incident` when a collector that could carry evidence failed.
- [ ] **H33 — an alert that matches no entity is dropped silently.** → kept as cluster-level
  evidence and counted.
- [ ] **H34 — the Loki query assumes the OTel Collector's labels** and keeps the last 20 lines of
  any level. → labels are configurable; error lines first.
- [ ] **H37 — the graph walk from a sick pod reaches almost nothing** (outgoing edges only;
  quotas, logs, traces, alerts and scaling not in the incident relations). → the walk follows the
  incident relations both ways.
- [ ] **H38 — an old `Failed` pod (Evicted, failed Job) is a seed**, and a Deployment scaling up
  is "Unhealthy". → terminated pods older than the window are not seeds; the rollout guard applies
  to Deployments.
- [ ] **H43 — synthetic PatchTST series built from the current state enter the prompt as
  anomalies, recorded as `mode: real`.** → synthetic series are labelled, never sent to the
  prompt or the decision.

## 3. Measurement

- [ ] **H14 — the replay never runs the production collectors**; the loader links every object to
  the case's release, builds `QUOTA_BLOCKS`, reads PolicyReports its own way, makes `drift.helm.*`
  and passes RoleBinding / NetworkPolicy lists production passes as `None`. → the loader builds
  what the collectors build, from the same code; the gaps that remain are listed per case.
- [ ] **R1 — a replay child that dies leaves no trace** (`_run_in_child` ignores the exit code).
  → the failure is printed and appended to `failures.jsonl`; `--resume` retries it.
- [ ] **H7 — rates are computed on recorded runs, not expected ones.** → `REPORT.md` counts
  expected runs; a missing run is a FAIL.
- [ ] **Prompts are not recorded** — what the model read cannot be checked afterwards. → each run
  stores its prompt.
- [ ] **A4 — "not present" is not accepted for "missing"** (found reading the 2026-10-17 mistral
  answers, h004). → dated amendment to `tests/veracity/synonyms.yaml`, re-grade.

## 4. Cases and claims

- [ ] **D2.1 — h016's replica drift is attached to the wrong Deployment** (catalog-api, the decoy).
  → release-to-workload matching fixed (H26).
- [ ] **Fixtures from the audit:** h002 and h010 (two causes each), h005 (RBAC denial as an Event
  Kubernetes never emits), h006 (a kubelet message that names the cause); **h011** retired.
- [ ] **F10 — `make demo` prints fixed verdict lines** and computes its own diff. → it prints what
  the diagnosis computes.
- [ ] **H8 — public claims outside the README**: `SKILL.md` (twice) says "air-gapped by default";
  the plugin descriptions say the live cluster is diffed; `SECURITY.md` says no data leaves the
  machine (remote LLM providers, the embedding download and the GitHub API do); the system prompt
  says "air-gapped" whatever the provider; `docs/configuration.md` says the database can be a
  Postgres URL (it is always SQLite). → aligned with what the code does.
- [ ] **H6 — tests that cannot fail**: `test_hybrid_pipeline_005.py::test_any_hypothesis_fires`
  (prints only), `test_workflow.py:1007` and `test_workflow_nodes_coverage.py:372` (accept any
  result), the human-review tests (assert the decision, not the path, H18), the anchor tests (call
  nodes by hand with one shared config, H10). → real assertions.
- [ ] **Dead code**: `VECTOR_STORE_TYPE` is read nowhere; `reasoning/beam_search.py`'s selection
  functions have no caller and its NO_GO condition needs 3 path switches where at most 2 exist. →
  removed.

## 5. Live validation (once, at the end)

- [ ] **H45 — the project's chart grants no Secrets**, where Helm 3 stores its releases, nor
  quotas, limit ranges, webhooks or impersonation: deployed with it, kube-verdict sees no release.
  → to confirm live, then the chart grants what the collectors read (Secrets of type
  `helm.sh/release.v1` only, through a dedicated Role) and the docs say what dry-run needs.

## Not in this PR (open work, not corrections)

HPAs, ResourceQuotas, LimitRanges, NetworkPolicies and RoleBindings collected live as typed objects
(needed for the HPA case of F9, F6 and h005/h006 live); Monte Carlo out of the decision path; a
dependency lockfile.

## Measurement after the PR

Block B, on the merged code: rules, structured, mistral × 3, qwen2.5:3b × 3, the 19 native cases;
a block counts only when complete (`docs/veracity-replay.md`).
