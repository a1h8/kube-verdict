# Incident corpus — the format of a resolved incident

> Spec 2026-10-17, not implemented. Why: the resolved-incident memory (`data/examples/`)
> held only 64 copies of one record written by a unit test, and a record had no field to
> say where it came from. Measured by [protocol-001-retrieval.md](protocol-001-retrieval.md).

## Purpose

Given the symptoms of a new incident, the corpus alone, without the LLM, should already
point toward a cause. So a record separates **what the on-call sees** (what retrieval
matches on) from **what the cause was** (what it returns).

## One record = one YAML file

`corpus/incidents/<id>.yaml`, committed. `id` = `inc-NNNN`, never reused.

```yaml
id: inc-0007
title: CoreDNS crash-loop breaks name resolution in other namespaces
family: dns                      # controlled list, below
origin: synthetic                # synthetic | public | enterprise
source:
  kind: legacy-case              # legacy-case | runbook | failure-story | approved-run
  ref: cases/017_dns_failure     # repo path or URL
  licence: Apache-2.0            # licence of the source; "facts-only" for a story we summarize

# --- what the on-call sees (indexed for retrieval) -----------------------------
symptoms:                        # observable, in the words of an alert or kubectl
  - billing-service pods log "lookup payments.production.svc.cluster.local: i/o timeout"
  - 5xx on several services at once
  - coredns pods CrashLoopBackOff in kube-system
signals: [k8s_events, loki]      # k8s_events | prometheus | loki | otel | helm | gitops
kinds: [Pod, Deployment, ConfigMap]

# --- what it was (returned, not indexed) ---------------------------------------
root_cause: >-
  A bad Corefile in the coredns ConfigMap makes CoreDNS crash; every service that
  resolves names fails.
cause_location: kube-system/ConfigMap/coredns   # where the cause is, often not where the symptom is
looks_like: [application bug, network policy]   # plausible wrong causes (decoys)
evidence:                        # what told the cause apart from the decoys
  - coredns container log "plugin/forward: not an IP address or file"
remediation:
  - kubectl -n kube-system edit configmap coredns
rollback:
  - kubectl -n kube-system rollout undo deployment/coredns
```

## Fields

| Field | Required | Rule |
|---|---|---|
| `id`, `title`, `family`, `origin`, `source` | yes | `source.ref` must resolve (path exists, or a URL) |
| `symptoms` | yes, ≥ 2 | observable facts only; **no cause words** (a symptom that names the cause leaks the answer) |
| `signals`, `kinds` | yes | from the lists above / Kubernetes kinds |
| `root_cause`, `cause_location` | yes | one cause per record; a multi-cause incident is two records |
| `looks_like`, `evidence` | `evidence` yes, `looks_like` optional | |
| `remediation` | yes | read-only checks are not remediation |
| `rollback` | yes, or `[]` with a reason in `rollback_note` | |

`family` (controlled, extended only by editing this file): `crashloop-dependency`,
`image-pull`, `oom`, `config-missing`, `rbac`, `network-policy`, `scheduling`,
`quota`, `probe`, `init-container`, `storage`, `hpa`, `dns`, `certificate`, `etcd`,
`node`, `rollout`, `gitops-drift`, `helm-drift`, `slo`, `crashloop`, `dependency`, `job`,
`resources`, `control-plane`, `negative` (the last six added 2026-10-17 with the coverage
matrix, `docs/diagnosis-coverage.md`).

## Sources, and what each may contain

| `source.kind` | `origin` | Content rule |
|---|---|---|
| `legacy-case` — `cases/0NN_*`, `cases/helm_cases/*` | synthetic | rewritten from the case's notes; names and values changed |
| `runbook` — prometheus-operator/runbooks (Apache-2.0) | public | alert → symptoms, documented causes; attribution kept in `source` |
| `failure-story` — k8s.af and the posts it links | public | **our own factual summary**, no copied text; `licence: facts-only`, link in `ref` |
| `approved-run` — `save_example_node` after a human-approved fix | enterprise | written by the workflow, not committed by default |

## Kept apart from the test cases

The 18 native cases (`tests/integration/cases/h0NN_*`) are what the corpus is measured
on, so the corpus must not contain them:

- no record's `source.ref` points into `tests/integration/cases/`;
- no record's `symptoms` reuses a native case's object names (pod, deployment,
  namespace) or its `expect.json` query;
- a record may share a **family** with a case (a past OOM helps diagnose a new OOM):
  that is what a corpus is for.

A unit test enforces these three rules and the required fields on every record.

## How it is used

- **Indexing:** each record becomes one vector-store entry built from `title`,
  `symptoms`, `signals` and `kinds`, with `doc_source = "example"` and its `origin`.
  `root_cause` and `remediation` are stored with it but not embedded.
- **Retrieval-only solver** (protocol 001): the root cause of the top-ranked record is
  the answer, graded like any other.
- **With the LLM:** the top records enter the prompt as "similar past incidents".
- The existing `ResolvedIncident` (approved runs) is mapped onto this format; the
  `example_lookup` short-circuit stays off during measurement.

## Not decided here

How many records per source, and which failure stories, are listed when the corpus is
built; the corpus is frozen (hashed) before protocol 001 runs C3.
