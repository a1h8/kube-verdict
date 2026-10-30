# Comparison and protocol 001: issues register

> Defects found in the pre-registered rules of [veracity-replay.md](veracity-replay.md)
> § Comparison (and its amendments) and of [protocol-001-retrieval.md](protocol-001-retrieval.md),
> while implementing them (`tools/veracity_replay.py`, `tools/compare_solvers.py`).
> Rule: a defect is **fixed by a dated amendment before the first run**, never by moving a
> threshold after seeing a number. No run of the comparison or of protocol 001 has taken place
> when an entry is resolved.
> Categories: **GAP** (a case no rule covers) · **UNDERSPEC** (a missing value or term) ·
> **INCONSISTENT** (two rules that cannot both hold) · **AMBIGUOUS** (several readings).
> Status: RESOLVED (amended and enforced by a test) · OPEN (decided before the first run, or the
> run does not start).

| ID | Category | Severity | Status |
|----|----------|----------|--------|
| CPI-1 | GAP | high | RESOLVED (2026-10-23) |
| CPI-2 | GAP | high | RESOLVED (2026-10-23) |
| CPI-3 | GAP | high | RESOLVED (2026-10-23) |
| CPI-4 | GAP | blocking | RESOLVED (2026-10-23) |
| CPI-5 | GAP | blocking | RESOLVED (2026-10-23) |
| CPI-6 | UNDERSPEC | medium | RESOLVED (2026-10-23) |
| CPI-7 | INCONSISTENT | medium | RESOLVED (2026-10-23) |
| CPI-8 | UNDERSPEC | medium | RESOLVED in text, checked live (2026-10-23) |
| CPI-9 | AMBIGUOUS | medium | RESOLVED (2026-10-23) |
| CPI-10 | AMBIGUOUS | medium | RESOLVED (2026-10-23) |
| CPI-11 | INCONSISTENT | low | RESOLVED (2026-10-23) |
| CPI-12 | GAP | medium | RESOLVED (2026-10-23) |
| CPI-13 | UNDERSPEC | medium | RESOLVED (2026-10-23) |
| CPI-14 | INCONSISTENT | low | RESOLVED (2026-10-23) |
| CPI-15 | INCONSISTENT | medium | RESOLVED (2026-10-24) |
| CPI-16 | AMBIGUOUS | medium | RESOLVED (2026-10-24) |
| CPI-17 | UNDERSPEC | low | RESOLVED (2026-10-24) |
| CPI-18 | GAP | medium | DEFERRED (2026-10-25, with C3) |
| CPI-19 | INCONSISTENT | medium | RESOLVED in text (2026-10-24) |
| CPI-20 | GAP | blocking | RESOLVED in text (2026-10-24) |
| CPI-21 | UNDERSPEC | medium | RESOLVED (2026-10-24) |
| CPI-22 | UNDERSPEC | medium | RESOLVED (2026-10-24) |
| CPI-23 | INCONSISTENT | medium | RESOLVED (2026-10-24) |
| CPI-24 | GAP | medium | RESOLVED (2026-10-24) |
| CPI-25 | GAP | medium | RESOLVED in text (2026-10-27) |
| CPI-26 | GAP | blocking | RESOLVED (2026-10-30) |
| CPI-27 | INCONSISTENT | blocking | RESOLVED (2026-10-30) |

---

## CPI-1: the collector-cut control named no collector (RESOLVED)

**Symptom.** The 2026-10-20 negative control "a run with the case's collector cut is never more
confident than the connected run" named no collector, no run and no comparison; no code applied
it.
**Decision (2026-10-23).** The case's collector is the HTTP backend `docs/test-cases.md` §3
already cuts (Prometheus, Loki, OTel): six cases, eight cut runs per configuration; the 13 others
are reported as not applicable (`veracity-replay.md` § Amendment 2026-10-23, the collector-cut
control). Implemented: `--cut`, `tests/unit/test_cut_runs.py`.

## CPI-2: grading files and model not identified in a run (RESOLVED)

**Symptom.** Protocol 001 requires the hashes of `tools/veracity.py` and
`tests/veracity/synonyms.yaml` and the model's digest in every run; only the model's name was
recorded.
**Decision (2026-10-23).** Recorded in every run, with the commit and a hash of the case
(`tests/unit/test_run_records.py`).

