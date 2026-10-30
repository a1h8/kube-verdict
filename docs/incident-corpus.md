# Incident corpus — the format of a resolved incident

> Spec 2026-10-17, amended 2026-10-24 (below, before any record is written); corpus v1 written
> 2026-10-24 (11 records, `corpus/`), not yet used by R0 or C3. Why: the resolved-incident memory (`data/examples/`)
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

## Amendment 2026-10-24 — corpus v1, before any record is written

Written before the exclusion script, before any record and before any run of C3 or R0. It
aligns this spec with protocol 001 (amendment 2026-10-23) and fixes what the corpus is measured
against. Where it differs from the sections above, it replaces them.

**The cases.** The 19 native cases frozen by protocol 001, not 18.

**Exclusion — replaces *Kept apart from the test cases*.** A candidate is excluded if:

1. its source is a legacy case with the name of a native case (`001_crashloopbackoff`,
   `002_imagepullbackoff`, `003_oomkilled`);
2. it names a resource present in a native case's fixtures: a workload, a pod-name prefix, a
   ConfigMap, a Secret, a Service, or a namespace — except the generic namespaces `default`,
   `production`, `staging`, `monitoring` and `kube-system` (a closed list, extended only by a
   dated amendment), which name an environment, not a case
   (`docs/comparison-protocol-issues.md` CPI-20); a namespace particular to a case, such as
   h011's `data`, excludes;
3. one of its observable lines (a log line, an event message) appears verbatim in a native
   case's fixtures;
4. its `source.ref` points into `tests/integration/cases/`.

Rules 2 and 3 are applied **to the source before it is rewritten**, and to the record after:
renaming the resources of a legacy case does not bring back a case the rules excluded.
`tools/corpus_exclusions.py` applies them and writes `corpus/EXCLUSIONS.md` — every candidate,
kept or excluded, with the rule and the matching text — published with the corpus. A record of
the same family as a native case is kept: recognising a known kind of incident is what the
memory is for. A unit test runs the script on the committed corpus and fails on any record a
rule excludes.

**Sources of v1.** Legacy cases only (`cases/0NN_*`, `cases/helm_cases/*`), `origin:
synthetic`: one record per kept case, two for a case with two causes. Runbooks and failure
stories are a later version (v2), with its own hash and its own runs.

**Indexing follows the product** (`docs/comparison-protocol-issues.md` CPI-19). C3 and R0 index
the corpus through the product's own path, `ExampleIndexer`, which embeds the whole incident:
each record maps onto a `ResolvedIncident` with `id` = the record's id, `query` = the title and
the symptoms, `root_cause`, `remediation`, `entity_kinds` = `kinds`, and the other fields empty.
The rule "`root_cause` and `remediation` are stored but not embedded" (*How it is used*) is
therefore **not** what is measured; changing the product to follow it is a separate, measured
change.

- **C3**: the corpus is loaded into the run's temporary example directory, nothing else; the
  `example_lookup` short-circuit stays off.
- **R0**: an index built from the corpus alone, searched with the case's `query`; the
  `root_cause` of the top-ranked record is the answer, graded like any other. One run
  (deterministic).

**Freezing.** The corpus hash is the SHA-256 of the record files, sorted by name, computed
before the first run of C3 or R0 and written in every record of both (`corpus_hash`). A corpus
changed after that is a new version; its runs are redone.

**Families of the native cases** (protocol 001: results split into cases whose family the
corpus covers and cases it does not; `docs/comparison-protocol-issues.md` CPI-18). Taken from
`docs/diagnosis-coverage.md`, fixed here before any record is written:

| Family | Native cases |
|---|---|
| `config-missing` | h001, h004, h024 (with `oom`) |
| `image-pull` | h002 |
| `oom` | h003, h024 (with `config-missing`) |
| `rbac` | h005 |
| `network-policy` | h006 |
| `hpa` | h007 |
| `init-container` | h008 |
| `probe` | h009 |
| `quota` | h010 |
| `storage` | h011 |
| `gitops-drift` | h012 |
| `slo` | h013 |
| `certificate` | h014 |
| `etcd` | h015 |
| `dependency` | h016 |
| `negative` | h022, h026 |

A case is **covered** when at least one kept record has its family; h024 when both of its
families are. h022 and h026 are the negative controls: reported apart, not counted in either
group.

**Known limits, stated before the numbers.** The v1 corpus is small (one record per kept legacy
case) and synthetic, and it was written by the authors of the native cases, in the same style:
that favours retrieval. A gain of C3 or R0 on covered cases is read as an upper bound, not as
what an enterprise's own incident history would give. The decision rules of protocol 001 do not
change.

**Written (2026-10-24).** `corpus/incidents/inc-0001.yaml` … `inc-0011.yaml`, one per kept source
(`corpus/EXCLUSIONS.md`, generated). `tools/incident_corpus.py` holds the checks fixed before the
records — required fields, controlled family, a source that resolves, at least 2 symptoms, a
remediation that changes something, a rollback, and **no cause word in a symptom** (a list for
every family and one per family) — and the corpus hash. `tests/unit/test_incident_corpus.py`
runs them and the exclusion script on every record. One record was reworded when the check
refused it ("the container was never created", a cause word for `config-missing`); no rule was
changed for a record. Families: storage, scheduling, oom, config-missing (2), probe,
init-container, node, quota, rbac, helm-drift. Covered native cases: h001, h003, h004, h005, h008,
h009, h010, h011, h024; not covered: h002, h006, h007, h012, h013, h014, h015, h016.
