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
> **Measured after F11 (2026-10-18): 11/19** — the 10/19 above, plus h012 now graded through the
> production render-vs-live path. **After H48: 15/19**, every drift found by the production
> comparisons on fixtures that say what Git declared and what Helm applied.
> **After H14 and the fixture corrections: 13/18** (h011 retired; h005 exposes H49).
> **After `docs/failing-cases-002.md`: 19/19** — h011 restored, four families added, each
> checked on one case plus controls (not yet evidence that they generalise).
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
- [x] **H41 — the Google client puts its key in the URL**, so a failed request carries it into
  the error message and the session state. → the key goes in a header; error messages are
  redacted. With it, **H47**: `GoogleClient.chat` was defined at module level (indentation), so
  the client had no `chat`.
- [x] **H31 — the Git token is part of the clone URL**, so a failed clone logs it and the clone's
  `.git/config` keeps it. → the token is passed to git through an environment-scoped credential
  helper, never in the URL; command errors are redacted.
- [x] **H16 — `GET /{id}/state`, `GET /{id}/stream` and `DELETE /{id}` are not behind
  `require_auth`.** `/stream` resumes the graph. → the three routes require auth, like the other
  session routes.
- [x] **H44 — OIDC with only a JWKS URL accepts a token issued for any application of the
  provider**, and `/feedback` does not record who approved. → a JWT is refused unless issuer and
  audience are both configured; an unknown key id refetches the JWKS at most once a minute; the
  approver (OIDC subject, or the gate that let them in) is stored with the decision.
- [x] **H46 — the Streamlit UI runs remediation commands outside the decision gate**, with any
  binary. → the panel runs only kubectl and helm remediation lines (`manual_apply_refusal`): no
  shell construct, no `--`, no flag that changes the cluster or identity, no `exec`, `cp`,
  `port-forward`, `run`, `debug` or helm `--post-renderer`; any other line is shown, not run.
- [x] **H18 — an approval never reaches remediation.** `log_human_decision` runs before
  `human_review`, so `human_router` always reads "reject". → the decision is logged after the
  interrupt; test: resume with `approve` reaches `remediation`, `reject` does not.
- [x] **H19 — AUTO accepts an irreversible change.** One inverse among N commands marks the whole
  set reversible, and `apply -f X` is "rolled back" with `delete -f X`. → every command needs its
  own inverse; `apply` has none we can compute; `delete` of a PVC, namespace or StatefulSet is
  irreversible.
- [x] **H23 — Kubernetes annotations and kube-verdict's evidence share one dictionary.** An
  object annotated `drift.…` or `missing.…` by anyone produces a signal and prompt text. →
  annotations read from the cluster are kept apart (`k8s_annotations`); evidence keys are written
  only by collectors. Text from logs, events, traces and alerts is marked as untrusted data in the
  prompt.
- [x] **H35 — the OTLP receiver binds `0.0.0.0` by default, without a body limit.** → default
  `127.0.0.1`, a body limit, an optional bearer token.
- [x] **H36 — `ChartParser.from_tgz` extracts without a filter.** → `filter="data"`.
- [x] **H13 — the remediation diff shown for approval can start from empty values** when
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
- [x] **H9 — the graph is cached per thread id, never evicted.** The API uses the session id, so
  a second investigation on a session (or a `/feedback` re-run) diagnoses the previous snapshot.
  → the cache lives for one run only.
- [x] **H10 — the rendered-manifest anchors never run.** `gitops_node` stores the Git provider in
  `config`, which LangGraph does not pass to the next node. → the provider is rebuilt from the
  state where it is needed.
- [x] **H11 — `GIT_REPOS_JSON` is read only by the UI**, and malformed JSON is ignored silently.
  → the workflow reads it; a parse error is logged.
- [x] **H40 — the API searches a vector store from an earlier run** (any cluster) and puts its
  texts in the prompt. → the cluster index is built per investigation; only the knowledge base
  (examples, docs) persists.
- [x] **H39 — resolved incidents and documents are indexed only by the UI.** → indexed by the
  workflow's `index` step for every entry point.