## CPI-3: a truncated run looked like a FAIL (RESOLVED)

**Symptom.** "A run that read fewer tokens than it was sent is invalid" was not detected: the
refusal works from an estimate, and a prompt the estimate undercounts is cut by Ollama without an
error.
**Decision (2026-10-23).** A run whose tokens read plus `num_predict` reach `num_ctx` is recorded
invalid and never counted as a pass.

## CPI-4: two solvers could write a case's answer into the incident base (RESOLVED)

**Symptom.** The store isolation of 2026-10-22 covered `raw:` and `context:` only. `structured`
and `llm:` ran where the replay was started; on the AUTO path `save_example` writes the run's
answer into `./data/examples` — the incident base protocol 001's corpus must keep disjoint from
the cases. Nothing checked after a run.
**Decision (2026-10-23).** Every solver runs in a temporary directory (stores, database, vector
index); a run that changed a real store is invalid. The check sees writes only; reads are
prevented, not detected — said in the amendment.

## CPI-5: the test suite wrote the real database (RESOLVED)

**Symptom.** Run from an empty directory, the suite left `kubeverdict.db` (sessions written by
two test files), its checkpoints and two store directories. Collection alone opened the database
(pytest inspects the attributes of the lazy session-store proxy).
**Decision (2026-10-23).** Every store and database points at one temporary directory from
`pytest_configure`; the run fails when a real store changed (`docs/fix-plan-001.md` H81).

## CPI-6: what a record must say was incomplete (RESOLVED)

**Symptom.** Protocol 001's item 3 asked for the configuration (C0–C3), store, examples and
corpus hash; neither these nor the commit were recorded, so "the frozen case list" and "the
commit of block B" could not be checked.
**Decision (2026-10-23).** All recorded (CPI-2); `invalid` and `cut` too.

## CPI-7: two configurations shared a records directory (RESOLVED)

**Symptom.** Protocol 001's directory `<model>-prompt_<v>-store_…-examples_…` is the same for
`llm:mistral` and `context:mistral` (both prompt v6): one report would overwrite the other.
**Decision (2026-10-23).** The solver is in the name; a records directory with another name is
refused unless `--out-free`.

## CPI-8: prompt sizes were checked on one case and one model (RESOLVED in text, checked live)

**Symptom.** The size estimate (2.7 characters per token) was calibrated on mistral and the
analyze prompt only; the 2026-10-22 text measured h016 alone. Raw YAML and qwen2.5 were never
measured. Offline, every `context:` and `llm:` prompt fits under 8 192 tokens by the estimate
(h016 with about 11 % of margin).
**Decision (2026-10-23).** Before the first run, every `raw:` and `context:` prompt is measured
with each model's own tokenizer, through the Ollama that runs them (`docs/fix-plan-001.md` H80).

## CPI-9: which label the cut control compares (RESOLVED)

**Symptom.** "More confident than the connected run" did not say which label, nor which of three
connected runs.
**Decision (2026-10-23).** Each record's `confidence` (the diagnosis's for `structured`, the
workflow's for `llm:`, the model's for `raw:` and `context:`), against the lower median of the
valid connected runs; a missing or invalid cut run fails (`veracity-replay.md` § Amendment
2026-10-23, how the tables read the controls).

## CPI-10: negative controls per run or per case (RESOLVED)

**Symptom.** "h022 and h026 stay `no_incident`" could be read per run or per case.
**Decision (2026-10-23).** Per case, with the 2-of-3 rule of every other case; the runs that
named a cause are reported beside it. One false alarm fails the control, which is also how the
rules' "no more false alarms" clauses apply.

## CPI-11: block B on the merged code, or before the merge (RESOLVED)

**Symptom.** `docs/fix-plan-001.md` said block B runs "on the merged code"; the plan runs it
before the merge, on the head of the PR.
**Decision (2026-10-23).** On the head of the PR, before the merge; the commit is in every run.

## CPI-12: the two-model rule had a hole (RESOLVED)

