# Trajectory — why it feels like going in circles

Read from `git log main` (73 commits since 2026-09-12) and this branch's work, on 2026-10-12.

## What was produced

- **Evidence plumbing, offline.** h013–h016 prove that Prometheus alerts, Loki logs and
  OTel traces reach the analyze prompt through the real collectors, with decoys dropped
  by the right filter.
- **Hardening found on the way.** Collector `last_error` (an outage is no longer "0
  collected"), separate Loki / OTel stats, the trace namespace filter, `v1beta1`
  ingresses, and the diagnostics / remediation split with two fail-closed gate rules.
- **Grading.** Grading v2 (decoys, negative cases, multi-cause, expected verdict).
- **Infra and observability.** Terraform for GKE Autopilot and Kapsule, the Monitoring
  Ops dashboard, live Tempo / Loki backends on the local cluster, and the vitrine spec.

Each item is real and tested. The problem is what they add up to.

## Why it is circular

1. **The model is never measured.** Every offline run uses `ScriptedLLM`, which answers
   HIGH as soon as the case's markers are in the prompt. So every green test proves the
   plumbing, and none proves the reasoning. The only real-model numbers are the two live
   captures of 2026-09-26: **both wrong**. Nothing has re-measured since. We have hardened
   the pipe around a model whose output we have not looked at for 16 days.
2. **Each step opens a prerequisite, which opens another one.** h016 needed the trace
   namespace filter, the degraded-path test needed `last_error`, h022 needed the
   diagnostics split, and the split needed the unknown-namespace rule. Each fix is
   justified locally, but none was chosen because it moves the target.
3. **Three scenario banks that ignore each other.**
   - `cases/0NN_*` (23 synthetic `input.json` cases, tested by `tests/cases/`);
   - `cases/helm_cases/` (6 cases, IDs `h001`–`h006` that clash with the native ones);
   - `tests/integration/cases/h0NN_*` (18 native cases).

   That is 47 directories, but only 18 run through the real collectors and workflow.
   The tier-C spec of 2026-10-11 re-invented scenarios that already exist in the legacy
   bank: CoreDNS (`017_dns_failure` = planned h018), node eviction
   (`013_evicted_disk_pressure` ≈ planned h021), StatefulSet stuck
   (`019_statefulset_update_stuck` ≈ h011).
4. **Scenarios with one clean cause.** Even tier C has one fault with one decoy. Real
   incidents have one cause with *several* side effects, and each side effect points to a
   plausible wrong cause. That is exactly how the two live captures failed ("memory" for
   a certificate, "image" for etcd), and no case reproduces it.
5. **PatchTST runs but proves nothing.** It is on in every workflow run, in synthetic
   mode, and contributes no evidence to any case.
6. **The vitrine and "live" keep being deferred.** "No piecemeal live sessions" was the
   right call against one-off captures. But it turned into *no* live session at all,
   while a local cluster with the full observability stack already exists.

## What changes

- Measure first (`veracity.md`). A real local model on the native cases, a number per
  tier, re-run on every scenario added. That number replaces "tests are green" as the
  progress signal.
- Consolidate before adding (`scenarios.md`).
- Design scenarios around misdirection, not around a collector.
- Give PatchTST one case where it matters, or turn it off in tests (`patchtst.md`).
- One live session on the local cluster, graded and shown in the vitrine
  (`vitrine-live.md`), before any cloud spend.

## In-flight work, kept

The diagnostics split and fail-closed gate rules (branch `test/h022-h024-cases`) fix a
real safety issue: any LOW run could end `NO_GO`, and an unknown namespace failed open.
They are finished and kept. The structured-diagnostics plan (`docs/diagnostics.md`)
waits behind the veracity loop: it improves the output for the operator, not the
diagnosis.