- [x] **H17 — three "expected state" paths; the only real render-vs-live diff runs outside the
  workflow** (MCP tools). → one path, in the workflow (with F11). Done 2026-10-20: the MCP `helm_drift` and
  `expected_state_drift` run the workflow's comparisons (`docs/three-states.md`); without an
  applied state the result is labelled `declared-vs-live`.
- [x] **H4 — the product does not use the structured diagnosis.** `rca/diagnosis.py` is called only
  by the measurement tools. → the workflow runs `extract_signals` + `diagnose`; its outcome is the
  report's diagnosis; the LLM, when enabled, words it. Spec: `docs/structured-in-product.md` (with H17, the two
  engines and H5's abstention). Done 2026-10-19 (rules 1–6): `diagnose` node, report = diagnosis,
  abstention on a failed collector, the LLM explains and its disagreement is recorded,
  `LLM_PROVIDER=none`, the gate scores the diagnosis (an abstention is reviewed), the Monte Carlo
  out of the decision; the workflow's report equals the structured solver on the 19 cases.
- [x] **Two rule engines.** The old `RemediationEngine` still injects the hypothesis into the LLM
  prompt and backs the reasoning-path "rule" step; its rules claim a precise cause on a broad clue
  (any Pending pod, any "not found" event, H22) and propose fixed commands (`demo/charts/…`, H21).
  → the structured diagnosis replaces it there. Done 2026-10-20: no hypothesis, no LOW fallback and no command
  from it in the product (`tests/unit/test_one_engine.py`); the reasoning-path rule step grades
  the structured diagnoses (P4 for h007 / h008 and P6 fixed). It stays the `rules` replay
  baseline and the Streamlit UI's bonus panel.
- [x] **H20 — the product reads the LLM's raw confidence word** (`MEDIUM:`), the measurement the
  normalised one. → one normaliser for both.

## 2. Real evidence

- [x] **F11 — on a live cluster the Helm detector sees no memory, image or probe drift**; those
  drifts (h002, h003, h009, h024) are made by the test loader. → the GitOps render-vs-live path
  (chart rendered locally, F9 already reads the live containers) is wired into the workflow and
  produces `drift.config` with its provenance; the loader stops making `drift.helm.*`.
- [x] **H28 — the render-vs-live drift is written as `gitops.*`, which neither the prompt nor the
  signals read.** → part of F11: the signals read it.
- [x] **H32 — `helm template` gets the values as `--set`**: lists are dropped, commas split a
  value, strings become numbers. → a values file.
- [x] **H30 — with a GitHub URL the GitOps path never reads Git** (`GithubProvider` has no local
  checkout). → GitHub repositories are cloned like any other.
- [x] **H29 — rendered objects without a namespace match a live object in any namespace.** → the
  release namespace is used.
- [x] **H26 — every `managed-by=Helm` object is linked to every release of its namespace**: two
  healthy releases make two false replica drifts. → linked by `meta.helm.sh/release-name` and the
  `app.kubernetes.io/instance` label only. Likely cause of **D2.1** (h016).
- [x] **H25 — a pod is tied to its workload by name prefix.** → owner references
  (Pod → ReplicaSet → Deployment) are linked and used.
- [x] **H27 — Helmfile CLI mode returns no release** although it logs a fallback. → the fallback
  parses the YAML.
- [x] **h012 end to end** in the replay through that path, nothing injected: the loader runs the
  production `ManifestDiffer` on the case's rendered chart.
- [x] **H48 — the GitOps render uses the deployed values**, so a values change against Git is
  never a drift (only an edit made outside Helm is). → spec `docs/three-states.md`: declared
  (Git) → applied (`helm get manifest`) → live, two comparisons with their provenance, no mode
  to choose; `GITOPS_VALUES_SOURCE` (an interim switch) is removed. Done: replay 11/19 → 15/19,
  h002 / h003 / h009 / h024 / h012 found by A with their provenance (`docs/case-audit-001.md`
  § Correction F11 / H48). h002, h003, h009, h024 (their
  drifts were made by the loader) are strict xfails naming F11/H48.
