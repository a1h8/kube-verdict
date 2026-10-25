# Protocol 001 — does retrieval help the diagnosis?

> Pre-registered 2026-10-17. Status: **decision rules approved 2026-10-23**, as amended on
> 2026-10-23 (end of file); the 19 cases are frozen at the commit that records it. Nothing
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
- **Context window:** `num_ctx` large enough for the longest prompt plus the answer, recorded
  in every run with the prompt tokens Ollama actually read (`prompt_eval_count`). A run that
  read fewer tokens than it was sent is **invalid**, not a FAIL (`docs/case-audit-001.md` F8).
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

### R0 — the corpus alone (2026-10-24, offline)

Run on commit `f2289e1`, corpus v1 hash `85ba60e7…` (frozen from this run), one run per case,
no model; records: `tests/veracity/replay-baselines/retrieval-store_faiss-examples_on-2026-10-24/`.
Reported, not decided (amendment 2026-10-24, CPI-23).

| Group | Passed | Detail |
|---|---|---|
| the 9 cases the corpus covers | **0/9** | 4 answered above the threshold, all FAIL; 5 abstained |
| the 8 cases it does not cover | **0/8** | 1 answered (h012), FAIL; 7 abstained |
| negative controls h022, h026 | **2/2** | both abstained: no false alarm |

The five answers: h001 (missing Secret) ← inc-0003, `oom`, score 0.676; h008 (init container) ←
inc-0010, `helm-drift`; h010 (quota) ← inc-0004, `config-missing` — three records of another
family. h003 (OOM) ← inc-0003, `oom`: the right family, graded FAIL because the record's cause
("the memory limit applied … is lower than the one in the release values") names none of the
case's second concept (`drift|512mi|declared`). h012 (uncovered, `gitops-drift`, whose cause is
also a memory-limit difference) ← inc-0003, FAIL for the same reason.

