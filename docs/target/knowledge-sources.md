# Knowledge sources — public vs enterprise

> Findings 2026-10-14, read from the code. Nothing implemented. To discuss at the next
> checkpoint, with the store and graph questions of [veracity.md](veracity.md).

## What exists

| Source | Where | Provenance recorded | `doc_source` when indexed |
|---|---|---|---|
| Expected-state charts (Helm / Helmfile / Kustomize / manifests) | `knowledge/chart_store.py` (`EnterpriseChart`), `knowledge/chart_indexer.py` | `source: upload / git / repo` = *how* it arrived, not *whether it is public* | none → `cluster` |
| Enterprise docs (runbooks, SOPs, Confluence) | `knowledge/doc_store.py`, `knowledge/doc_indexer.py` | `source: manual / upload / url` | none → `cluster` |
| Resolved incidents | `knowledge/example_store.py` | none | none → `cluster` |
| Manifest anchor violations | `vectorstore/store.py` `index_anchor_violations` | — | `anchor` ✅ |
| Official Kubernetes docs | legacy Streamlit UI only (`ui/app.py`) | — | — |

## What already relates to chart origin (checked 2026-10-14)

- **The repository of each release is in the graph.** `ingestion/helm_collector.py`
  reads `helm repo list` and creates `HelmRepository` entities (`name`, `url`,
  `repo_type: http | oci`) linked to releases and charts. A release from a public repo
  (e.g. `https://charts.bitnami.com`) and one from an internal OCI registry are visible
  as such through their URL. Nothing classifies the repo as public or private, and no
  ranking or rule reads it.
- **Enterprise expected state** (#86): charts pushed into `data/charts/` at their pinned
  version, rendered and indexed as anchors. Everything pushed there is treated as
  enterprise, an upstream chart included.
- **Artifact Hub** (B11) is the publication of kube-verdict's own chart, not the
  ingestion of public charts.

So the origin is readable from the repo URL, but "public vs private" exists neither as
data nor as a rule. The proposal below can derive a first `origin` from the
`HelmRepository` URL (a configurable list of public hosts), then let the operator
confirm it when pushing a chart.

## Findings

1. **No notion of a public chart.** Every pushed chart is an `EnterpriseChart`; an
   upstream chart and an internal one cannot be told apart. No upstream chart
   (Artifact Hub, GitHub) is ingested as a reference.
2. **Source weights are dead configuration.** `config.py` defines `SOURCE_WEIGHTS`
   (`enterprise` 1.5, `runbook` 1.8, `example` 1.2, `official` 1.0), but no indexer
   passes `doc_source` to `FAISSStore.add_entity()`, so everything is weighted as
   `cluster` (1.0). Only `anchor` is tagged.
3. **Official docs are not ingested by the API path.**

## Why it matters for the sLLM

- **Confidentiality.** Enterprise charts, runbooks and incidents must never enter a
  training corpus or a public artefact without explicit consent. Public content can,
  under its licence. Today nothing allows that split.
- **Relevance.** The enterprise expected state (its values, its pinned version) must
  outrank upstream chart defaults, which requires knowing which is which.

## Proposal (to spec)

- One provenance record on charts, docs and examples: `origin: public | enterprise`,
  plus `licence` and `source_url` for public content and `tenant` for enterprise.
- `doc_source` set at indexing time from that record, so the weights apply.
- An export guard for the sLLM corpus: nothing `enterprise` without an explicit flag,
  and a test that enforces it.
- Official Kubernetes docs ingested by the API path, tagged `official`.
