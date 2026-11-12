# R2 — does hybrid retrieval over heterogeneous sources bring in the hop the graph cannot build?

> Spec written 2026-11-08, before any R2 corpus, case or run. Status: approved 2026-11-08. The rule
> and the prediction below do not change after the first run; a change is a dated amendment, and a run made before
> it stays reported as it was.

## Why

The product's founding thesis is that hybrid retrieval (BM25 + dense vectors + Reciprocal Rank Fusion, with a weight
per source) over sources outside the cluster's graph brings in what the ontology cannot build alone. Those sources
are enterprise Helm charts, release notes and changelogs, the logs of Helm, ArgoCD and Flux, Kubernetes and component
documentation, runbooks, enterprise documents, resolved incidents and manifest anchors.

That thesis was never measured:
- R1 (`docs/protocol-001-retrieval.md`) indexed only the investigation's own graph (4 to 30 vectors, no document).
- R0 used an 11-record incident corpus.
- S2 ran with the store off.

On S2 with the store on (`docs/s2-context-reach.md`), retrieval over the graph alone reached 3 cause objects that no
relation reached. Once the relations were built, it added none and cost one, crowded out of 10 slots. A store that
holds only the graph repeats the ontology. R2 measures the store with what the graph does not hold.

## The question

For an incident whose **origin** (the change that made the cause abnormal) is written in a document outside the
cluster, does hybrid retrieval rank that document among its first results, better than chance, and better than
BM25 or the dense search alone?

## Roles

Retrieval **proposes**: a link or an origin, with its provenance (source, document, rank, score per list). The
correlation engine (`docs/correlation-engine.md`, M4) **verifies** each proposal against the cluster's live state; an
unverified proposal is reported as such and never counts as a cause. The model, if any, explains. R2's first part
measures retrieval alone, so it needs no engine and no model.

## The cases

- **Real upgrades of real charts.** Each R2 case starts from a documented change in a public chart's changelog or
  upgrade notes, at pinned versions: a renamed value, a moved port, a changed default, a removed key. The case is the
  incident that change causes when it is applied without the matching values. The change is real and its document
  exists. Neither is written by us.
- **Selection, fixed before any case.**
  - The charts: the first public charts, by Artifact Hub listing, that the S2 families touch (a cache, an ingress
    controller, a metrics adapter, a certificate manager, a database). The exact list is written in the corpus
    manifest before any case is drawn.
  - The entries: every changelog or upgrade-notes entry marked breaking, or listed under "upgrading", across the two
    latest major versions.
  - The rule: an entry is kept when its effect is visible in the objects the product reads, as listed in
    `tests/s2b/scenarios/brief-v2.md` § What the tool can see.
  - **At least 12 cases**, one per kept entry, drawn in a fixed order (chart, then version, then entry).
  - **4 cases with no incident**: the same charts upgraded with the matching values. They hold the same changelog
    documents.
- **Author.** The scenario of each case (the question, the chain, the decoys) is written by the external author of
  S2b (`docs/s2b-generation.md`), from the changelog entry and the brief. The engine's author only picks the entries,
  by the rule above. The ground truth names the document chunk that holds the origin before any retrieval runs: the
  file, the version and the entry's first line.

## The corpus

Real sources only, each with its URL, version or commit, licence and SHA-256, in a manifest written before the
first index:

| Source | `doc_source` | What |
|---|---|---|
| chart sources (templates, `values.yaml`, `README`, `CHANGELOG`, upgrade notes) | `public` | the selected charts at every version in the window, not only the ones the cases use |
| Kubernetes documentation | `official` | the concept and task pages the S2b families cite |
| prometheus-operator runbooks | `runbook` | at a pinned commit |
| component documentation | `official` | the selected components' configuration pages |
| enterprise documents | `enterprise` | not in R2's first run: none real is available, and synthetic ones would be written by someone who knows the cases |

**Distractors.** The corpus holds every version and entry of the selected charts, plus unselected charts of the same
kinds, so that the right chunk competes with many near ones. The corpus is indexed by the product's own indexers
(`knowledge/doc_indexer.py`, `knowledge/chart_indexer.py`), with their chunking unchanged, and frozen by its hash
before the first query.

## The measures

**First part: retrieval alone.** No engine, no model.
- **The query.** It is built from what the cluster shows: the symptom's object, its failing field or message, and
  the release and its version, as the product would build it (`rca/context_builder.py`'s query, and the frontier
  query M4 will send). It is never built from the ground truth.
- **R2.1.** For each case, the rank of the origin chunk in four orders:
  - BM25 alone;
  - dense alone;
  - the hybrid RRF with source weights, as `hybrid_search` runs;
  - chance, computed exactly for the corpus size, as M1 of R1 was.

  Reported: the cases whose origin chunk is in the top 5 (R@5) and in the top 10 (R@10).
- **R2.2.** On the 4 cases with no incident, the number of changelog chunks of the upgraded chart in the top 5. This
  is the risk that retrieval makes a harmless upgrade look guilty. It is reported, for the second part.

**Second part: with the engine.** Run once M4 exists. On the same cases, does the engine with M3's proposals find the
origin in more cases than the engine alone? Does it lose any case with no incident? This part is written in detail
by an amendment before M4 is run on R2.

## The prediction, written before the corpus

- The hybrid ranks the origin chunk in the top 5 in **at least half** of the cases, BM25 alone in fewer, and dense
  alone in fewer still. Changelog entries name the exact value or key, which BM25 matches, and RRF keeps that while
  adding paraphrases.
- Chance is near 0 for a corpus of thousands of chunks.
- On the cases with no incident, the upgraded chart's changelog reaches the top 5 in most of them. That is why the
  engine's verification is required before anything retrieval proposes counts.

## The rule

- **Retrieval earns its place in the default path** when all of the following hold:
  - the hybrid's R@5 is at least 3 cases above chance and not below BM25 alone's;
  - in the second part, the engine with M3 finds the origin in at least 3 more cases than without it;
  - it loses no case with no incident.
- **Inconclusive:** a gain of 1 to 2 cases in either part.
- **Otherwise:** retrieval stays off by default, and the result is published.
- R2 is exploratory (16 cases): intervals, no significance test. Its numbers feed the volume of the 280-case set (A3)
  only through a dated amendment.

## What R2 cannot show

- Enterprise documents: none real exists yet, so their weight stays unmeasured and is claimed nowhere.
- Charts that are not public, and incidents with no written origin (a hand edit, a cloud event).
- Whether a model uses the proposals well: that is the second part, and protocol 002's arms after it.

## Order

1. Review this spec.
2. The corpus manifest (the charts, versions, sources, licences), then the selection of entries, both before any
   case.
3. The cases from the external author, their fixtures, the freeze.
4. The corpus indexed and frozen; R2.1 and R2.2 run offline; results appended below.
5. The second part, by amendment, once M4 exists.