- [x] **H1 — `env.*` is never written in production**: the GitOps env diff is blind live, and the
  database checks built from `env.DB_HOST` never appear. → read env from the live containers.
  Half done with H48: the differ reads env from the live containers. Open: `RemediationEngine`'s
  database checks still read an `env.DB_HOST` annotation no collector writes (two engines, H4).
  Closed 2026-10-20: the only reader of `env.*` left is the old `RemediationEngine`, out of the
  product since the one-engine change (`tests/unit/test_one_engine.py`).
- [x] **H5 — collection failures read as absence.** `ingestion/helm_collector.py:203`,
  `ingestion/git_provider.py:114,120`, the K8s collector's listings other than ConfigMaps / PVCs /
  ServiceAccounts (H24), metrics-server, and the discovery's refused groups and CRDs (logged at
  debug only) return empty. → each reports `fallback: true` like Prometheus/Loki/OTel, and a
  diagnosis does not abstain as `no_incident` when a collector that could carry evidence failed.
- [x] **H33 — an alert that matches no entity is dropped silently.** → kept as cluster-level
  evidence and counted.
- [x] **H34 — the Loki query assumes the OTel Collector's labels** and keeps the last 20 lines of
  any level. → labels are configurable; error lines first.
- [x] **H37 — the graph walk from a sick pod reaches almost nothing** (outgoing edges only;
  quotas, logs, traces, alerts and scaling not in the incident relations). → the walk follows the
  incident relations both ways.
- [x] **H38 — an old `Failed` pod (Evicted, failed Job) is a seed**, and a Deployment scaling up
  is "Unhealthy". → terminated pods older than the window are not seeds; the rollout guard applies
  to Deployments.
- [x] **H43 — synthetic PatchTST series built from the current state enter the prompt as
  anomalies, recorded as `mode: real`.** → synthetic series are labelled, never sent to the
  prompt or the decision.

## 3. Measurement

- [x] **H14 — the replay never runs the production collectors**; the loader links every object to
  the case's release, builds `QUOTA_BLOCKS`, reads PolicyReports its own way, makes `drift.helm.*`
  and passes RoleBinding / NetworkPolicy lists production passes as `None`. → the loader builds
  what the collectors build, from the same code; the gaps that remain are listed per case. Done 2026-10-20: the gaps
  left are listed in `docs/test-cases.md` § Live parity; replay 15/19 unchanged.
- [x] **R1 — a replay child that dies leaves no trace** (`_run_in_child` ignores the exit code).
  → the failure is printed and appended to `failures.jsonl`; `--resume` retries it. Done
  2026-10-20: exit code, timeout and a missing record are printed and kept in `failures.jsonl`.
- [x] **H7 — rates are computed on recorded runs, not expected ones.** → `REPORT.md` counts
  expected runs; a missing run is a FAIL. Done 2026-10-20: a run expected and not recorded is
  `MISSING`, counted as a FAIL.
- [x] **Prompts are not recorded** — what the model read cannot be checked afterwards. → each run
  stores its prompt. Done 2026-10-20: each LLM run stores its prompts (`prompts`).
- [x] **A4 — "not present" is not accepted for "missing"** (found reading the 2026-10-17 mistral
  answers, h004). → dated amendment to `tests/veracity/synonyms.yaml`, re-grade. Done 2026-10-20:
  h004 0/3 → 3/3 on the 2026-10-17 runs, nothing else moves.

- [ ] **H79 — rules fixed before any run that no code applies** (read-only pass over
  `docs/veracity-replay.md` § Comparison and `docs/protocol-001-retrieval.md`, 2026-10-23). Each
  is written; none is enforced or recorded:
  - the collector-cut control (defined by `docs/veracity-replay.md` § Amendment 2026-10-23):
    no cut for the `raw:` and `context:` solvers, no `--cut` in the replay;
  - the hashes of `tools/veracity.py` and `tests/veracity/synonyms.yaml` in every run;
  - the model's digest (only its name is recorded);
  - a run that read fewer tokens than it was sent is **invalid**, not a FAIL: the refusal works
    from an estimate, and a prompt the estimate undercounts is truncated by Ollama without an
    error. Detected when the tokens read plus `num_predict` reach `num_ctx`;
  - a run that reads or writes a store outside its temporary directories is invalid: the
    stores are pointed there, but nothing checks it after the run;
  - each record names its configuration (C0–C3), store, examples setting and corpus hash;
  - records go to `<model>-prompt_<v>-store_<store>-examples_<on|off>/`; `--out` is free today;
  - protocol 001's results split into cases whose family the corpus covers and cases it does
    not;
  - an empty answer is a FAIL (it is, through `grade("")`, but no test says so).
  → each enforced in the replay or in `tools/compare_solvers.py`, with a test that fails without
  it, before the first run.

