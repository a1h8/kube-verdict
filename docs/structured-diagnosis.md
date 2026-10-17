# Structured diagnosis — typed signals, typed diagnoses, the LLM optional

> Spec 2026-10-17, not implemented. Review before any code.
> Why: `docs/case-audit-001.md`, `docs/reasoning-path-tests.md`. The deterministic part works
> (graph, Helm drift, evidence reaching the prompt); what fails is that evidence enters the
> pipeline as text, the rules are few (4/19 cases alone) and read text, and the LLM is asked
> to decide. The direction (2026-10-17): structure the signals and the diagnoses; the LLM
> words a diagnosis, it does not make it.

## Principle

```
collectors ──► signals (typed) ──► diagnoses (typed, ranked, scored) ──► report
                                                         └──► LLM (optional): wording only
```

Every step is deterministic and replayable: the same graph gives the same signals, the same
signals give the same diagnoses. A diagnosis names a cause **family**, the object where the
cause is, the signals that support it and those that contradict it — the "typed gap between
declared and observed" applied to the whole diagnosis.

## 1. Signals

A signal is one observed fact, typed, attached to one entity, pointing to its raw evidence.

```python
@dataclass(frozen=True)
class Signal:
    kind: str            # controlled list below
    entity: str          # fqn, e.g. Pod/production/billing-gateway-5b7d9c8f6-4hxkd
    source: str          # k8s_event | k8s_status | helm | prometheus | loki | otel | graph
    attrs: dict          # kind-specific fields (container, reason, field, declared, observed…)
    observed_at: str     # ISO time when known, "" otherwise
    evidence: str        # uid of the raw entity or annotation (event, log line, span, drift item)
```

**Extractors**, one per source, deterministic (fields first, then fixed patterns on
messages). A raw item that matches no pattern produces no signal and is **counted**
(`unclassified` per source), so a growing unclassified count shows where the vocabulary is
missing.

| Kind | Source → how it is read | Cases |
|---|---|---|
| `container.crashloop` | status: `state.waiting.reason=CrashLoopBackOff` | h001, h003, h009, h024 |
| `container.oom_killed` | status: `lastState.terminated.reason=OOMKilled` | h003, h012, h024 |
| `container.config_error` | status: `waiting.reason=CreateContainerConfigError` | h004, h024 |
| `dependency.missing` | `missing.<kind>.<name>` (ingestion/readiness.py); event `"<kind> \"x\" not found"` | h001, h004, h024 |
| `image.pull_failed` | status `ImagePullBackOff`/`ErrImagePull`; event `Failed to pull image` (attrs: image, code 401/404) | h002 |
| `init.failed` | status `initContainerStatuses[].state.terminated.exitCode≠0` | h008 |
| `probe.readiness_failed` / `probe.liveness_failed` | event `Unhealthy` + "Readiness/Liveness probe failed" (attrs: target, error) | h009, h014–h016 |
| `pod.not_ready` | `Pod.is_not_ready` (not in a clean rollout start) | h014–h016 |
| `rollout.progressing` / `rollout.stalled` | Deployment conditions | h026 |
| `drift.config` | `drift.*` (attrs: field, declared, observed, release) | h002, h003, h009, h016, h024 |
| `scheduling.failed` | event `FailedScheduling` (attrs: reason class: cpu, affinity, volume, taint) | h010, h011 |
| `quota.exceeded` | event `FailedCreate … exceeded quota` | h010 |
| `rbac.denied` | log/event `is forbidden: User "system:serviceaccount:…" cannot` | h005 |
| `hpa.metrics_unavailable` | event `FailedGetResourceMetric` | h007 |
| `tls.cert_expired` | log `x509: certificate has expired` | h014 |
| `net.connection_refused` / `net.timeout` / `dns.failed` | log/event patterns | h006, h016 |
| `db.connections_exhausted` | log `remaining connection slots are reserved`; alert `*ConnectionsSaturated` | h016 |
| `etcd.slow` | span error `etcdserver` / `DeadlineExceeded` on `etcdserverpb.*` | h015 |
| `slo.burn` | alert `SLOErrorBudgetBurn*`, `LatencySLOBreach` (attrs: dependency from labels) | h013 |
| `restart.past` | restartCount > 0 with the container ready now (context only, never alone a cause) | h022 |
| `event.past` | a container-lifecycle warning (crash, OOM, probe, pull) about a pod Running and ready now (context only); other warnings stay current | h022 |
| `rbac.unbound` | `missing.rbac.<sa>`: the ServiceAccount exists but no (Cluster)RoleBinding names it | h005 |
| `cert.issuer_failed` | cert-manager event `ErrRegisterACMEAccount`, `ErrInitIssuer` | h014 |
| `dependency.unreachable` | log `<target> unreachable` (attrs: target) | h014, h016 |
| `db.pool_timeout` | log `pool timeout … db=<name>` | h016 |
| `db.connect_failed` | error span `db.connect` | h016 |
| `container.exited` | event `container X in pod Y exited with code N` | h008 |
| `app.error_rate` | alert `*HighErrorRate`, `5xx` | h016 |

