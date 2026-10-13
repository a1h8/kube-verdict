# Vitrine and live — one real session, shown and graded

## Where it is

- **Vitrine** (`docs/vitrine.md`, spec 2026-10-04): route `#/vitrine`, 4 steps (alert →
  reasoning → verdict → service health). 0/6 acceptance criteria. Its fixtures exist
  (`dashboard/src/sampleJourneys/h013–h015.json`, `source: "fixture-replay"`), generated
  from the offline workflow with the scripted LLM.
- **Live**: the local cluster (`rancher-desktop`, namespace `kubeverdict-obs`,
  `docs/local-observability-stack.md`) already runs Prometheus, Loki, Tempo, Alloy and
  Grafana, and kube-verdict and PatchTST are deployed there. `tools/b13_capture.py`
  applies a scenario, waits for the real failure, investigates and grades it. The last
  capture is from 2026-09-26.
- **Decision of 2026-10-04**: no piecemeal live sessions, only one live validation at
  the end. It was right against one-off captures that block offline work. It has
  become a reason never to run live.

## What a vitrine fixture shows today

It shows the plumbing, with a scripted answer. A pitch audience reading it sees a
correct diagnosis that no model produced. The `fixture-replay` label is honest, but
the vitrine must not stop there.

## Proposal

1. **Build the vitrine on the fixtures** (`docs/vitrine.md` as specified): the route,
   the 4 steps, the source label, the component test. Offline, no infra.
2. **Replace the scripted answer with a real one, offline**: once
   `veracity.md`'s replay harness exists, the frozen journeys can come from the real
   model's run instead of `ScriptedLLM`, with `source: "fixture-replay:<model>"` and
   their veracity grade shown. A wrong answer is shown as wrong.
3. **One live session, local, end to end**, not per case. One misdirection case (M1
   from `scenarios.md`, or h014 first because its manifest exists) applied on the local
   cluster. kube-verdict investigates with the real backends and the real model, the
   session is graded by `grade()`, and the vitrine narrates **that** session id with
   `source: "live"`, linked to the Monitoring Ops dashboard for the same time window.
   This is the single live validation, done locally first. It reuses the same tooling
   GCP and Scaleway will run.
4. **Then cloud**: the same session, the same grading, on GKE Autopilot and Kapsule
   (`docs/cloud-prerequisites.md` §5), with Terraform already in `terraform/`.

## Gate

- [ ] `#/vitrine` built on the fixtures (the 6 criteria of `docs/vitrine.md`)
- [ ] Vitrine journeys generated from a real-model replay, grade shown
- [ ] One local live session: applied, investigated, graded, narrated in the vitrine
- [ ] The same on GCP, then Scaleway

## Next step

Build the vitrine route (item 1). It is independent of the veracity harness, so it can
go in parallel. Item 3 waits for the first veracity report, so that the live session is
not the first time anyone sees the model's answer.