## 4. Cases and claims

- [x] **D2.1 — h016's replica drift is attached to the wrong Deployment** (catalog-api, the decoy).
  → release-to-workload matching fixed (H26).
- [x] **Fixtures from the audit:** h002 and h010 (two causes each), h005 (RBAC denial as an Event
  Kubernetes never emits), h006 (a kubelet message that names the cause); **h011** retired. Done 2026-10-20
  (`docs/case-audit-001.md` § Corrections 2 and 3): h005, h006, h010 corrected, h002 kept with
  the reason, h011 retired; structured replay 13/18.
- [x] **H50 — klog error lines were read as `info`** and dropped by the signals (found correcting
  h005). → the klog severity letter is read (`tests/unit/test_klog_level.py`).
- [x] **H49 — an application whose pod is Ready but whose work fails is invisible**: logs are
  read only for failing or not-ready pods, so h005 reads `no_incident`. To decide: read the logs
  of the workload the query names, or of every pod of the namespace within a budget.
  Done 2026-10-21 (option chosen: the workload the query names, `docs/failing-cases-002.md` S1).
- [x] **F10 — `make demo` prints fixed verdict lines** and computes its own diff. → it prints what
  the diagnosis computes. Done 2026-10-20: `demo/real_run.py` runs the
  workflow (`LLM_PROVIDER=none`) and prints its state only.
- [x] **H8 — public claims outside the README**: `SKILL.md` (twice) says "air-gapped by default";
  the plugin descriptions say the live cluster is diffed; `SECURITY.md` says no data leaves the
  machine (remote LLM providers, the embedding download and the GitHub API do); the system prompt
  says "air-gapped" whatever the provider; `docs/configuration.md` says the database can be a
  Postgres URL (it is always SQLite). → aligned with what the code does. Done 2026-10-20 (README
  too): what leaves the machine is stated per provider; `tests/unit/test_public_claims.py`.
- [x] **H6 — tests that cannot fail**: `test_hybrid_pipeline_005.py::test_any_hypothesis_fires`
  (prints only), `test_workflow.py:1007` and `test_workflow_nodes_coverage.py:372` (accept any
  result), the human-review tests (assert the decision, not the path, H18), the anchor tests (call
  nodes by hand with one shared config, H10). → real assertions.
- [x] **Dead code**: `VECTOR_STORE_TYPE` is read nowhere; `reasoning/beam_search.py`'s selection
  functions have no caller and its NO_GO condition needs 3 path switches where at most 2 exist. →
  removed.

## 6. Remaining findings of the file-by-file pass (2026-10-21)

Items the pass recorded as minor or suspect; each suspect was checked before being listed.

- [x] **H51 — a remediation line can read a Secret through the UI's manual panel.** The panel
  runs `kubectl get` (H46), and the Secret filter `_SECRET_READ` only matches
  `get secret … -o yaml`, not `get -o yaml secret …`. → `get` / `describe` of Secrets refused in
  the panel; the filter matches either order.
  *Done: a `get` of Secrets with an output that prints their data (`-o yaml|json|jsonpath|
  template`), in any argument order, is refused in the panel and filtered from the checks;
  `describe secret` (sizes only, no values) and `get secrets` (names) stay allowed.*
- [x] **H52 — a metrics-server failure reads as "no metrics"** (warning, `fallback: false`; H5
  family). → `last_error` and `fallback: true`.
  *Done: a refused or failed listing sets `last_error`; the node reports `fallback: true`
  with it. A cluster that returns no pod metrics is not a failure.*
