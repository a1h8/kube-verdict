"""Typed signals — one observed fact, typed, attached to one entity, pointing to its evidence.

Spec: docs/structured-diagnosis.md § 1. Extraction is deterministic: fields first, then
fixed patterns on messages. A raw item that matches no pattern produces no signal and is
counted in ``SignalSet.unclassified`` (per source), so the missing vocabulary is visible.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from ontology.entities import ResourceKind
from ontology.graph import OntologyGraph
from ontology.relationships import RelationshipType


@dataclass(frozen=True)
class Signal:
    kind: str
    entity: str            # fqn of the entity the fact is about
    source: str            # k8s_status | k8s_event | helm | prometheus | loki | otel
    attrs: tuple = ()      # sorted (key, value) pairs — hashable
    evidence: str = ""     # uid of the raw entity, or the annotation key

    def attr(self, key: str, default: str = "") -> str:
        return dict(self.attrs).get(key, default)


@dataclass
class SignalSet:
    signals: list[Signal] = field(default_factory=list)
    unclassified: Counter = field(default_factory=Counter)

    def kinds(self) -> set[str]:
        return {s.kind for s in self.signals}

    def of(self, kind: str) -> list[Signal]:
        return [s for s in self.signals if s.kind == kind]


def _sig(signal_kind: str, entity: str, source: str, evidence: str = "", attrs: dict | None = None,
         **more: str) -> Signal:
    merged = {**(attrs or {}), **more}
    return Signal(signal_kind, entity, source, tuple(sorted((k, str(v)) for k, v in merged.items())), evidence)


# ── message patterns (data) ─────────────────────────────────────────────────────
# (signal kind, compiled pattern, names of the groups kept as attrs)
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
    ("container.exited", re.compile(r"container (?P<container>\S+) in pod \S+ exited with code (?P<code>\d+)", re.I), ("container", "code")),
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
_ROLLOUT_REASONS = {"ReplicaSetUpdated", "NewReplicaSetCreated", "FoundNewReplicaSet"}
# signals a pod that is Running and ready now contradicts: its container lifecycle
_LIFECYCLE = {"container.crashloop", "container.oom_killed", "container.exited", "container.config_error",
              "image.pull_failed", "probe.readiness_failed", "probe.liveness_failed"}


def _match(patterns, text: str):
    for kind, rx, groups in patterns:
        m = rx.search(text or "")
        if m:
            return kind, {g: (m.group(g) or "") for g in groups if g in rx.groupindex and m.group(g)}
    return None


def _owners(graph: OntologyGraph, uid: str, rel: RelationshipType) -> list[str]:
    return [e.fqn for e in graph.neighbors(uid, rel, reverse=True)]


# ── extractors ──────────────────────────────────────────────────────────────────

def _from_pods(graph: OntologyGraph, out: SignalSet) -> None:
    for pod in graph.entities(ResourceKind.POD):
        for cs in pod.container_statuses:
            name = cs.get("name", "")
            state, last = cs.get("state") or {}, cs.get("lastState") or {}
            waiting = (state.get("waiting") or {}).get("reason", "")
            if waiting == "CrashLoopBackOff":
                out.signals.append(_sig("container.crashloop", pod.fqn, "k8s_status", pod.uid, container=name))
            elif waiting == "CreateContainerConfigError":
                out.signals.append(_sig("container.config_error", pod.fqn, "k8s_status", pod.uid, container=name))
            elif waiting in ("ImagePullBackOff", "ErrImagePull"):
                out.signals.append(_sig("image.pull_failed", pod.fqn, "k8s_status", pod.uid, container=name))
            for term in ((state.get("terminated") or {}), (last.get("terminated") or {})):
                if term.get("reason") == "OOMKilled":
                    out.signals.append(_sig("container.oom_killed", pod.fqn, "k8s_status", pod.uid, container=name))
                    break
            if cs.get("restartCount") and cs.get("ready", False) and "running" in state:
                out.signals.append(_sig("restart.past", pod.fqn, "k8s_status", pod.uid, container=name,
                                        restarts=cs.get("restartCount")))
        if pod.is_not_ready:
            out.signals.append(_sig("pod.not_ready", pod.fqn, "k8s_status", pod.uid,
                                    starting=str(pod.is_starting).lower()))


def _from_deployments(graph: OntologyGraph, out: SignalSet) -> None:
    for dep in graph.entities(ResourceKind.DEPLOYMENT):
        for c in dep.conditions:
            if c.get("type") != "Progressing":
                continue
            if str(c.get("status")) == "True" and c.get("reason") in _ROLLOUT_REASONS:
                out.signals.append(_sig("rollout.progressing", dep.fqn, "k8s_status", dep.uid,
                                        available=str(dep.is_available).lower()))
            elif str(c.get("status")) == "False" and c.get("reason") == "ProgressDeadlineExceeded":
                out.signals.append(_sig("rollout.stalled", dep.fqn, "k8s_status", dep.uid))


def _healthy_now(graph: OntologyGraph) -> set[str]:
    return {p.fqn for p in graph.entities(ResourceKind.POD)
            if not p.is_unhealthy and not p.is_not_ready and p.container_statuses}


def _from_events(graph: OntologyGraph, out: SignalSet) -> None:
    healthy = _healthy_now(graph)
    for ev in graph.entities(ResourceKind.EVENT):
        if not ev.is_warning:
            continue
        entity = f"{ev.involved_kind}/{ev.namespace}/{ev.involved_name}" if ev.involved_kind else ev.fqn
        if ev.reason == "FailedScheduling":
            text = (ev.message or "").lower()
            cls = next((c for c, needle in _SCHEDULING_CLASSES if needle in text), "other")
            out.signals.append(_sig("scheduling.failed", entity, "k8s_event", ev.uid, cls=cls))
            continue
        hit = _match(_EVENT_PATTERNS, ev.message)
        if hit and hit[0] in _LIFECYCLE and entity in healthy:
            # a container-lifecycle warning about a pod Running and ready now is history
            # (h022); other warnings (RBAC, DNS, quota…) stay current (h005)
            out.signals.append(_sig("event.past", entity, "k8s_event", ev.uid, reason=ev.reason, was=hit[0]))
        elif hit:
            kind, attrs = hit
            out.signals.append(_sig(kind, entity, "k8s_event", ev.uid, attrs))
        else:
            out.unclassified["k8s_event"] += 1


def _from_annotations(graph: OntologyGraph, out: SignalSet) -> None:
    for e in graph.entities():
        for key, text in e.annotations.items():
            if key.startswith("drift.") and key != "drift.helm.summary":
                out.signals.append(_sig("drift.config", e.fqn, "helm", key, field=key[len("drift."):]))
            elif key.startswith("missing.rbac."):
                out.signals.append(_sig("rbac.unbound", e.fqn, "k8s_status", key,
                                        serviceaccount=key[len("missing.rbac."):]))
            elif key.startswith("missing."):
                _, kind, name = key.split(".", 2)
                out.signals.append(_sig("dependency.missing", e.fqn, "k8s_status", key, {"kind": kind, "name": name}))


def _from_logs(graph: OntologyGraph, out: SignalSet) -> None:
    for log in graph.entities(ResourceKind.LOKI_LOG):
        if log.level not in ("error", "warn"):
            continue
        hit = _match(_LOG_PATTERNS, log.log_line)
        owners = _owners(graph, log.uid, RelationshipType.HAS_LOG) or [log.fqn]
        if not hit:
            out.unclassified["loki"] += 1
            continue
        kind, attrs = hit
        for owner in owners:
            out.signals.append(_sig(kind, owner, "loki", log.uid, attrs))


def _from_traces(graph: OntologyGraph, out: SignalSet) -> None:
    for tr in graph.entities(ResourceKind.OTEL_TRACE):
        if tr.status != "ERROR":
            continue
        text = " ".join([tr.error_message or "", tr.root_span_name or ""]
                        + [str(s.get("name", "")) + " " + str(s.get("status_message", "")) for s in tr.error_spans])
        kind = next((k for k, rx in _TRACE_PATTERNS if rx.search(text)), None)
        owners = _owners(graph, tr.uid, RelationshipType.HAS_TRACE) or [tr.fqn]
        if not kind:
            out.unclassified["otel"] += 1
            continue
        for owner in owners:
            out.signals.append(_sig(kind, owner, "otel", tr.uid, span=tr.root_span_name or ""))


def _from_alerts(graph: OntologyGraph, out: SignalSet) -> None:
    for al in graph.entities(ResourceKind.PROMETHEUS_ALERT):
        if al.state and al.state != "firing":
            continue
        text = " ".join([al.alert_name or "", al.summary or ""])
        kind = next((k for k, rx in _ALERT_PATTERNS if rx.search(text)), None)
        owners = _owners(graph, al.uid, RelationshipType.HAS_ALERT) or [al.fqn]
        if not kind:
            out.unclassified["prometheus"] += 1
            continue
        for owner in owners:
            out.signals.append(_sig(kind, owner, "prometheus", al.uid, alert=al.alert_name or "",
                                    severity=al.severity or ""))


def extract_signals(graph: OntologyGraph) -> SignalSet:
    """Every typed signal the graph holds, deduplicated, in a stable order."""
    out = SignalSet()
    for extractor in (_from_pods, _from_deployments, _from_events, _from_annotations,
                      _from_logs, _from_traces, _from_alerts):
        extractor(graph, out)
    seen, unique = set(), []
    for s in out.signals:
        key = (s.kind, s.entity, s.attrs)
        if key not in seen:
            seen.add(key)
            unique.append(s)
    out.signals = sorted(unique, key=lambda s: (s.entity, s.kind, s.attrs))
    return out