Not a signal: the synthetic PatchTST series (`docs/patchtst-readiness.md`) — a real series
from Prometheus may become one later, measured on its own case.

**First extraction (2026-10-17), on the 19 native cases.** Three findings:
- **S1 — a missing ServiceAccount on every case.** `ingestion/readiness.py` flagged
  `missing.serviceaccount.<sa>` whenever the ServiceAccount was not in the graph; fixtures do
  not ship it, so every case — the healthy h022 and h026 included — told the model a
  ServiceAccount was missing. A pod that exists proves its ServiceAccount existed (the
  admission plugin rejects it otherwise; the ReplicaSet reports `FailedCreate`). Fixed: never
  inferred from a pod.
- **S2 — a past event read as present.** h022's 3-hour-old BackOff produced
  `container.crashloop` though the container is running and ready. Fixed: a container-lifecycle
  warning about a pod that is healthy now becomes `event.past`. A first version applied it to
  every warning and turned h005's RBAC denials into history; a denial on a running app is a
  current failure, so only lifecycle warnings are superseded by the pod's state.
- **S3 — missing vocabulary**, shown by the unclassified counts: cert-manager issuer events,
  "<target> unreachable" readiness logs, pool timeouts, `db.connect` error spans. Added above.

## 2. Diagnoses

```python
@dataclass
class Diagnosis:
    family: str                 # controlled list of docs/incident-corpus.md
    cause: str                  # one sentence built from the signals' attrs (template per rule)
    cause_location: str         # fqn, or "absent:<fqn>", or "outside:<fqn>"
    supports: list[str]         # signal ids
    contradicts: list[str]      # signal ids that argue against it
    missing: list[str]          # signal kinds the rule expects but did not see
    checks: list[Check]         # read-only, from rca/diagnostics.py
    score: float                # evidence score (docs/evidence-score.md), not an LLM label
    rule: str
```

A **rule** is data plus one function: the family, the signal kinds it **requires**, those
that **strengthen** it, those that **contradict** it, how it resolves `cause_location`
(follow `MOUNTS_SECRET`, `MANAGED_BY_HELM`, `OWNS`…), its cause template and its checks.
Example — `dependency.missing`:

```
requires     container.config_error | container.crashloop  on Pod P
             dependency.missing                             on P (or absent:Secret/ns/x)
strengthens  event "<kind> \"x\" not found"
contradicts  the dependency exists in the graph
location     absent:<kind>/<ns>/<name>
cause        "{kind} {name} referenced by {pod} does not exist"
checks       kubectl get <kind> <name> -n <ns>
```

**Ranking.** By score; ties by rule priority then fqn — deterministic. Several diagnoses can
hold at once (h024: two families on two objects), and the report keeps every diagnosis whose
score passes the threshold, not only the first.

**Abstention.** No diagnosis above the threshold → `no_incident` when no fault signal
exists (h022, h026), `insufficient_evidence` when fault signals exist but no rule explains
them. Both are answers, graded as such.

**Decoys.** A signal that matches a rule's `contradicts` lowers it; a rule that fires on a
signal whose entity is outside the incident's seeds and their reached context is dropped
(h016's catalog-api).

## 3. The LLM

Given the ranked diagnoses (family, cause, location, the supporting raw evidence), the LLM
writes the explanation. It may not change the family, the location or the ranking; if its
text names another cause, the structured diagnosis stands and the disagreement is recorded.
Measured separately (protocol 001, C1 vs structured).

## 4. Measurement

- A new solver `structured` in `tools/veracity_replay.py`: the answer is the top
  diagnoses' cause sentences, graded with the same cause concepts; deterministic, 1 run.
- The reasoning-path tests gain two steps: **signals** (the case's expected signal kinds are
  extracted) and **diagnosis** (family + location), declared per case in `reasoning_path`.
- Reported next to `rules` (today 4/19) and the LLM runs. No target number is set in advance;
  each family added is measured on the cases it concerns, and a family that does not improve
  its cases without breaking a negative case is not kept.

## 5. Order

1. `Signal` + extractors for what the graph already holds (status, events, drift, missing,
   Deployment conditions) — no new collection. Test: expected signal kinds per case.
2. Diagnosis model, score, abstention; port the 7 existing rules onto signals.
3. Families by case: `dependency.missing` (h001, h004, h024), `config.drift` (h003, h009,
   h024), `init.failed` (h008), `quota` (h010), `hpa` (h007), then telemetry families
   (h013–h016) once Loki/OTel lines are signals.
4. `structured` solver and the report; the LLM as explainer last.

## Not in scope

New collectors (ontology gaps D1.x are separate), retrieval (protocol 001), remediation
changes beyond attaching the checks each rule already declares.
