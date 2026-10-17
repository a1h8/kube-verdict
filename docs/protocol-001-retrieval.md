# Protocol 001 — does retrieval help the diagnosis?

> Pre-registered 2026-10-17. Status: **draft, decision rules not yet approved**. Nothing
> below may be changed after the first measured run except by a dated amendment at the
> end of this file. No threshold is set or moved after seeing a number.

## Question

The offline baseline (30/54, `docs/veracity-replay.md`) ran **without retrieval**
(`StubStore`) and with the resolved-incident memory **off**. It says nothing about the
knowledge base. This protocol measures, on the same cases, what each retrieval layer
adds, and decides whether it stays in the default path.

## Configurations

| Id | Solver | Vector search (FAISS on the investigation graph + anchors) | Resolved-incident memory (`data/examples/`) |
|---|---|---|---|
| C0 | rules (no LLM) | — | — |
| R0 | **corpus alone (no LLM)**: the root cause of the top-ranked incident record | — | **on** (corpus only) |
| C1 | LLM | off (`StubStore`) | off |
| C2 | LLM | **on** — the path `index_node` takes when no store is passed (CLI) | off |
| C3 | LLM | on | **on** |

Comparisons: C1 vs C0 (what the LLM adds), **C2 vs C1** (vector search), **C3 vs C2**
(incident memory), and **R0 vs C0**: does the incident corpus alone already point to the
cause, without the LLM? (format: `docs/incident-corpus.md`).

C1 is re-measured at the current commit: the 2026-10-14 baseline predates the
symptom-vs-drift fix (`f102422`) and is not reused.

## Fixed before the first run

- **Cases:** the native cases as corrected by `docs/case-audit-001.md` (h011 retired),
  frozen at the commit that approves this protocol. No case is added or removed during
  the protocol.
- **Runs:** 3 per case and LLM configuration; 1 for C0 (deterministic).
- **Grading:** cause concepts (`docs/case-audit-001.md` § Correction 1), `tools/veracity.py`
  and `tests/veracity/synonyms.yaml` at the commit that approves this protocol; their
  hashes are recorded in every run.
- **Model:** mistral via Ollama, digest recorded; prompt version recorded.
- **Case-level result:** a case passes a configuration when **at least 2 of its 3 runs**
  pass.
- **Denominator:** a timeout, a crash or an empty answer is a FAIL. No run is dropped.

## Controls

- **Positive control — h001 (crashloop).** Must pass (≥ 2/3) in C1, C2 and C3. If it
  does not, that configuration's run is invalid: re-run it once; a second failure is
  recorded as invalid and no conclusion is drawn from it.
- **Negative control — h022 (healthy cluster).** A configuration that makes h022 name a
  cause where the previous one did not has added a false alarm.

## Decision rules — to approve before any run

For each comparison (C2 vs C1, C3 vs C2), with *net gain* = cases that pass only with the
layer − cases that pass only without it:

| Net gain | And | Decision |
|---|---|---|
| ≥ **3** cases | no new false alarm on h022 | the layer **stays** in the default path |
| 1 or 2 | — | **inconclusive**: unchanged, flagged; decided again on ≥ 30 cases |
| ≤ 0 | — | the layer **leaves the default path** (kept in the code, off by default) |
| any | a new false alarm on h022 | the layer **leaves the default path** |

18 cases cannot show a general improvement. A "stays" means "worth keeping and measuring
further", not "proven".

## Known limit before starting: the incident memory is empty of content

On 2026-10-17 the local `data/examples/` holds 64 records, **all with the query
`pods oom`** (one incident approved 64 times), and it is not in git, so nobody else can
reproduce a run that uses it. C3 on this content would measure one repeated example.

On 2026-10-17 the cause was found: a unit test wrote two records into the real store at
every run (fixed in `f910d22`); the 64 records were moved out. So C3 and R0 need, before
they run, an **incident corpus** (`docs/incident-corpus.md`) that is:
- committed (or hashed and archived) so the run is reproducible;
- **disjoint from the 18 cases**: no record written from a run of these cases, and no
  record whose query equals a case query; otherwise the model would retrieve the answer;
- described (number of incidents, families, origin).

Until that corpus exists, C3 and R0 are recorded as **not measurable**, and no decision on the
incident memory is drawn from C1 or C2.

## Code needed before the first run

1. `tools/veracity_replay.py --store stub|faiss` and `--examples off|on`.
2. With `faiss`, the store is built in a temporary directory and the DB writes of
   `index_node` (`save()`, `persist_texts`) go to a temporary DB: a replay never writes
   to the real index or database.
3. Each run record names its configuration (`C0`–`C3`), the store, the examples setting
   and the hashes of the grading files and of the corpus.
4. Records go to `tests/veracity/replay-baselines/<model>-prompt_<v>-store_<store>-examples_<on|off>/`.

## Environment

- Ollama on `k3d-dev` (`kubeverdict/ollama`), alone: the hourly kube-verdict CronJob is
  suspended for the duration and resumed after (both with an explicit go), and no other
  replay or live session runs at the same time.
- Cost estimate: 18 cases × 3 runs × 127 s median ≈ 2 h per LLM configuration.

## Results

*(empty until the decision rules are approved and the runs are done)*

## Amendments

*(none)*