- [x] **H53 — every webhook alert starts a full investigation, with no limit.** → at most one
  running investigation per (alert name, namespace), and a global cap.
  *Done: one running investigation per alert identity (alert name + namespaces; signal: entity +
  metric), `WEBHOOK_MAX_RUNNING` (4) in all; a skipped alert counts in `skipped`.*
- [x] **H54 — the UI renders edge-log reasons as HTML**; some carry model-produced text (a
  namespace taken from a command, a dry-run command). → escaped.
- [x] **H55 — the prompt still tells the model to answer HIGH from the pre-LLM score**, a
  label nothing decides on since the structured diagnosis. → the instruction is removed.
  *Done: prompt `v5`; runs recorded with `v4` are not compared with `v5` runs.*
- [x] **H56 — `helm` calls have no timeout**: a hung `helm` blocks the ingest. → a timeout,
  reported as a failure.
  *Done: helm 60 s, git 120 s, `helmfile version` 30 s; a timeout is a recorded failure; a
  timed-out pull keeps the checkout and says it may be stale.*
- [x] **H57 — every field of every discovered object (CRDs) is sent to the LLM**, a remote one
  included. → only kind, name, namespace, labels and status conditions.
  *Done: kind, apiVersion, name, namespace, labels, `status.phase` and each condition's
  type / status / reason; no spec, no condition message.*
- [x] **H58 — two Git repositories with the same name share one clone directory.** → the
  directory is derived from the whole URL.
  *Done: `<name>-<sha256(url#branch)[:12]>`; existing clones are cloned again once.*
- [x] **H59 — `_parse_chart_name` cuts at the last `-`** (`app-1.2.3-rc1` → `app-1.2.3`). →
  semver-aware split.
- [x] **H60 — `policy_fix_hints` proposes `kubectl describe clusterpolicy X | kyverno test .`**,
  a command that does not do what it says. → read-only checks only.
  *Done: `describe clusterpolicy` + `get policyreport`; the UI's two Kyverno panels lose the
  local-test line and a broken `kubectl annotate` write.*
- [x] **H61 — the schema anchors declare `imagePullPolicy: IfNotPresent` for every image**;
  Kubernetes defaults to `Always` for `:latest` or no tag. → the default follows the tag.
  *Done for Pods (the only kind whose schema has container fields): one anchor per container.*
