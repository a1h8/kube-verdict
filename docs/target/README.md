# Target — where kube-verdict is going, and an honest read of where it is

> Written 2026-10-12, after a request to stop and diagnose the trajectory: "on tourne
> en rond sans réel progrès". This folder is the reference for what counts as progress.
> Each file is one axis; each axis has a measurable gate.

| File | Axis |
|---|---|
| [trajectory.md](trajectory.md) | What the last month produced, and why it feels circular |
| [veracity.md](veracity.md) | What we actually measure vs what we claim; the loop that is missing |
| [scenarios.md](scenarios.md) | One scenario bank, > 30 cases, and the "looks like X, is Y" family |
| [patchtst.md](patchtst.md) | What PatchTST really contributes today, and what to do |
| [vitrine-live.md](vitrine-live.md) | The vitrine demo, and getting one real live session into it |
| [knowledge-sources.md](knowledge-sources.md) | Public vs enterprise knowledge, and what the sLLM corpus may contain |

## The target, in one paragraph

On a real cluster (local first, then GCP and Scaleway), an incident fires whose cause is
**not** where the symptom is. kube-verdict collects Kubernetes, Helm, Prometheus, Loki,
OTel and PatchTST evidence, names the **right** cause with the evidence that supports it,
says "I don't know" when the evidence is not there, proposes a fix with a rollback, and
stops at a human gate. The vitrine shows that session — live, not a fixture — and a
veracity number, measured against a real model on a scenario corpus big enough to mean
something, says how often this works.

## Where we are against it (2026-10-12)

| Gate | Today |
|---|---|
| Veracity of a real model on the corpus | **23/54 (43 %)** offline, mistral, no retrieval (2026-10-14): A 20/36, B 3/12, C 0/6. Rules alone 4/18. Live: 0/2 (2026-09-26) |
| Native scenarios | 18 (h001–h016, h022, h024) — 47 directories exist across 3 incompatible banks |
| Scenarios where the cause is not where the symptom is | 6 (h013–h016 one-signal, h022, h024) — all graded only against a scripted LLM |
| "Looks like X, is Y" cases with several side effects | 0 |
| PatchTST evidence in any test case | 0 cases; runs in synthetic mode, contributes nothing, costs 20–45 s per run |
| Vitrine | spec only, 0/6 acceptance criteria |
| Live session in the vitrine | never |
| Cloud validation gate (`cloud-prerequisites.md` §5) | 0/8 |

## Rule from now on

A change is progress when it moves one of the numbers above, or unblocks the next one.
A prerequisite fix found along the way is done when it is needed for that, not for its
own sake. Each axis file ends with its next concrete step.

## Order (proposed)

1. **Veracity loop** — replay the native cases through a real local model and publish
   the first number per tier (`veracity.md`). Everything else is judged against it.
2. **One scenario bank** — migrate the legacy cases worth keeping into the native format,
   pass 30, then build the misdirection family (`scenarios.md`).
3. **PatchTST** — one case where the anomaly is the earliest or only evidence, and stop
   paying for synthetic runs that prove nothing (`patchtst.md`).
4. **Vitrine + one live session** on the local cluster that already runs the
   observability stack (`vitrine-live.md`). Cloud comes after, with the same grading.
