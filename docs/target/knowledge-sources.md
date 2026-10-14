# Knowledge sources — public vs enterprise

> Findings 2026-10-14, read from the code. Step 1 below is specced (go given
> 2026-10-14, "pour améliorer le diagnostic"); the sLLM export guard comes later.

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

## Spec — step 1: provenance for the diagnosis

> 2026-10-14. Scope: what the diagnosis reads. The sLLM corpus export guard is step 2,
> once a corpus export exists.

**Model.** `knowledge/provenance.py`:
- `Origin = "public" | "enterprise"`.
- `classify_url(url) -> Origin`: `public` when the host (or `oci://` registry path) is in
  `PUBLIC_CHART_SOURCES` (config, comma-separated, with a default list of well-known
  public chart repos: Artifact Hub, Bitnami, prometheus-community, grafana, jetstack,
  kubernetes-sigs…). **Anything else is `enterprise`**: an unknown source is treated as
  confidential (fail closed), never as public.

**Where it is recorded.**
- `EnterpriseChart` gets `origin` (default `enterprise`) and `source_url`.
  `ChartStore.push(..., origin=None, source_url="")`: when `origin` is not given, it is
  derived from `source_url` with `classify_url`; with neither, `enterprise`. Existing
  `.kv-meta.json` files without the field load as `enterprise`.
- `HelmRepository` gets `origin`, classified from its URL by `helm_collector`, and its
  `to_text()` says it (`origin=public`), so the context tells "upstream chart defaults"
  from "enterprise registry".
- `EnterpriseDoc` and `ResolvedIncident` are enterprise by construction (pushed by the
  operator, or approved in this cluster): no field needed.

**Indexing — the weights finally apply.**
- Chart anchors: `doc_source = "enterprise"` or `"public"` from the chart's origin.
- Enterprise docs: `doc_source = "enterprise"`. Resolved incidents: `"example"`.
- New weight `public` = **0.8** (`SOURCE_WEIGHT_PUBLIC`): an upstream default is weaker
  evidence of what *this* company intends than its own pinned expected state
  (`enterprise` 1.5). The existing weights are unchanged.

**Not changed.** The graph's own entities stay `cluster`; anchors stay `anchor`. No
export, no UI.

**Effect on veracity.** Retrieval only. The `stub` replay cannot show it, so it is
measured with `--store faiss` (next replay configuration).

**Tests.** `classify_url` (public hosts, OCI, unknown → enterprise, configurable list);
`push` derives and persists `origin`, old meta files load as `enterprise`; each indexer
sets its `doc_source`; ranking: at equal similarity an `enterprise` chunk outranks a
`public` one, in `search` and in `hybrid_search`; `HelmRepository` origin from the
collector.

**Implemented (2026-10-14).** `knowledge/provenance.py` (`classify_url`,
`PUBLIC_CHART_SOURCES`); `EnterpriseChart.origin` / `source_url` and
`ChartStore.push(origin=…, source_url=…)`; chart anchors indexed with
`doc_source = origin` and `origin=` in their text; enterprise docs indexed as
`enterprise`, resolved incidents as `example`; `HelmRepository.origin` from the helm
collector, shown in its context text; `SOURCE_WEIGHT_PUBLIC` 0.8.
Tests: `tests/unit/test_provenance.py` (classification, push, old meta files, each
indexer's `doc_source`, enterprise outranking public at equal similarity in `search`
and `hybrid_search`).
- *Changed while implementing:* a configured public source may be written with its
  scheme (`oci://registry.acme/public`); entries are normalized (no scheme, no trailing
  slash). `ghcr.io` is not in the default list: it hosts public and private charts
  alike, so it stays enterprise unless configured.
- *Effect on existing data:* documents and examples indexed **before** this change keep
  `doc_source = cluster` in `vector_store_docs` until they are re-indexed. Since the
  weights were never applied before, every ranking that mixes enterprise docs or
  examples with cluster entities changes: enterprise docs ×1.5, examples ×1.2.
- *Found while implementing:* `ChartStore.push()` has no production caller (no API
  route, no CLI, no MCP tool): the enterprise expected state is fed only by files
  placed under `data/charts/` by hand. A push entry point is needed before this can be
  used outside tests.

