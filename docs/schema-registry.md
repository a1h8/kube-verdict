# Schema registry: the product reads the definitions of what it collects

> Spec written 2026-11-09, before any of its code. Status: approved 2026-11-09, with the bundle amendment (§ 1).
> It implements origin 3 of `docs/anchors-and-relations.md` ("anchors come from the definition first"), and the
> correction of fixture plausibility found in the diagnostics (D1.5: fixtures are not validated as real Kubernetes
> objects). It must be done before the GitOps cases (`docs/s2g/gitops-cases.md`), which are then read through it.

## The rule

The definitions of Kubernetes, of the CRDs (Argo CD, Flux, cert-manager, any other), and of Helm charts are
**retrieved by the product's code** from where they are published. A fact read by hand in a tool's documentation does
not become product data, except what the definition does not hold. That exception is declared, sourced and justified
entry by entry, as `ontology/spec/conditions.yaml` already requires.

## What exists, and why it is not enough

- `ingestion/k8s_schema.py` holds a schema **written by hand** for 7 kinds (`_SCHEMA`). `K8sApiSchema` can enrich it
  from the live `/openapi/v2`, but only for 5 core group prefixes and only top-level `spec` properties.
- No CRD definition is read. Discovery (`ontology/discovery.py`) lists CRD *objects*, never their schema.
- Since 2026-11-09, facts on Argo CD and Flux were copied by hand into `ontology/spec/*.yaml` and the GitOps specs.
  They are reviewed against the registry in step 5 below.

## 1. Sources, in order

| # | Source | When | What it gives |
|---|---|---|---|
| 1 | the API server's `/openapi/v3`: an index of one document per group and version, then each document | live | every served kind, CRDs included; each schema carries `x-kubernetes-group-version-kind` |
| 2 | `CustomResourceDefinition` objects (`apiextensions.k8s.io/v1`), read explicitly since they are cluster-scoped | live, and in fixtures | `spec.group`, `spec.names.kind`, `spec.versions[].name`, `.schema.openAPIV3Schema`, `.subresources.status` |
| 3 | pinned upstream copies, fetched by `tools/fetch_specs.py` into `ontology/spec/upstream/` | offline, fixtures, air-gapped clusters | the same documents as 1 and 2, at a stated version |
| 4 | a chart's own `values.schema.json`, read with the chart (`knowledge/chart_store.py`) | when the chart has one | the values' types, enums and defaults |

**Pinned copies (source 3) are a bundle, built by a script, as for an air-gapped install.** The product never
downloads a definition at run time. `tools/fetch_specs.py` runs on a machine with network access, writes a bundle
directory with its lock, and the bundle is carried to where the product runs (copied, mounted or built into the
image). The product reads the bundle from a configured directory, and `--verify` checks a bundle against its lock
with no network. The sources and their tags are data (`ontology/spec/upstream/sources.yaml`); `latest` is resolved
once at fetch and written as a tag in the lock.

One entry per source in `ontology/spec/upstream/lock.yaml`: the URL, the release tag,
the sha256 of each file, the licence read from the repository's `LICENSE`, and the fetch date. The tool refuses a
file whose sha256 differs from the lock unless asked to update it. The initial set:
- Kubernetes: `api/openapi-spec/v3/*.json` of `kubernetes/kubernetes` at the minor version of the local cluster
  (`docs/live-k3s.md`: v1.35);
- Argo CD: the CRDs in `manifests/crds/` of `argoproj/argo-cd`, at a release tag;
- Flux: the CRDs in `config/crd/bases/` of `fluxcd/kustomize-controller`, `fluxcd/helm-controller` and
  `fluxcd/source-controller`, at release tags;