- [x] **H62 — `OllamaClient.stream_generate` sends no temperature** (Ollama's default, ~0.8)
  where `generate` sends 0.1. → the same temperature.
- [x] **H63 — `session_store` rewrites `os.environ["KUBEWHISPERER_DB"]`** (a global side
  effect). → the path is passed, not written to the environment.
- [x] **H64 — the raw manifests keep the database on the container's filesystem and use
  `:latest` images.** → the database on the volume; pinned images.
  *Done for kube-verdict (`v0.1.4`, the chart defaults to `v<appVersion>`; `KUBEWHISPERER_DB`
  on `/data` in the chart too, where it was missing as well). `ollama/ollama` and
  `curlimages/curl` stay `:latest` until a version is checked in the live run.*
- [x] **H65 — with no seed, the context is every entity of the cluster.** → no seed, no
  context expansion (the abstention says so).
  *Done: no seed and no retrieval hit → no expansion (it was the whole cluster). Nothing is
  added to the report: the diagnosis already abstains from the signals.*
- [x] **P7 — a generic `config.drift` ranks above the specific `oom.memory_drift`** on h003 /
  h012. → a drift used by a more specific rule is not reported again on its own.
  *Done: the cause was not the drift but the failure — the OOM, explained by
  `oom.memory_drift`, strengthened another drift of the same Deployment (h003
  `requests.memory`, h012 `spec.replicas`) to 0.65. A failure a specific rule explains no
  longer strengthens a generic drift (`rules/5`). Structured replay 19/19, h009's
  `config.drift` (0.8, its failure unexplained) unchanged. h024's confidence goes HIGH → MEDIUM:
  its HIGH was the same double count (a generic drift at 0.8 above both real causes).*
- [x] **H66 — the report carried a second verdict.** The LLM report's `decision` re-ran the
  gate on the pre-LLM context score; on h013, once H65 stopped feeding it the whole cluster, it
  read `NO_GO` (0.42) beside the workflow's `HUMAN_REVIEW`. Nothing read it, but the API returned
  it. → dropped from the report; the verdict is the policy gate's, on the diagnosis (rule 6).
  Found by the frozen journeys h013–h015, regenerated (their context loses the 3 entities the
  no-seed fallback used to add).
- Latent, recorded: `ChartStore._chart_dir` does not sanitise names before `rmtree` (no
  production caller pushes); Kyverno's newer report API group — to check during the live run.

## 7. Modules not read before (2026-10-21)

A read of the modules the file-by-file pass had not covered (signals, `PatchBuilder`, enterprise
docs, the API models and mappers, the rest of the UI, scripts, terraform, the dashboard). Each
item says how it was checked; the fix comes with a test that fails on today's code.

- [x] **H67 — the PatchTST detector calls every normal series anomalous** (measured). The score
  divides the error on the last window by the error on training windows the model has just fit:
  white noise scores ~47 (`critical`), a clean sine ~2.7 (`warning`). With Prometheus enabled,
  every pod's text carries `signal.anomaly=…critical` into the LLM context and the index. → a
  held-out baseline (windows not trained on), a fixed seed; test: normal series (noise, sine,
  constant) → `normal`, a final spike → anomalous.
  *Done: the worst recent forecast (half a context) against the worst held-out one, never
  trained on; normalised on history only; a fixed seed. Rule fixed first, three designs (in the
  test): normal ≤ 1.47, spike ≥ 18.1; on unseen data seeds normal ≤ 1.57, spike ≥ 18.2. PatchTST
  now needs 128 points: the short and medium horizons use the z-score.*
- [x] **H68 — the Prometheus source hides failures and drops series** (measured). A failed query
  reads as "no data" (logged at debug, no `fallback`); series of different lengths fail
  `np.vstack` and the whole metric is dropped; memory is averaged across containers instead of
  summed. A 7-day detection costs ~6.6 s, run on every pod of the graph with no cap. → a
  reported failure, aligned series summed per pod, the analysis limited to seeds with a budget,
  no training on synthetic series whose results are discarded (H43).
  *Done: failed queries recorded and reported as `fallback`; series aligned on timestamps,
  summed per pod (averaged for a ratio), `container!=""`; the seeds only, `SIGNAL_MAX_ENTITIES`
  (10), the rest counted; the workflow no longer trains on synthetic series (the UI keeps them).
  Structured replay 0.1 s per case instead of 1.5 s.*
- [x] **H69 — `PatchBuilder` misreads `--set`** (measured against `helm`). Two releases are merged
  into one patch, `1.10` becomes `1.1` (Helm keeps the string), `--set-string` is coerced,
  `a=1,b=2` and list indexes are not parsed. No production caller yet. → Helm's typing (integers
  and booleans only), one patch per release, the comma and index forms, or the module removed
  until it is wired.
  *Decided 2026-10-22: fixed, not removed (phase 1 of `docs/pr-mr-first.md`, kept for the GitOps
  path).*
  *Done: Helm's typing (integers, booleans, null), `--set-string`, unescaped commas, list
  indexes, `--set=`, one release per patch, a scalar replaced by a mapping reported in
  `warnings`; checked against `helm template` on five flag sets. Correction to the diagnosis:
  PatchBuilder does have a production caller, the MCP tool `propose_patch` (which can open a
  draft PR).*
- [x] **H70 — enterprise documents reach only the UI's own pipeline**; the vector-store
  persistence (`persist_texts`, `rebuild_from_db`, `vector_store_docs`) has no production caller
  and a test mocks it as if `index_node` called it. → documented as UI-only, or indexed by the
  workflow; the dead persistence removed and the mock with it.
  *Decided 2026-10-22: documented as UI-only; the dead persistence and its mock removed. Indexing
  enterprise documents in the workflow would be a new capability, not a correction.*
  *Done for the persistence: `persist_texts`, `rebuild_from_db`, `persistence/vector_store_repo.py`
  and the `vector_store_docs` table removed, with the mocks that simulated them. Retracted: the
  first half of the finding was wrong — since H39, `index_node` indexes the enterprise documents
  (`DocIndexer(...).index_all(DocStore())`) in every investigation, whatever the door; only
  adding one goes through the UI. Nothing is documented as "UI-only".*
