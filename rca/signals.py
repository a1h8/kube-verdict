"""Typed signals on the correlation-spine model — docs/structured-diagnosis.md § 1.

The model is the one reconcileflow uses for its evidence (immutable raw evidence, a
record pointing to it, versioned normalisation — its docs/provenance.md): three layers,
never mixed —

  RAW EVIDENCE   the graph entity or annotation, untouched      → ``payload_ref``
  SIGNAL         a minimal envelope: what was observed and when, separating what the
                 source *authored* from what the extractor *observed* in it
  DERIVED EDGES  which entity the signal is about, with the method and confidence of that
                 attribution, versioned — kept apart from the signal

Extraction is deterministic: fields first, then fixed patterns. A raw item that matches
no pattern produces no signal and is counted in ``SignalSet.unclassified``.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ontology.entities import ResourceKind
from ontology.graph import OntologyGraph
from ontology.relationships import RelationshipType

EXTRACTOR_VERSION = "signals/1"
CORRELATOR_VERSION = "attribution/1"

PLANS = {"k8s_status": "k8s", "k8s_event": "k8s", "helm": "helm", "gitops": "helm",
         "loki": "telemetry", "otel": "telemetry", "prometheus": "telemetry"}


def _pairs(d: dict) -> tuple:
    return tuple(sorted((k, str(v)) for k, v in d.items() if v not in (None, "")))


@dataclass(frozen=True)
class Signal:
    """One observed fact. ``authored``: written at the source by whoever knows it (an
    event's involvedObject, a log stream's labels, an alert's labels). ``observed``: what
    the extractor read in the raw item (reason, container, field, values, error code)."""
    signal_id: str
    kind: str
    plan: str                 # k8s | helm | telemetry
    source: str               # k8s_status | k8s_event | helm | loki | otel | prometheus
    event_time: str           # when it happened, ISO; "" when the raw item does not say
    authored: tuple
    observed: tuple
    payload_ref: str          # uid of the raw entity, or "<uid>#<annotation key>"
    extractor_version: str = EXTRACTOR_VERSION

    def attr(self, key: str, default: str = "") -> str:
        return dict(self.observed).get(key, dict(self.authored).get(key, default))


@dataclass(frozen=True)
class DerivedEdge:
    """Which entity a signal is about — a derived fact, with how it was derived."""
    signal_id: str
    entity: str               # fqn
    method: str               # self | event.involvedObject | loki.stream_labels | …
    confidence: float
    correlator_version: str = CORRELATOR_VERSION


@dataclass
class SignalSet:
    signals: list[Signal] = field(default_factory=list)
    edges: list[DerivedEdge] = field(default_factory=list)
    unclassified: Counter = field(default_factory=Counter)
    # pod fqn → the fqns of what runs it (ReplicaSet, Deployment / StatefulSet / DaemonSet,
    # Helm release), read from owner references, selectors and Helm labels — never from the
    # pod's name (docs/fix-plan-001.md, H25)
    workloads: dict[str, set[str]] = field(default_factory=dict)

    def same_workload(self, pod: str, other: str) -> bool:
        return pod == other or other in self.workloads.get(pod, set())

    def kinds(self) -> set[str]:
        return {s.kind for s in self.signals}

    def of(self, kind: str) -> list[Signal]:
        return [s for s in self.signals if s.kind == kind]

    def subjects(self, signal: Signal) -> list[str]:
        return [e.entity for e in self.edges if e.signal_id == signal.signal_id]

    def subject(self, signal: Signal) -> str:
        found = self.subjects(signal)
        return found[0] if found else ""

    def about(self, entity: str) -> list[Signal]:
        ids = {e.signal_id for e in self.edges if e.entity == entity}
        return [s for s in self.signals if s.signal_id in ids]


def _iso(value) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()
    if isinstance(value, int):
        return datetime.fromtimestamp(value / 1e9, tz=timezone.utc).isoformat()
    return str(value)


class _Builder:
    def __init__(self) -> None:
        self.out = SignalSet()
        self._seen: set[str] = set()

    def add(self, kind: str, source: str, payload_ref: str, subjects: list[tuple[str, str, float]],
            *, event_time="", authored: dict | None = None, observed: dict | None = None) -> None:
        authored_t, observed_t = _pairs(authored or {}), _pairs(observed or {})
        signal_id = hashlib.sha1(repr((kind, payload_ref, authored_t, observed_t)).encode()).hexdigest()[:16]
        if signal_id in self._seen:
            return
        self._seen.add(signal_id)
        self.out.signals.append(Signal(signal_id, kind, PLANS[source], source, _iso(event_time),
                                       authored_t, observed_t, payload_ref))
        for entity, method, confidence in subjects:
            self.out.edges.append(DerivedEdge(signal_id, entity, method, confidence))


# ── message patterns (data) ─────────────────────────────────────────────────────
_EVENT_PATTERNS: list[tuple[str, re.Pattern, tuple[str, ...]]] = [
    ("dependency.missing", re.compile(r'(?P<kind>secret|configmap|persistentvolumeclaim|serviceaccount) "(?P<name>[^"]+)" not found', re.I), ("kind", "name")),
    ("dependency.missing", re.compile(r"couldn't find key (?P<key>\S+) in (?P<kind>ConfigMap|Secret) (?P<ns>[^/\s]+)/(?P<name>\S+)", re.I), ("kind", "name", "key")),
    ("image.pull_failed", re.compile(r'Failed to pull image "(?P<image>[^"]+)"(?:.*?(?P<code>401|403|404|unauthorized|not found))?', re.I), ("image", "code")),
    ("image.pull_failed", re.compile(r'Back-off pulling image "(?P<image>[^"]+)"', re.I), ("image",)),
    ("probe.readiness_failed", re.compile(r"Readiness probe failed: (?P<error>.*)", re.I), ("error",)),
    ("probe.liveness_failed", re.compile(r"Liveness probe failed: (?P<error>.*)|failed liveness probe", re.I), ("error",)),
    ("quota.exceeded", re.compile(r"exceeded quota: (?P<quota>[^,\s]+)", re.I), ("quota",)),
    ("hpa.metrics_unavailable", re.compile(r"unable to get metrics|no metrics returned from resource metrics API|invalid metrics", re.I), ()),
    ("rbac.denied", re.compile(r'is forbidden: User "(?P<user>[^"]+)" cannot (?P<verb>\w+) resource "(?P<resource>[^"]+)"', re.I), ("user", "verb", "resource")),
    ("cert.issuer_failed", re.compile(r"Failed to register ACME account|ErrRegisterACMEAccount|ErrInitIssuer|issuer .* not ready", re.I), ()),
    ("container.exited", re.compile(r"container (?P<container>\S+) in pod \S+ exited with code (?P<code>\d+)(?::\s*(?P<detail>.{1,160}))?", re.I), ("container", "code", "detail")),
    ("container.oom_killed", re.compile(r"out of memory|OOMKill", re.I), ()),
    ("container.crashloop", re.compile(r"Back-off restarting failed container (?P<container>\S+)", re.I), ("container",)),
    ("dns.failed", re.compile(r"lookup (?P<host>\S+?)(?::| ).*(?:no such host|i/o timeout)", re.I), ("host",)),
    ("net.timeout", re.compile(r"(?:connect|dial tcp)[^:]*: .*(?:timed out|i/o timeout)", re.I), ()),
]
_SCHEDULING_CLASSES = [("cpu", "insufficient cpu"), ("memory", "insufficient memory"),
                       ("volume", "volume node affinity"), ("affinity", "didn't match"),
                       ("affinity", "node affinity"), ("unschedulable", "unschedulable"),
                       ("taint", "taint")]
_LOG_PATTERNS: list[tuple[str, re.Pattern, tuple[str, ...]]] = [
    ("tls.cert_expired", re.compile(r"x509: certificate has expired", re.I), ()),
    ("db.connections_exhausted", re.compile(r"remaining connection slots are reserved|too many connections|sorry, too many clients", re.I), ()),
    ("db.pool_timeout", re.compile(r"pool timeout.*?(?:db=(?P<db>\S+))?", re.I), ("db",)),
    ("dependency.unreachable", re.compile(r"(?:upstream )?(?P<target>[\w.-]+) unreachable", re.I), ("target",)),
    ("rbac.denied", re.compile(r'is forbidden: User "(?P<user>[^"]+)" cannot (?P<verb>\w+)', re.I), ("user", "verb")),
    ("dns.failed", re.compile(r"lookup (?P<host>\S+?)(?::| ).*(?:no such host|i/o timeout)", re.I), ("host",)),
    ("net.connection_refused", re.compile(r"connection refused", re.I), ()),
    ("net.timeout", re.compile(r"i/o timeout|timed out|deadline exceeded", re.I), ()),
]
_TRACE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("etcd.slow", re.compile(r"etcdserver|etcdserverpb\.", re.I)),
    ("db.connections_exhausted", re.compile(r"remaining connection slots|too many connections", re.I)),
    ("db.connect_failed", re.compile(r"\bdb\.connect\b", re.I)),
    ("net.connection_refused", re.compile(r"connection refused", re.I)),
    ("net.timeout", re.compile(r"DeadlineExceeded|deadline exceeded|timed out", re.I)),
]
_ALERT_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("slo.burn", re.compile(r"SLOErrorBudgetBurn|LatencySLOBreach|ErrorBudget", re.I)),
    ("db.connections_exhausted", re.compile(r"ConnectionsSaturated|TooManyConnections", re.I)),
    ("app.error_rate", re.compile(r"HighErrorRate|5xx", re.I)),
]
# `gitops.<field>` written by ManifestDiffer (DriftItem.to_text()): rendered from Git vs live.
# Values may hold spaces, so declared/observed run up to the fixed ` severity=` tail.
_GITOPS_DRIFT = re.compile(r"^drift field=(?P<field>\S+) declared=(?P<declared>.*) "
                           r"observed=(?P<observed>.*) severity=(?P<severity>\S+)$")
_GITOPS_META = {"gitops.drift_count", "gitops.critical_drifts", "gitops.orphan"}
_DRIFT_TEXT = re.compile(r"field=(?P<field>\S+) declared='?(?P<declared>[^'\s\[]+)'?.*?observed='?(?P<observed>[^'\s\[]+)'?")
_ROLLOUT_REASONS = {"ReplicaSetUpdated", "NewReplicaSetCreated", "FoundNewReplicaSet"}
# signals a pod that is Running and ready now contradicts: its container lifecycle
_LIFECYCLE = {"container.crashloop", "container.oom_killed", "container.exited", "container.config_error",
              "image.pull_failed", "probe.readiness_failed", "probe.liveness_failed"}


# every kind an extractor can emit — docs/diagnosis-coverage.md checks rows against it
SIGNAL_KINDS: frozenset[str] = frozenset(
    {k for k, _rx, _g in _EVENT_PATTERNS + _LOG_PATTERNS}
    | {k for k, _rx in _TRACE_PATTERNS + _ALERT_PATTERNS}
    | {"container.crashloop", "container.config_error", "image.pull_failed", "container.oom_killed",
       "restart.past", "pod.not_ready", "rollout.progressing", "rollout.stalled", "scheduling.failed",
       "event.past", "drift.config", "rbac.unbound", "dependency.missing", "init.failed"})


def _match(patterns, text: str):
    for kind, rx, groups in patterns:
        m = rx.search(text or "")
        if m:
            return kind, {g: (m.group(g) or "") for g in groups if g in rx.groupindex and m.group(g)}
    return None


def _graph_owners(graph: OntologyGraph, uid: str, rel: RelationshipType) -> list[str]:
    return [e.fqn for e in graph.neighbors(uid, rel, reverse=True)]


# ── extractors ──────────────────────────────────────────────────────────────────

def _from_pods(graph: OntologyGraph, b: _Builder) -> None:
    for pod in graph.entities(ResourceKind.POD):
        me = [(pod.fqn, "self", 1.0)]
        for cs in pod.container_statuses:
            name = cs.get("name", "")
            state, last = cs.get("state") or {}, cs.get("lastState") or {}
            waiting = (state.get("waiting") or {}).get("reason", "")
            kind = {"CrashLoopBackOff": "container.crashloop", "CreateContainerConfigError": "container.config_error",
                    "ImagePullBackOff": "image.pull_failed", "ErrImagePull": "image.pull_failed"}.get(waiting)
            if kind:
                b.add(kind, "k8s_status", pod.uid, me, observed={"container": name, "reason": waiting})
            for term in ((state.get("terminated") or {}), (last.get("terminated") or {})):
                if term.get("reason") == "OOMKilled":
                    b.add("container.oom_killed", "k8s_status", pod.uid, me, event_time=term.get("finishedAt", ""),
                          observed={"container": name, "exit_code": term.get("exitCode", "")})
                    break
            if cs.get("restartCount") and cs.get("ready", False) and "running" in state:
                b.add("restart.past", "k8s_status", pod.uid, me,
                      event_time=(last.get("terminated") or {}).get("finishedAt", ""),
                      observed={"container": name, "restarts": cs.get("restartCount")})
        for cs in pod.init_container_statuses:
            state, last = cs.get("state") or {}, cs.get("lastState") or {}
            term = (state.get("terminated") or {}) or (last.get("terminated") or {})
            code = term.get("exitCode")
            if code not in (None, 0, "0"):
                b.add("init.failed", "k8s_status", pod.uid, me, event_time=term.get("finishedAt", ""),
                      observed={"container": cs.get("name", ""), "exit_code": code,
                                "reason": (state.get("waiting") or {}).get("reason", term.get("reason", ""))})
        if pod.is_not_ready:
            b.add("pod.not_ready", "k8s_status", pod.uid, me, observed={"starting": str(pod.is_starting).lower()})


def _from_deployments(graph: OntologyGraph, b: _Builder) -> None:
    for dep in graph.entities(ResourceKind.DEPLOYMENT):
        me = [(dep.fqn, "self", 1.0)]
        for c in dep.conditions:
            if c.get("type") != "Progressing":
                continue
            if str(c.get("status")) == "True" and c.get("reason") in _ROLLOUT_REASONS:
                b.add("rollout.progressing", "k8s_status", dep.uid, me,
                      observed={"reason": c.get("reason"), "available": str(dep.is_available).lower()})
            elif str(c.get("status")) == "False" and c.get("reason") == "ProgressDeadlineExceeded":
                b.add("rollout.stalled", "k8s_status", dep.uid, me, observed={"reason": c.get("reason")})


def _healthy_now(graph: OntologyGraph) -> set[str]:
    return {p.fqn for p in graph.entities(ResourceKind.POD)
            if not p.is_unhealthy and not p.is_not_ready and p.container_statuses}


def _from_events(graph: OntologyGraph, b: _Builder) -> None:
    healthy = _healthy_now(graph)
    for ev in graph.entities(ResourceKind.EVENT):
        if not ev.is_warning:
            continue
        involved = f"{ev.involved_kind}/{ev.namespace}/{ev.involved_name}" if ev.involved_kind else ev.fqn
        subjects = [(involved, "event.involvedObject", 1.0)]
        authored = {"involved": involved, "reason": ev.reason}
        when = ev.last_time or ev.first_time
        if ev.reason == "FailedScheduling":
            text = (ev.message or "").lower()
            cls = next((c for c, needle in _SCHEDULING_CLASSES if needle in text), "other")
            b.add("scheduling.failed", "k8s_event", ev.uid, subjects, event_time=when,
                  authored=authored, observed={"cls": cls})
            continue
        hit = _match(_EVENT_PATTERNS, ev.message)
        if hit and hit[0] in _LIFECYCLE and involved in healthy:
            # a container-lifecycle warning about a pod Running and ready now is history (h022);
            # other warnings (RBAC, DNS, quota…) stay current (h005)
            b.add("event.past", "k8s_event", ev.uid, subjects, event_time=when,
                  authored=authored, observed={"was": hit[0]})
        elif hit:
            b.add(hit[0], "k8s_event", ev.uid, subjects, event_time=when, authored=authored, observed=hit[1])
        else:
            b.out.unclassified["k8s_event"] += 1


def _from_annotations(graph: OntologyGraph, b: _Builder) -> None:
    for e in graph.entities():
        for key, text in e.annotations.items():
            ref = f"{e.uid}#{key}"
            if key.startswith("drift.") and key != "drift.helm.summary":
                m = _DRIFT_TEXT.search(str(text))
                observed = ({"field": m.group("field"), "declared": m.group("declared"),
                             "observed": m.group("observed")} if m else {"field": key[len("drift."):]})
                # the entity carrying the drift was chosen by the detector: a derived attribution
                b.add("drift.config", "helm", ref, [(e.fqn, "helm_drift.detector", 1.0)], observed=observed)
            elif key.startswith("gitops.") and key not in _GITOPS_META:
                # the render-vs-live diff: the chart from Git, rendered, against the live object
                # — written as gitops.*, which no signal read (docs/fix-plan-001.md, H28)
                m = _GITOPS_DRIFT.match(str(text))
                if m:
                    b.add("drift.config", "gitops", ref, [(e.fqn, "manifest_differ", 1.0)],
                          observed={"field": m.group("field"), "declared": m.group("declared"),
                                    "observed": m.group("observed")})
            elif key.startswith("missing.rbac."):
                b.add("rbac.unbound", "k8s_status", ref, [(e.fqn, "readiness.detector", 1.0)],
                      observed={"serviceaccount": key[len("missing.rbac."):]})
            elif key.startswith("missing."):
                _, kind, name = key.split(".", 2)
                b.add("dependency.missing", "k8s_status", ref, [(e.fqn, "readiness.detector", 1.0)],
                      observed={"kind": kind, "name": name})


def _from_logs(graph: OntologyGraph, b: _Builder) -> None:
    for log in graph.entities(ResourceKind.LOKI_LOG):
        if log.level not in ("error", "warn"):
            continue
        hit = _match(_LOG_PATTERNS, log.log_line)
        if not hit:
            b.out.unclassified["loki"] += 1
            continue
        owners = _graph_owners(graph, log.uid, RelationshipType.HAS_LOG) or [log.fqn]
        b.add(hit[0], "loki", log.uid, [(o, "loki.stream_labels", 1.0) for o in owners],
              event_time=log.timestamp_ns or "", authored={"pod": log.pod_name}, observed=hit[1])


def _from_traces(graph: OntologyGraph, b: _Builder) -> None:
    for tr in graph.entities(ResourceKind.OTEL_TRACE):
        if tr.status != "ERROR":
            continue
        text = " ".join([tr.error_message or "", tr.root_span_name or ""]
                        + [str(s.get("name", "")) + " " + str(s.get("status_message", "")) for s in tr.error_spans])
        kind = next((k for k, rx in _TRACE_PATTERNS if rx.search(text)), None)
        if not kind:
            b.out.unclassified["otel"] += 1
            continue
        owners = _graph_owners(graph, tr.uid, RelationshipType.HAS_TRACE) or [tr.fqn]
        b.add(kind, "otel", tr.uid, [(o, "otel.resource_attributes", 0.9) for o in owners],
              event_time=tr.started_at, authored={"service": tr.service_name, "trace_id": tr.trace_id},
              observed={"span": tr.root_span_name, "error": (tr.error_message or "")[:200]})


def _from_alerts(graph: OntologyGraph, b: _Builder) -> None:
    for al in graph.entities(ResourceKind.PROMETHEUS_ALERT):
        if al.state and al.state != "firing":
            continue
        kind = next((k for k, rx in _ALERT_PATTERNS if rx.search(" ".join([al.alert_name or "", al.summary or ""]))), None)
        if not kind:
            b.out.unclassified["prometheus"] += 1
            continue
        owners = _graph_owners(graph, al.uid, RelationshipType.HAS_ALERT) or [al.fqn]
        b.add(kind, "prometheus", al.uid, [(o, "prometheus.label_match", 0.9) for o in owners],
              event_time=al.started_at, authored={"alert": al.alert_name, "severity": al.severity})


_CONTROLLERS = (ResourceKind.DEPLOYMENT, ResourceKind.STATEFULSET, ResourceKind.DAEMONSET)


def _workloads(graph: OntologyGraph) -> dict[str, set[str]]:
    """For each pod, what runs it — the relations Kubernetes and Helm themselves use."""
    from ingestion.helm_collector import belongs_to_release
    controllers = [e for k in _CONTROLLERS for e in graph.entities(k)]
    replicasets = {(e.namespace, e.name): e for e in graph.entities(ResourceKind.REPLICASET)}
    releases = list(graph.entities(ResourceKind.HELM_RELEASE))
    out: dict[str, set[str]] = {}
    for pod in graph.entities(ResourceKind.POD):
        ns, labels, owners = pod.namespace, pod.labels or {}, set()
        kind, name = getattr(pod, "owner_ref_kind", ""), getattr(pod, "owner_ref_name", "")
        if kind == "ReplicaSet" and name:
            owners.add(f"ReplicaSet/{ns}/{name}")
            rs = replicasets.get((ns, name))
            dep = (rs.owner_ref_name if rs is not None else "") or (
                name.removesuffix("-" + labels["pod-template-hash"])
                if labels.get("pod-template-hash") and name.endswith("-" + labels["pod-template-hash"]) else "")
            if dep:
                owners.add(f"Deployment/{ns}/{dep}")
        elif kind and name:
            owners.add(f"{kind}/{ns}/{name}")
        for c in controllers:                               # the controller's selector
            if c.namespace == ns and c.selector and all(labels.get(k) == v for k, v in c.selector.items()):
                owners.add(c.fqn)
        controllers_of_pod = [c for c in controllers if c.fqn in owners]
        for rel in releases:
            if rel.namespace == ns and (belongs_to_release(pod, rel.name)
                                        or any(belongs_to_release(c, rel.name) for c in controllers_of_pod)):
                owners.add(rel.fqn)
        out[pod.fqn] = owners
    return out


def extract_signals(graph: OntologyGraph) -> SignalSet:
    """Every typed signal the graph holds, with its attributions, in a stable order."""
    b = _Builder()
    for extractor in (_from_pods, _from_deployments, _from_events, _from_annotations,
                      _from_logs, _from_traces, _from_alerts):
        extractor(graph, b)
    out = b.out
    out.workloads = _workloads(graph)
    first_subject = {}
    for e in out.edges:
        first_subject.setdefault(e.signal_id, e.entity)
    out.signals.sort(key=lambda s: (first_subject.get(s.signal_id, ""), s.kind, s.observed, s.signal_id))
    out.edges.sort(key=lambda e: (e.signal_id, -e.confidence, e.entity))
    return out