- cert-manager: its CRDs, at a release tag (S2's w2).

The tags are chosen once, as the versions the live check on k3d installs, and written in the lock. A path that does
not exist at a tag stops the tool: no file is guessed.

Helm itself defines no CRD: a release is read through `helm` (`ingestion/helm_collector.py`), and a chart's values
through source 4.

## 2. The registry

`ontology/schema_registry.py`:
- `SchemaRegistry.from_openapi_v3(docs)`, `.from_crds(objects)`, `.from_upstream(dir)`, merged in the order of § 1:
  a live definition replaces a pinned one of the same group, version and kind.
- `field(group, kind, path) -> FieldSchema | None`, with `path` in the dotted form of `ontology/declared_relations.py`
  (`spec.containers[].resources.limits`). `$ref`, `allOf` and nested `items` are resolved.
- `FieldSchema`: `type`, `enum`, `default`, `required`, `description`, `list_type`, `list_map_keys`
  (`x-kubernetes-list-type`, `x-kubernetes-list-map-keys`), `preserve_unknown`
  (`x-kubernetes-preserve-unknown-fields`), `int_or_string`.
- `kinds()`, and `has_status(group, kind)`, `has_conditions(...)`, `has_observed_generation(...)`.
- `version()`: the sources it was built from, with their sha256, recorded in each diagnosis like the other
  versions.

## 3. What the code derives from it

| Derived | From | Used by |
|---|---|---|
| `valid` anchor | an `enum` | the anchor engine; and the fixture check (a stored object cannot hold a value outside it, since the API server refuses it at write) |
| `default` anchor | a `default` | the applied-vs-live comparison: a live field equal to its default and absent from the manifest is not a drift |
| list keys | `list_map_keys` | `ontology/managed_fields.py`, to read `k:` entries by their documented keys |
| kstatus applicability | `status.conditions` and `status.observedGeneration` present | the conventions in `ontology/anchors.py`, instead of the group list in `conditions.yaml` |
| path check | every path in `ontology/spec/relations.yaml` and `anchors.yaml` | a test: each declared path exists in the schema of its kind, or the entry is refused |

**Not derived, because no definition holds it:** the polarity of a condition (the API conventions say so), the values
a tool computes at run time (Argo CD's health), and field manager names. They stay declared, each with its source, or
are confirmed live.

## 4. Fixtures are validated against it

`tools/validate_fixtures.py` checks every object of a case's `kube/` files against the registry built from the
pinned copies: kind known for its `apiVersion`, every field known (unless under `preserve_unknown`), every enum
respected, required fields present. It runs on S1, S2, S2b and S2-GitOps. A frozen case that fails is **reported, not
silently fixed**: each failure is listed with its case, and a fix is a dated amendment of that case.

The GitOps fixtures hold the Argo CD and Flux CRD objects copied from the pinned files, so the product reads their
definitions by source 2, as in production.

## 5. Review of the facts copied by hand

Each entry of `ontology/spec/relations.yaml`, `anchors.yaml`, `conditions.yaml` and the GitOps specs is classed:
- **given by the definition:** the entry is removed, or kept only as a path the test above checks;
- **not in the definition:** kept, with its source and a line on why the definition lacks it;
- **not confirmed:** removed until a live run shows it.

The result is a table in this file, dated.

## 6. Measurement, rule written before the numbers

- **Unit:** each derivation tested on small schemas written for the test, with mutants (an enum dropped, a `$ref` not
  followed, a list key read from the wrong level).
- **Prediction, written now:** the schema detects **no fault by itself** on S2 (the API server refuses invalid values
  at write). Its effect is elsewhere: the fixture check, the path check, defaults no longer read as drifts, and
  kstatus decided from the definition. The prediction is refuted if a schema-derived anchor names a cause on S2.
- **Fixture check:** the number of S1, S2 and S2b objects that fail, per reason. The prediction is that S2b's
  externally authored fixtures fail more than S2's.
- **Guards:** S1 stays 19/19, the S2 guards hold, `make ci` green.

## 7. Order

1. Review this spec.
2. `tools/fetch_specs.py` and the lock, then one fetch of the pinned copies, on its own go (it downloads from
   GitHub).
3. `ontology/schema_registry.py`, with its tests.
4. The path check on the declared files, and the review of § 5.
5. `tools/validate_fixtures.py`, run on S1, S2 and S2b; results recorded here.
6. The anchor engine, the applied-vs-live comparison and `managed_fields` read the registry; `k8s_schema.py`'s
   hand-written `_SCHEMA` is removed once the guards hold without it.
7. The GitOps cases, read through the registry.

## Step 2 done, 2026-11-09: the script and the first bundle

- `tools/fetch_specs.py`, the sources as data (`ontology/spec/upstream/sources.yaml`), 9 tests with the HTTP layer
  faked, 5 mutants killed (CRD filter, lock refusal, extra file, tag resolution, asset name).
- First fetch: Kubernetes v1.35.5 (64 OpenAPI v3 documents), Argo CD v3.6.1 (3 CRDs; `kustomization.yaml` skipped,
  not a CRD), Flux kustomize-controller v1.10.1, helm-controller v1.7.1, source-controller v1.10.1, cert-manager
  v1.21.3. All Apache-2.0 per the repositories. 14 MB, 1.4 MB compressed. `--verify` passes offline.

**Read from the bundle, before the registry exists, on the points marked *to confirm*:**
- Argo CD `Application` (v1alpha1): `status.health.status`, `status.sync.status` and `status.operationState.phase`
  are plain strings with **no enum**. Their values stay declared, as predicted (§ 3). `spec.syncPolicy.automated.selfHeal`
  is documented in the CRD as reverting resources to their desired state.
- Flux `Kustomization` (v1): the inventory id is `'<namespace>_<name>_<group>_<kind>'`, the version in a separate
  `v`. That is what `flux-id` parses.
- Flux `HelmRelease` (v2): **no `lastAppliedRevision`**; it has `lastAttemptedRevision`, `history[]` (with
  `chartVersion`, `status`, `action`), and **an `inventory`** of the same form as the Kustomization's. Two corrections
  follow for `docs/gitops-managed-by.md`: the anchor `flux.applied-is-attempted` cannot apply to HelmRelease as
  written, and HelmRelease's inventory can be declared for `MANAGES`. Both go through the review of § 5.
- Conditions of both Flux kinds: `status` has the enum `True`, `False`, `Unknown`; types and reasons have none, so
  polarity stays declared.