- [x] **H71 — a webhook alert's text becomes the incident query**, in the operator's question
  slot rather than as alert text; an alert without a namespace label investigates every
  namespace. The H53 key (alert name + namespaces) merges two resources' alerts in one
  namespace. → the alert text passed as quoted alert evidence, the query built from labels; no
  namespace → refused or the configured default; the key's choice written down.
  *Decided 2026-10-22: no namespace label → the configured `KUBE_NAMESPACES`; when that is empty,
  the alert is refused and counted in `skipped`.*
  *Done: the query holds sanitized labels only; the summary and description go to a quoted
  "Alert text" section of the prompt (v6). No namespace → `KUBE_NAMESPACES` (a signal first
  reads its `Kind/namespace/name` entity), refused when empty. The key is (alert name, resource,
  namespaces).*
- [x] **H72 — the verdict envelope and the sample journey describe the previous pipeline**: the
  envelope's docstring gives the old label → score map and it has no `outcome`, no
  `llm_disagreement`; for an abstention its next step speaks of "applying the proposed
  remediation". `api/sample_journey.py` is hand-written and shows hypothesis retries on the
  LLM's label. → the envelope carries the outcome and the disagreement; the sample is a
  generated journey.
  *Done: the envelope carries `outcome`, the ranked `diagnoses`, `failed_collectors` and
  `llm_disagreement`; an abstention's next step proposes no remediation; `confidence_score` is
  documented as the gate's score (the top diagnosis's). `POST /sessions/sample` serves
  `api/sample_state.json`, generated from an offline h014 run by `tools/freeze_journey_fixtures.py`
  and checked against the code.*
- [x] **H73 — an API caller can choose the kubeconfig file and the context** (`RunRequest`), on
  an API without authentication by default. → the fields refused by the API (server
  configuration only), or an allow-list of contexts.
  *Decided 2026-10-22: the fields are refused (422); the kubeconfig and the context are server
  configuration only.*
  *Done: `RunRequest` and `InvestigateRequest` refuse both fields (422); the routes no longer
  put them in the state, so the workflow reads `KUBECONFIG` / `KUBE_CONTEXT`.*
- [x] **H74 — the UI's "Fetch from URL" reads any URL scheme and address** (checked:
  `file://` is read). → `http(s)` only, private and link-local addresses refused, the token sent
  only to the host it was entered for.
  *Done: `knowledge/url_guard.py` — http(s) only, every resolved address must be global, each
  redirect checked again, the token dropped on a redirect to another host. Not covered: a DNS
  answer that changes between the check and the fetch.*
- [x] **H75 — the UI can rewrite a graded case's expectations from a run's output**
  (`update_expect_from_sim`, `recalibrate_all` on native cases). → limited to synthetic cases;
  graded expectations change only through the case audit.
  *Done: `update_expect_from_sim` refuses a case under `tests/integration/cases/`,
  `recalibrate_all` skips it, the UI hides the button. The old code proposed three changes on
  h003, `confidence: MEDIUM → HIGH` among them.*
- [x] **H76 — tooling**: `scripts/init-k3s.sh --image` is silently ignored (its `sed` pattern no
  longer exists) and it applies the on-demand Job the manifest says to skip; `make test` runs
  the tests marked as needing Ollama and a cluster; the Scaleway terraform does not pin the
  Kubernetes version. → each fixed.
  *Done: `init-k3s.sh` parses `--image` and refuses an unknown argument, replaces the
  manifest's kube-verdict image (and stops if there is none), leaves out the on-demand Job, and
  has a render-only `--print-manifest`; its test runs with a PATH that has no curl, kubectl or
  k3s. `make test` uses the CI's selection. The Scaleway version is a required variable (not
  checked with terraform: not installed here).*
