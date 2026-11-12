# S2, how the answers are graded (protocol 002, amendment A1)

> Written 2026-11-08, before any S2 run. Nothing here changes once the first S2 run has started, except by a
> dated amendment of protocol 002.

## The graders

Two graders, each grading every answer of the run, not told which arm wrote it: two models from different
providers, or one person and one model. Each grades in a fresh session, from the pack below and nothing else.
The keyword grader (`tools/veracity.py`) is computed as well, as a secondary measure.

## What a grader receives

`tools/s2_grading_pack.py` writes one pack per run of S2: every answer of every arm (D, L₀, S₀), mixed in an order
drawn from a fixed seed, each with the case's question, its ground truth (the cause object and the mechanism, or "No
incident"), what passes and what is only partial, both taken from the case's `expect.json`. The key (which arm, case and
run wrote each item) is written to a separate file, never given to a grader.

## The instructions, given verbatim with every pack

> You are grading answers to Kubernetes incident questions. Each answer was written by an automated system; several
> systems are mixed, in random order, and you are not told which wrote what.
>
> For each item you get the question, the ground truth of the incident (the object at fault and how it causes the
> symptom, or "No incident"), what an answer must name to pass, and what earns only PARTIAL. Grade the substance only:
> different wording never costs points and matching words never earn them.
>
> - YES: it names the object at fault (or one the ground truth accepts) and the mechanism, as "Passes if" says.
> - PARTIAL: it is on the path to the cause but stops short, as "Partial if only" describes, or it names the right
>   object with a wrong mechanism.
> - NO: anything else, including naming a fault when the ground truth is "No incident", and saying "No incident" when
>   there is one.
>
> If you hesitate between two verdicts, choose the stricter. Grade each item on its own; do not guess which system wrote
> it; do not change a verdict once given. Output only a table, one row per item: | item | verdict | reason in at most
> 15 words |

## How the verdicts are combined

- **A run's verdict** is the two graders' verdict when they agree. When they disagree the run is **disputed** and goes to
  a third reading, by a grader that has not graded this run; its verdict decides. A run still disputed after that is
  reported as such and counts as not passing.
- **A case passes an arm** when at least 2 of its 3 runs are YES (D: its one run). PARTIAL is not a pass; the number of
  PARTIAL runs is reported beside it.
- **Reported:** per arm and per family, cases passed, PARTIAL, NO, disputed; the agreement of the two graders (raw
  agreement and Cohen's κ); the keyword grader's result beside the graders'. S2 is exploratory (amendment A3): no test,
  intervals only.

## What this grading cannot remove

The graders are told the ground truth we wrote, so a mistake in a case's truth is a mistake in its grading. A model
grader may prefer long or fluent answers, and may recognise the structured diagnosis by its terse style. These limits are
stated with every result.