**Against the prediction:** "no uncovered case passes" holds; "R0 passes mostly covered cases"
does not — none passes. Read as: searched with the case's one-line `query`, every record about a
crash or a restart scores between 0.6 and 0.77, so retrieval returns a kind of failure, not its
cause (the question of `docs/incident-corpus.md` § Purpose, and the diagnosis's D5.5). Neither
the record, the synonyms nor the threshold were changed after the run: the corpus is frozen. C3
(the model with the same corpus) remains to run, live.

## Amendments

- **2026-10-22 — "Code needed", item 2.** `index_node` no longer writes the index (`save()`,
  since `docs/fix-plan-001.md` H40) and `persist_texts` no longer exists (H70): each
  investigation builds its index in memory. Item 2 holds by construction; no protocol rule
  changes.

- **2026-10-23 — the configurations follow the product, the sLLM is added, 19 cases.**
  Written before any run of this protocol; it replaces *Configurations*, the cases of
  *Fixed before the first run* and the controls. The decision rules below were approved on
  2026-10-23, in the commit that adds this amendment, before any run.
  - **Why the configurations change.** This protocol was written on 2026-10-17, when the
    LLM named the report's cause. Since `docs/structured-in-product.md` (H4) the report's
    cause is the structured diagnosis and the model explains it: a retrieval layer added
    there can change the explanation, never the graded cause. The layers are therefore
    measured where the model names the cause itself — on the pipeline's context without the
    diagnosis, the `context:` configuration of `docs/veracity-replay.md` (§ Amendment
    2026-10-22).
  - **Configurations**, each for mistral (LLM) and qwen2.5:3b-instruct (sLLM), 3 runs per
    case; C0 and R0 once (deterministic):

    | Id | What names the cause | Vector search | Resolved-incident memory |
    |---|---|---|---|
    | C0 | the structured diagnosis, no model (`structured`) | — | — |
    | R0 | the root cause of the top-ranked incident record, no model | — | corpus only |
    | C1 | the model, on the pipeline's context without the diagnosis (`context:`) | off | off |
    | C2 | as C1 | **on** — what the product's `index_node` builds for the investigation (graph, chart anchors, enterprise documents), in memory | off |
    | C3 | as C1 | on | **on** |

    C1 is the `context:` configuration of `docs/veracity-replay.md`: the same runs, not run
    twice. In the product path (structured diagnosis explained by the model) the layers are
    reported on `explanation_veracity` and `llm_disagreement` only, and decide nothing.
  - **Comparisons**, per model: C2 against C1 (vector search), C3 against C2 (incident
    memory); R0 against C0 (does the corpus alone point to the cause?). LLM against sLLM on
    the same configuration is reported on its own line.
  - **Cases:** the 19 native cases of `tests/integration/cases/` at the commit that approves
    these rules (h011 restored by `docs/failing-cases-002.md`, h026 added), frozen.
  - **Controls:** positive, h001, must pass (≥ 2/3) in C1, C2 and C3 for each model, or that
    run is invalid (re-run once, then recorded invalid); negative, **h022 and h026**: a
    layer that makes either name a cause where the previous configuration did not has added
    a false alarm.
  - **Decision rules** — the table of *Decision rules* unchanged, applied per model, with
    "h022" read as "h022 or h026". A layer stays in the default path only if it stays for
    both models or is inconclusive for one and stays for the other; if the two models
    disagree outright (stays / leaves), the layer is inconclusive and the README says why.
  - **The corpus, beyond *Known limit*.** The legacy cases of `cases/` share most failure
    families with the native cases, three share their name (`001_crashloopbackoff`,
    `002_imagepullbackoff`, `003_oomkilled`), and several share a workload: `001` and h001
    (payment-service), `002` and h002 (ml-inference), `019` and h011 (kafka); `009` and h005,
    `010` and h006 name the same workload with another cause. Fixed now, checked by a script
    whose output (every exclusion and its reason) is published with the corpus: a record is
    excluded if its source is a legacy case with the name of a native case, if it names a
    resource (workload, pod prefix, ConfigMap, Secret, Service, namespace other than
    `default`) present in a native case's fixtures, or if one of its symptoms appears
    verbatim (a log line, an event message) in a native case's fixtures — otherwise
    retrieval could return the case's own answer. A record of the same family is kept:
    recognising a known kind of incident is what the memory is for. Results are also reported split into cases whose
    family the corpus covers and cases it does not. Until the corpus exists, C3 and R0 are
    recorded as not measurable.
  - **A valid run reads and writes no real store.** `DocStore` (`./data/docs`) and the
    example store (`./data/examples`) point at temporary directories; C3 loads the committed
    corpus there and nothing else. A run that reads or writes outside them is invalid.
  - **C2 has no enterprise documents.** No document corpus exists, so C2 measures what
    `index_node` builds from the investigation's graph, the anchors and the charts; the
    enterprise-document part of the vector search is not measured, and claimed nowhere.
  - **Cost, estimated:** C2, about 3 h for mistral and 1.5 h for qwen; C3 the same once the
    corpus exists. C0 and C1 come from the comparison.

- **2026-10-23 — "Code needed", items 3 and 4; the store rule.** Written before any run.
  Item 4's directory becomes
  `tests/veracity/replay-baselines/<solver>[-<model>]-prompt_<v>-store_<store>-examples_<on|off>/`:
  C1 (`context:`) and the product's `llm:` path share a model and a prompt version, and would
  share a directory. Item 3 also records the commit and a hash of each case's fixtures. "A valid
  run reads and writes no real store" holds for C0 as for C1–C3, and is checked after each run
  for writes; reads are prevented by the isolation, not detected. Detail:
  `docs/veracity-replay.md` § Amendment 2026-10-23, what every run records. No decision rule
  changes.

- **2026-10-23 — what C2 adds, measured offline, and a noise control.** Written before any run
  of C2, after building it (`tools/veracity_replay.py --solver context:<model> --store faiss`)
  and reading its prompts on the 19 cases with a scripted model and isolated stores:
  - the vector search is built by the product's own code (`build_investigation_store`, shared
    with `index_node`) from the case's graph alone: 4 to 30 vectors per case, no resolved
    incident, no document, no enterprise chart, and **no anchor violation** in any case (none of
    the fixtures carries the `anchor.*` annotations it indexes);
  - what retrieval adds to the C1 prompt is a "Related context" list of objects already in the
    context (the Deployment, its Pods, the Namespace, an Event), and it changes the chunk count
    and the "Context quality score" line both prompts carry (h013: MEDIUM 0.42 in C1, HIGH 0.70 in
    C2) — a line that could move the model's label. It does so for 8 cases (h005, h007, h011, h012, h013, h016,
    h022, h026); **for 10 cases (h001, h002, h003, h004, h006, h008, h009, h010, h015, h024) the
    C2 prompt is the C1 prompt**, the timestamp line aside, and h014's has the same lines in
    another order. C2 adds no knowledge the C1 prompt lacks;
  - **prediction, stated before the numbers:** C2 against C1 shows a net gain of 0 or less for
    both models, so the decision rule says the vector search over the investigation's own graph
    leaves the default path. The rule is unchanged; the run decides, the prediction is checked
    against it and reported either way;
  - **noise control:** on the cases whose C2 prompt equals C1's (the timestamp line aside),
    any difference between C1 and C2 is run-to-run variation; it is reported as such, beside
    the net gain, and computed from the recorded prompts, not from this list;
  - **what C2 does not measure:** the knowledge base — resolved incidents (C3, R0) and
    enterprise documents (no corpus). "LLM against the knowledge base" is C3 and R0.

- **2026-10-23 — the two-model rule, completed.** Written before any run
  (`docs/comparison-protocol-issues.md` CPI-12). One model "leaves" and the other
  "inconclusive" had no outcome. Read literally ("stays only if"), the layer does not stay: it
  leaves the default path (kept in the code, off by default), flagged, since neither model showed
  a gain.