**Symptom.** Protocol 001: a layer stays if it stays for both models or is inconclusive for one
and stays for the other; outright disagreement is inconclusive. One model "leaves" and the other
"inconclusive" was covered by no outcome.
**Decision (2026-10-23).** Read literally ("stays only if"): the layer does not stay — it leaves
the default path, flagged, since neither model showed a gain. Amended in protocol 001 and in
`tools/compare_solvers.py` (`two_models`).

## CPI-13: what C2 measures was not said (RESOLVED)

**Symptom.** Protocol 001's C2 was "the path `index_node` takes"; nobody had read what that adds
to the prompt.
**Decision (2026-10-23).** Measured offline and written down before any run: built from the
case's graph alone, it adds no knowledge the C1 prompt lacks; for 10 cases the prompt is the
same. Prediction: net gain ≤ 0. Those 10 cases become a noise control (protocol 001, amendment
2026-10-23).

## CPI-14: prompts are never identical byte for byte (RESOLVED)

**Symptom.** Every analyze prompt carries an `Analysis timestamp` line, so two runs on the same
input never send the same bytes.
**Decision (2026-10-23).** Comparisons of prompts (the C2 noise control) leave that line out.

## CPI-15: h016 stays HIGH with a collector cut (RESOLVED)

**Symptom.** With any of its three collectors cut, h016's structured diagnosis stays HIGH (the
control passes: not more confident than connected). `docs/test-cases.md` §4 says the label is
"never HIGH" with a collector cut.
**Read (2026-10-24).** There is one confidence: since H4 the product's report carries the
structured diagnosis's. h016's cause is `db.connections_exhausted` (outside: postgres-primary),
supported by all three signals: 0.95 connected, 0.95 with Prometheus cut, 0.80 with Loki or OTel
cut, above the HIGH threshold (0.75). Redundant evidence keeps HIGH legitimately; the test
already asserts "never higher than connected". The §4 sentence predates H4.
**Decision (2026-10-24).** `docs/test-cases.md` §4 amended: never more confident than connected;
the label may stay HIGH when redundant evidence remains. No threshold changes.

## CPI-16: a score in the prompt may anchor the model's label (RESOLVED)

**Symptom.** The context carries a "Context quality score" (HIGH, MEDIUM) computed from
heuristics (BFS depth, diversity, TF-IDF). With C2 it moves from MEDIUM 0.42 to HIGH 0.70 on h013
without new information. A model asked for a confidence label may copy it.
**Decision (2026-10-24).** The score stays in the prompt: prompt v6 is what the product sends,
and removing it would measure a prompt nobody runs. It is not only declared a confound: an
anchoring control, fixed before any run, measures it (`docs/veracity-replay.md` § Amendment
2026-10-24). If anchoring is shown, the model's labels — the collector-cut control of `context:`
and the labels of C2 against C1 — are reported as not informative; graded causes are unaffected.
Removing the score is then a separate, measured change (prompt v7). Implemented the same day in
`tools/compare_solvers.py` (`anchoring`, `agreement`); its tests fail on a mutant of each
threshold (`tests/unit/test_compare_solvers.py`).

## CPI-17: no anchor violation is indexed in any case (RESOLVED)

**Symptom.** `index_anchor_violations` indexes `anchor.*` annotations marked `[manifest]`; none
of the 19 cases carries one, so the "anchors" part of the vector search is empty in C2, while the
quality score reports "Anchors matched 14" — another notion of anchor.
**Read (2026-10-24).** Not dead code: `anchor_node` makes `[manifest]` anchors only from a GitOps
provider (the chart rendered from the repository); the replay has none, so every case has schema
anchors (6 to 70, what "Anchors matched" counts) and no manifest anchor. Offline, neither
`index_anchor_violations` nor the manifest branch of `anchor_fix_hints` runs, and the `context:`
prompt lacks the manifest fix hints a live run with a GitOps source carries — a difference
between offline and live, declared.
**Decision (2026-10-24).** A gap of the harness, recorded in `docs/test-cases.md` (the loader's
gaps table); exercised live only. C2's prediction (CPI-13) holds. Whether the harness should get
a GitOps source is a question for a later change, not for this protocol.

## CPI-18: results split by corpus family not implemented (DEFERRED)

