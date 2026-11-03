# The failing cases — causes and solutions

> Spec, 2026-10-20. Asked: "you remove the failing tests — analyse them and find solutions".
> After fix plan 001 the structured diagnosis passes 13 of 18 native cases; this spec takes each
> case that fails (and h011, retired too early) to its cause and fixes a solution before any code.
> Rules: each solution is measured on the case it targets **and** on the negative cases (h022,
> h026) and the decoys it could hit; a solution that passes its case by breaking another is not
> kept. A fixture is changed only where it is unrealistic, and the change is listed in
> `docs/case-audit-001.md`.

## What each case lacks (measured 2026-10-20)

| Case | Signals extracted | Diagnosis | Cause of the failure |
|---|---|---|---|
| h005 RBAC | none | `no_incident` | the pod is Ready, so its logs are never read (H49) |
| h006 NetworkPolicy | `dns.failed`, `dependency.unreachable`, `pod.not_ready`, `probe.readiness_failed` | `insufficient_evidence` | NetworkPolicies are not collected; no rule links a blocking policy to the failures |
| h010 quota | `quota.exceeded`, `rollout.progressing` | `insufficient_evidence` | the signal exists, **no rule reads it** |
| h013 SLO burn | `slo.burn` | `insufficient_evidence` | the dependency is named only in an alert's prose (and that prose states the answer); no signal names a slow upstream |
| h016 connections | `db.connections_exhausted`, `db.pool_timeout`, `db.connect_failed`, … | `insufficient_evidence` | no rule for connection exhaustion |
| h011 PVC | `dependency.missing` (wrong) | PASS for a wrong cause | the fixture has no PVC / PV, so the PVC reads "missing"; no cause concepts, so the wrong cause passes |

## Solutions

### S1 — h005: read the logs of the workload the query names (H49, option chosen 2026-10-20)

`LokiSource` reads the logs of pods that need telemetry **and** of the pods of a workload the
query names (Deployment, StatefulSet, DaemonSet or Helm release name found as a word in the
query, in the investigated namespaces), within `LOKI_MAX_LOGS_PER_POD`. The query is passed to
the collector by the workflow's `otel` node.
- h005's query names `audit-exporter`: its pod's logs carry the klog `forbidden` lines (H50) →
  `rbac.denied` → rule `rbac` on `ServiceAccount/monitoring/audit-exporter-sa`.
- Control: h022 and h026 (healthy, their queries name a workload) stay `no_incident` — reading
  healthy logs adds no fault signal; h016's decoy `catalog-api` (Ready, error lines) is not named
  by its query and stays unread.

### S2 — h010: rule `quota.exceeded`

A `quota.exceeded` signal (the replicaset controller's `exceeded quota: <name>`) is the cause,
located on `ResourceQuota/<ns>/<name>`; strengthened by a Deployment of the same namespace that
is not available (`ReplicaFailure`, 0 ready). Checks: `kubectl describe resourcequota <name>`.
- Control: no `quota.exceeded`, no diagnosis (h022, h026).

### S3 — h016: rule `db.connections_exhausted`

`db.connections_exhausted` (alert, log or trace) on a workload, with `db.pool_timeout` or
`db.connect_failed` from the same workload, is the cause "the database's connection limit is
reached by the workload's pools", located on the database named by the alert (`outside:<db>`
when it is not in the graph); the text names the workload's replica count read from its
Deployment. Strengthened by the workload's not-ready pods.
- Control: h016's decoys (a staging alert, a Ready `catalog-api`) do not become causes; a
  `db.connect_failed` alone (no exhaustion) does not fire the rule.

### S4 — h006: NetworkPolicies collected, rule `netpol.egress_blocked`

`K8sCollector` lists NetworkPolicies per namespace (read-only, `networking.k8s.io/v1`) and
passes them to `ingestion.readiness.detect_missing` (`Present.networkpolicies`), as the offline
loader did before H14; a refused listing is a listing failure (H24). New signal
`netpol.egress_blocked` from the existing `netpol.*` annotation. Rule: a pod selected by a
NetworkPolicy whose egress allows neither DNS (53) nor the ports it fails to reach, with
`dns.failed` / `net.timeout` / `dependency.unreachable` from that pod, is the cause, located on
`NetworkPolicy/<ns>/<name>`. *Implemented for a policy with an empty egress (allows nothing),
  the case the existing detector flags; a policy that allows some ports but not DNS is not read
  yet.*
- Control: a NetworkPolicy that allows DNS and the port does not fire; DNS failures without a
  selecting policy do not fire (stay `dependency.unreachable`). The chart and `k8s/rbac.yaml`
  grant `list networkpolicies` (H45 family).

### S5 — h013: a slow-upstream signal, rule `slo.dependency`, fixture without the answer

- Fixture: the `LatencySLOBreach` description loses the sentence that states the cause; the
  evidence of the slow upstream is what a RED / mesh setup exposes — an alert
  `UpstreamLatencyHigh{deployment="checkout-api", upstream="payments-gateway"}` (p99 per
  upstream). Listed in the audit.
- Signal `upstream.slow` from an alert carrying an `upstream` (or `destination_service`) label.
- Rule: `slo.burn` on a workload plus `upstream.slow` from the same workload naming D, while
  the workload's pods are healthy (no lifecycle signal), is the cause "calls to D are slow",
  located on `outside:<D>` (or D's object when collected).
- Control: an SLO burn without an upstream signal stays `insufficient_evidence`; the decoy
  `reporting-batch` alert (not in the snapshot) is out of scope.

### S6 — h011: fixture completed, concepts, rule `volume.node_affinity`

- Fixture: the PVC `data-kafka-2` (Bound) and its PV (node affinity to `worker-node-01`), the
  node `worker-node-01` cordoned; the invented event message ("bound to node … which is cordoned
  and being drained") replaced by what the scheduler writes (`1 node(s) had volume node affinity
  conflict`); cause concepts `volume node affinity|node affinity|bound to` · `pvc|persistentvolume`.
- Signal `scheduling.volume_affinity` from that scheduler message; rule: the pod's PVC is bound
  to a PV pinned to a node that cannot take the pod (cordoned / not ready) → cause on the PV.
- Control: a PVC that is really absent stays `dependency.missing` (h001-style); h011 back in the
  graded cases.

## Order and measurement

S2, S3 (rules on signals that exist) → S1 → S6 → S4 → S5. After each: the structured replay on
all cases, the reasoning-path tests, h022 / h026 unchanged; the number reported with the case
list it was measured on.

## Results (2026-10-21)

| Solution | Case | Before → after | Rule fires elsewhere? |
|---|---|---|---|
| S2 quota | h010 | FAIL → PASS | no |
| S3 connections | h016 | FAIL → PASS (`outside:postgres-primary`, "8 replicas") | no |
| S1 named workload (H49) | h005 | FAIL → PASS (`rbac` on the ServiceAccount) | — (h016's Ready decoy stays unread) |
| S6 fixture + PVC named | h011 | wrong PASS → PASS for the right cause; the old answer now FAILs | — |
| S4 NetworkPolicy | h006 | FAIL → PASS | no |
| S5 slow upstream | h013 | FAIL → PASS (`outside:payments-gateway`) | no |

Structured replay: **19/19**, h022 and h026 still `no_incident`. What this does and does not say:
each of the four new families is checked on **one** native case plus unit controls that can
fail; it says these cases are diagnosed for the right reason, not that the families generalise.
Next evidence: more cases per family, and the live validation.