- [x] **H77 — the dashboard presents the structured diagnoses as the former engine's
  hypotheses** and shows neither an abstention nor the LLM's disagreement. → the ranked
  diagnoses, the outcome and the disagreement shown as such.
  *Done: a "Structured diagnosis" panel (outcome, collectors that could not be read, ranked
  diagnoses with their rule, the LLM's disagreement and its explanation, marked as not deciding)
  replaces the panel that called them RemediationEngine rule hits.*
- [x] **H78 — the lint is not reproducible**: the CI installs the latest ruff, and the checks
  run before a commit were the tests alone, so the lint failed on the PR at 70 commits that
  pass every test. → the CI's ruff pinned, one configuration for the CI and `make lint`.
  *Done: `ruff==0.17.2` in the CI and in the dev dependencies; the CI runs `ruff check .` with
  the rules of `pyproject.toml`; `make ci` runs the lint, then the CI's test selection. The 38
  findings are fixed in one commit at the end of the PR (statements split, unused imports
  removed), with no change to the tests' ASTs where only a statement was split.*
- Retracted: `ontology/version.py` is not dead code — `K8sCollector` calls `detect_version`
  and every flag has a production caller; only its docstring contradicted the fallback (fixed).
  Previously recorded as: dead code, `ontology/version.py` (no caller). Latent: `git clone` receives the
  repository URL without `--` (an option-shaped URL is parsed as an option; tried, nothing ran).
  → `--` and an URL that starts with `-` refused.
  *Done for `git clone`: `--` before the URL, an URL starting with `-` refused before git runs.*

## 5. Live validation (once, at the end)

- [ ] **H45 — the project's chart grants no Secrets**, where Helm 3 stores its releases, nor
  quotas, limit ranges, webhooks or impersonation: deployed with it, kube-verdict sees no release.
  → to confirm live, then the chart grants what the collectors read and the docs say what
  dry-run needs.
  *Corrected 2026-10-23: the fix first written here — a Role on "Secrets of type
  `helm.sh/release.v1` only" — cannot exist. RBAC selects by resource and by name, never by a
  field such as `type`; Helm names its Secrets `sh.helm.release.v1.<release>.v<revision>`, a new
  name at each revision, so `resourceNames` cannot hold them either. Any Role that lets
  `helm get manifest` read a release lets the account read every Secret of that namespace.*
  *Decided 2026-10-23 (option A): opt-in, per namespace, off by default. A chart value lists
  the namespaces whose releases kube-verdict may read; for each, a Role grants `get` / `list` on
  `secrets`, and the chart's documentation says that it covers every Secret of the namespace,
  not only Helm's. With the list empty — the default — no Secret is readable and the applied
  state is reported as not read (`fallback: true`), never as absent. Rejected: B, no in-cluster
  release at all (the three-state comparison would never run live); C, a ClusterRole on
  `secrets` (every Secret of the cluster).*

- [ ] **H80 — steps of the live run written nowhere but in the protocols.** → listed in the
  live-run spec, in this order, each checked before the first measured run: Ollama alone on the
  cluster, the hourly CronJob suspended (and resumed after, both with an explicit go); h016's
  prompt measured with each model's tokenizer under the 16 384-token ceiling
  (`docs/veracity-replay.md` § Amendment 2026-10-22); the models' digests recorded; protocol
  001's positive control h001 re-run once when it fails, then recorded invalid.

## Not in this PR (open work, not corrections)

HPAs, ResourceQuotas, LimitRanges, NetworkPolicies and RoleBindings collected live as typed objects
(needed for the HPA case of F9, F6 and h005/h006 live); Monte Carlo out of the decision path; a
dependency lockfile.

## Measurement before the merge

The comparison none / LLM / sLLM and its decision rules are fixed in
`docs/veracity-replay.md` § Comparison (2026-10-20), before any run.

Block B, on the head of this PR before it is merged (the commit is recorded in every run):
rules, structured, mistral × 3, qwen2.5:3b × 3, the 19 native cases; a block counts only when
complete (`docs/veracity-replay.md`).
Amended 2026-10-22 (`docs/veracity-replay.md`, § Amendment 2026-10-22): the block also runs the
model on the raw artifacts (raw:mistral × 3, raw:qwen2.5:3b × 3) and on the pipeline's context
without the diagnosis (context:mistral × 3, context:qwen2.5:3b × 3), with their decision rules.