**Symptom.** Protocol 001 reports C3 and R0 split into cases whose family the corpus covers and
cases it does not; nothing computes it.
**Decision.** Implemented with the corpus (`docs/incident-corpus.md`); C3 and R0 are not
measurable before it.
**Families fixed (2026-10-24).** Each native case's family is fixed in `docs/incident-corpus.md`
§ Amendment 2026-10-24, before any record is written; the split is still to implement.
**Deferred (2026-10-25).** C2 and C3 are not run in this PR's live run (protocol 001, amendment
2026-10-25); the split is implemented before their run, not before this one.

## CPI-19: the corpus spec and the product index different text (RESOLVED in text)

**Symptom.** `docs/incident-corpus.md` says a record's `root_cause` and `remediation` are stored
but not embedded; the product's `ExampleIndexer` embeds the whole incident (cause and fix in
`_ExampleChunk.to_text`). Both cannot hold for C3 and R0.
**Decision (2026-10-24).** C3 and R0 measure the product's path as it is; the spec's rule is
recorded as not implemented by the product, and following it is a separate, measured change
(`docs/incident-corpus.md` § Amendment 2026-10-24).

## CPI-20: the namespace rule excluded almost the whole corpus (RESOLVED in text)

**Symptom.** Protocol 001 excludes a corpus record that names "a namespace other than `default`"
present in a native case's fixtures. The native cases use `production` (143 occurrences),
`staging`, `monitoring` and `data`; applied to the sources, the rule excludes 28 of the 29 legacy
cases (only `014_resource_quota_exceeded`, in `team-alpha`, remains), so C3 and R0 could not be
measured. The rule's intent is to keep out a record that describes a case's own resources, not
to forbid the names of environments. Found reading the sources, before any record was written.
**Decision (2026-10-24).** The generic namespaces `default`, `production`, `staging`,
`monitoring` and `kube-system` (closed list) do not exclude a record; a namespace particular to a
case still does, and the rules on workloads, pod-name prefixes, ConfigMaps, Secrets and Services
are unchanged. Amended in protocol 001 and `docs/incident-corpus.md` (§ Amendment 2026-10-24).

## CPI-21: what "names a resource" reads in a legacy source (RESOLVED)

