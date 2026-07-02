# PR/MR-first remediation (design)

*Status: **design / target**, not implemented. This document defines the flow and the concrete
integration points before any code lands. Sections marked **[exists]** reuse current bricks;
sections marked **[new]** are the work to build.*

## Why

Today KubeVerdict proposes remediation as **break-glass commands** — a list of
`helm upgrade --set ...` / `kubectl ...` strings on `IncidentReport.remediation`
(`decision/models.py`), gated by human approval and a heuristic blast-radius score. That is
imperative and bypasses the source of truth: in a GitOps shop the cluster is reconciled from git,
so a `kubectl`/`helm` fix applied directly is drift the controller will fight or overwrite.

**PR/MR-first** turns the verdict into a *reviewable git change* instead of a live command:

```
IncidentReport (verdict)
      ↓  [new] PatchBuilder
values.yaml patch (declared change that fixes the drift)
      ↓  [new] GitProvider.propose_change
branch + commit + PR/MR (draft)
      ↓  CI on the MR: render → diff → policy   [exists: ManifestRenderer / ManifestDiffer]
human review + merge   ← the approval gate moves into the PR
      ↓
ArgoCD / Flux reconciles the merged desired state
```

This is the differentiator vs auto-remediation tools (Kubernaut) and RCA-only tools (KubeRCA): the
fix is an auditable artifact, reviewed where infra changes are already reviewed, and rolled back the
GitOps way (revert the merge) — never an out-of-band mutation.

## Flow

### 1. Verdict → structured patch  **[new] `remediation/patch_builder.py`**

Input: `IncidentReport` (`remediation`, `affected`, `root_cause`) plus the anchor drift already on
the graph (`gitops.*` / `anchor.*` annotations).

- Parse each `helm upgrade --set <key>=<value>` in `remediation` into `{helm_key: value}`.
- Reuse **[exists]** `_field_path_to_helm_key` (`rca/context_builder.py`) to map anchor field paths
  (`container.api.resources.limits.memory`) to Helm value keys (`resources.limits.memory`), so a
  drift anchor becomes a values change even when no `--set` string was produced.
- Resolve the target release → chart/values file in the GitOps repo (the same
  `name@version` mapping used by the ChartStore / expected-state source).
- Load the current `values.yaml` **[exists]** via `GitProvider.get_file(path)`, apply the keyed
  changes, and emit a **unified diff** + the new file content.

Output: a `PatchProposal` **[new]** dataclass:
`{release, namespace, repo_url, branch_base, file_path, diff, new_content, source_report}`.

This phase is **offline-testable** on the `hNNN` cases (values.yaml in → patched values.yaml +
diff out) with no network — the first milestone, mirrors how h012 is validated.

### 2. Patch → PR/MR draft  **[new] extend `ingestion/git_provider.py`**

`GitProvider` is read-only today (`get_file`, `list_files`, `local_path`). Add a write path:

```
GitProvider.propose_change(patch: PatchProposal, *, draft=True) -> ProposedChange
```

- `LocalGitProvider`: create branch `kubeverdict/fix-<release>-<short-hash>`, write the file, commit
  with a message built from the verdict (root cause + confidence + evidence links), push.
- `GithubProvider`: use the existing token to create the branch + commit via the REST API (or `gh`),
  then open a **draft** PR. Body = verdict summary, causal chain, render-vs-live drift table, blast
  radius, and the rollback note.
- Return `ProposedChange {url, branch, provider}`.

**No auto-merge.** The draft PR *is* the human gate — KubeVerdict never merges its own proposal.

### 3. CI on the MR: render → diff → policy  **[exists, wire]**

The MR triggers the repo's CI, which should run KubeVerdict's own bricks against the *proposed*
state:

- **[exists]** `ManifestRenderer` renders the branch's chart/values.
- **[exists]** `ManifestDiffer` diffs rendered-proposed vs live (or vs the pre-fix render) → shows
  exactly which objects change. **This is also the real, non-heuristic blast radius** the current
  `BlastRadius` docstring flags as future work (`decision/models.py`).
- Policy gate (OPA/Kyverno `PolicyReport`) on the rendered proposed manifests.

A GitHub Action template ships under `.github/` (example) so a consuming repo can adopt it.

### 4. Merge → GitOps applies; rollback = revert

Human merges the PR; ArgoCD/Flux reconciles. Rollback is `git revert` of the merge commit (a new
PR), not `helm rollback` — the `RollbackPlan` **[exists]** gains a `git_revert` strategy alongside
the current `helm_rollback` / `rollout_undo`.

## Data model & surface changes

- **[new]** `PatchProposal`, `ProposedChange` in `remediation/`.
- **[exists→extend]** `RollbackPlan.strategy` adds `git_revert`.
- **[exists→extend]** `VerdictEnvelope` (`api/verdict_contract.py`) gains an optional
  `pull_request: {url, state}` so portal/agent consumers see the proposed change.
- **[new]** MCP tool `propose_patch(session_id | report)` and REST `POST /api/v1/investigate` option
  `open_pr=true`, routing through the same investigation service (no parallel path — matches B12).

## Scope

**In scope (phase 1):** Helm `values.yaml` patch generation from a verdict + offline diff, tested on
`hNNN`. **Phase 2:** GitHub draft-PR opening via the existing `GithubProvider` token. **Phase 3:** CI
template (render/diff/policy) + `VerdictEnvelope.pull_request`.

**Out of scope (now):** GitLab MR (add a `GitlabProvider` behind the same `GitProvider` interface
later — `make_provider` already dispatches by URL); Kustomize/Jsonnet patch synthesis (start with
Helm values, the validated path); auto-merge (never — human gate is the point).

## Open questions

- Provider first: GitHub only (token + REST already present) — GitLab later behind the same ABC.
- Where the release→values-file mapping is authoritative (ChartStore vs `GITOPS_REPO_URL` layout).
- Commit identity/signing for machine-authored PRs.

## Reuses

`ingestion/git_provider.py` · `ingestion/manifest_renderer.py` · `ingestion/manifest_differ.py` ·
`rca/context_builder.py` (`_field_path_to_helm_key`) · `decision/models.py`
(`IncidentReport`, `RollbackPlan`, `BlastRadius`) · `api/verdict_contract.py` (`VerdictEnvelope`).
See also [anchor-by-render.md](anchor-by-render.md).