- **2026-10-24 — the context quality score in C1 and C2.** Written before any run
  (`docs/comparison-protocol-issues.md` CPI-16). The score line stays in the prompt; C2 against
  C1 also serves as the anchoring test of `docs/veracity-replay.md` § Amendment 2026-10-24. If
  the model's label is shown to follow the score, the labels of C2 against C1 are reported as not
  informative; the net gain is computed on graded causes and does not change.

- **2026-10-24 — the corpus's namespace rule.** Written before any corpus record
  (`docs/comparison-protocol-issues.md` CPI-20). In *The corpus, beyond Known limit* (amendment
  2026-10-23), "namespace other than `default`" becomes "namespace other than the generic
  `default`, `production`, `staging`, `monitoring` and `kube-system`": as written, the rule
  excluded 28 of the 29 legacy cases for naming `production` or `staging`. A namespace
  particular to a native case still excludes; no other rule changes. The corpus v1 follows
  `docs/incident-corpus.md` § Amendment 2026-10-24.

- **2026-10-24 — what R0 answers, and when it abstains.** Written before any run of R0
  (`docs/comparison-protocol-issues.md` CPI-22). "The root cause of the top-ranked incident
  record" answers every case, the negative controls h022 and h026 included, so R0 would fail the
  negative control by construction. R0 follows the product's own example path
  (`example_lookup_node`): an index built from the corpus alone by `ExampleIndexer`, searched
  with the case's `query` (`store.search`, the example hits only); when the top record's score —
  as the product computes it, cosine × the example source weight (`SOURCE_WEIGHT_EXAMPLE`, 1.2) —
  reaches the product's `EXAMPLE_MATCH_THRESHOLD` (0.65, `workflow/nodes.py`, unchanged), the
  answer is its `root_cause` with the product's label (the record's confidence, HIGH when it has
  none — every v1 record); below it, R0 abstains: no cause, label LOW. One run (deterministic);
  each record keeps the corpus hash, the matched record and the three best scores. No decision
  rule changes.

- **2026-10-24 — R0 is reported, not decided; a prediction.** Written before any run of R0
  (`docs/comparison-protocol-issues.md` CPI-23). "R0 against C0" with the vector search's rule
  cannot decide anything: C0 passes all 19 cases (in-sample), so R0 can never show a positive
  net gain, and the comparison does not answer R0's question — does the corpus alone point to the
  cause? R0 therefore gets no stays/leaves outcome. It is reported: cases passed among the 9
  cases whose family the corpus covers and among the 8 it does not (`docs/incident-corpus.md`
  § Amendment 2026-10-24), the negative controls h022 and h026 (a cause named there is a false
  alarm, reported as such), and, per case, the matched record and its score. **Prediction,
  stated before the numbers:** R0 passes mostly covered cases and **no** uncovered case — a cause
  from a family the corpus lacks cannot be the right one; an uncovered case that passes is
  examined (a chance keyword match, or a leak the exclusion missed) and reported either way. The
  decision on the incident memory stays C3 against C2, live.

- **2026-10-24 — what C3 adds, measured offline; h016's size; a prediction.** Written before any
  run of C3, after building it (`--solver context:<model> --store faiss --examples on`) and
  reading its prompts on the 19 cases with a scripted model and isolated stores:
  - the corpus v1 (11 records) is indexed in every case (`index.examples` = 11), and the
    product's context lists **five "SIMILAR PAST INCIDENTS — proven remediations"** in every
    case, the negative controls h022 and h026 included — no threshold on this path — each with
    its root cause and fix (CPI-19); the same incidents appear again under "Related context"
    (15 mentions per prompt). The prompt grows by 8 500 to 10 000 characters;
  - the first of the five has the case's own family for h001, h003, h008 and h009; it is second
    for h011, fourth for h004, fifth for h024 (`oom` only); absent for h005 and h010 (covered) and
    for every uncovered case. The "Context quality score" rises in every case (+0.07 to +0.14),
    from MEDIUM to HIGH in ten: the anchoring confound of CPI-16 applies to C3's labels too;
  - **h016 does not fit** (CPI-24): 22 724 characters, about 8 400 tokens by the client's
    estimate, over the 8 192 ceiling with the answer and the reserve. The ceiling is unchanged:
    the product's own guard refuses the prompt, so h016 is a C3 FAIL recorded `did_not_fit`, and
    the net gain of C3 against C2 is reported with the cases lost by size named;
  - **prediction, stated before the numbers:** C3 against C2 gains at most 2 cases for each model
    — the right family heads the list in four covered cases only, R0 passed none, and h016 is lost
    by size — so C3 does not stay; a cause named on h022 or h026, now shown five past causes, is
    the likeliest way for it to leave. The rule is unchanged; the run decides.