**Symptom.** The first exclusion script read a source's names from its structured fields only
(object names, references, labels). `cases/018_secret_wrong_key` names h004's Secret
`db-credentials` inside an event message and an anchor's value, and was kept. Reading every field
as text then excluded three sources on the word `api` (h003's workload): "resource metrics API",
"in API group", the path "POST /api/orders" — none names a resource. Found running the script on
the 29 sources, before any record was written.
**Decision (2026-10-24).** A source's names are read from every field except its author's prose
(`symptom`, `scenario`, `query`); a native case's names from its structured fields only. A name
matches as a Kubernetes name: case-sensitive (names are lowercase), a pod name by its prefix, a
DNS name by its first label, never a segment of a URL path (a URL's host is kept). The rules of
`docs/incident-corpus.md` § Amendment 2026-10-24 are unchanged; each reading has a test that fails
on its mutant (`tests/unit/test_corpus_exclusions.py`). Result: 11 of the 29 sources kept.

## CPI-22: R0 could not abstain (RESOLVED)

**Symptom.** Protocol 001's R0 is "the root cause of the top-ranked incident record, no model":
read literally it names a cause on every case, h022 and h026 included, and fails the negative
control whatever the corpus holds — no decision could ever be drawn from it.
**Decision (2026-10-24).** R0 follows the product's example path as it is: the top record answers
only when its score reaches the product's `EXAMPLE_MATCH_THRESHOLD` (0.65), with the product's
label (HIGH when the record has no confidence); below it R0 abstains, LOW. The threshold is the
code's, fixed before any run (protocol 001, amendment 2026-10-24, R0).

## CPI-23: R0 against C0 could not decide (RESOLVED)

**Symptom.** Protocol 001 compares R0 with C0 under the vector search's rule (net gain ≥ 3 stays,
≤ 0 leaves). C0 passes the 19 cases (in-sample), so R0 can never gain: the outcome is fixed
before any run and says nothing about whether the corpus alone points to the cause.
**Decision (2026-10-24).** R0 gets no outcome; it is reported split into covered and uncovered
cases, with the negative controls and the matched record per case, against a prediction written
before the run (protocol 001, amendment 2026-10-24, R0 reported). The incident memory is decided
by C3 against C2.

## CPI-24: h016's C3 prompt does not fit (RESOLVED)

**Symptom.** With the corpus loaded, every context prompt grows by 8 500 to 10 000 characters
(five similar incidents, repeated under "Related context"). h016's reaches 22 724 characters,
about 8 400 tokens by the client's estimate: over the 8 192 ceiling, so the client refuses it
before any call. Nothing said how a refused C3 prompt counts.
**Decision (2026-10-24).** The ceiling stays: the refusal is the product's own guard, so h016 is a
C3 FAIL recorded `did_not_fit`, and the net gain of C3 against C2 is reported with the cases lost
by size named. The tokenizer measurement before the live run (`docs/fix-plan-001.md` H80) covers
the C3 prompts too (protocol 001, amendment 2026-10-24, C3).

## CPI-25: the vector search stays in the default path unmeasured (RESOLVED in text)

**Symptom.** C2 is deferred (protocol 001, amendment 2026-10-25), block B runs every model
without the vector search, and the product builds it at every investigation (`index_node`): after
the live run, a layer of the default path would still have no measurement, predicted useless.
**Decision (2026-10-27).** R1, offline and without a model: what the search finds alone against
an exact chance line (M1, reported), and the cases where it brings the cause into the context
(M2, decides) — protocol 001, amendment 2026-10-27.

## CPI-26: C2's prompts are not measured with the model's tokenizer (RESOLVED)

**Symptom.** C2 is added to block B (`docs/live-validation-001.md`, amendment 2026-10-30).
Step 4 of that file measures every prompt with the model's tokenizer before the first run, but
`tools/token_measure.py` builds the `raw:` and `context:` prompts with the stub store only: C2's
prompts, longer by the search's hits, would be sent unmeasured — the silent truncation that
invalidated every earlier LLM measure.
**Decision (2026-10-30).** `tools/token_measure.py --store faiss` builds C2's prompts as the
replay does (`isolated_stores`, the investigation's store in memory) and lists them with the
others; a test checks that a C2 prompt is measured and that the stub prompts are unchanged.
Block B does not start before it is committed.
**Resolved (2026-10-30).** `--store faiss` adds one C2 row per case (`context`, `store: faiss`,
no cut); the table records its `store`. Tests in `tests/unit/test_token_measure.py`: 19 C2 rows,
the stub rows identical with or without it (control), the C2 prompt built by the vector search
(a mutant that builds it without the search fails the test).

## CPI-27: the tokenizer measurement cannot run in the cluster's Ollama (RESOLVED)

**Symptom.** Step 4 of `docs/live-validation-001.md` measures with `num_ctx` 32 768. Ollama
allocates the KV cache for the whole context: 4 GiB for mistral at 32 768, plus 4.1 GiB of
weights, over the pod's 8 Gi limit. The first measurement (2026-10-30, 03:39Z) was OOM-killed on
its first prompt; nothing was recorded, no measured run had started.
**Decision (2026-10-30).** `MEASURE_CTX` = 16 384, twice the runs' ceiling (8 192), by an
amendment of step 4 (same day); the invalid rule keeps its meaning with the new value. The pod's
memory is not raised. A test checks the context is at least twice the ceiling and that mistral's
KV cache at that size, with the weights, stays under 8 Gi.
**Corrected (2026-10-30, before any measured run).** That decision was wrong: at 16 384 Ollama was
OOM-killed again on an 8 000-token prompt — the estimate left out the compute buffers. The pod's
limit is raised to 12 Gi (cluster and `k8s/ollama.yaml`); the memory test is removed (memory
is not computable offline) and the measurement counts only if Ollama does not restart during it.
The measurement uses the replay's client timeout (900 s).
**Corrected again (2026-10-30).** 12 Gi was OOM-killed too (8.1 GB anonymous memory, 3.8 GB file
cache); the limit is 16 Gi, and the pod's memory is sampled during the block.
