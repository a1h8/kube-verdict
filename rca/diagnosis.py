"""Typed diagnoses derived from typed signals — docs/structured-diagnosis.md § 2.

On the correlation-spine model (rca/signals.py), a diagnosis is a derived edge from the
signals that support it to a cause, produced by a versioned rule (``RULES_VERSION``).

A rule names a cause family, the signals it requires, those that strengthen or contradict
it, where the cause is, and how to word it. Everything is deterministic: the same signals
give the same diagnoses, in the same order. The LLM, when used, only words the result.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from rca.signals import Signal, SignalSet

RULES_VERSION = "rules/1"   # bump when a rule, a weight or a threshold changes
REPORT_MIN = 0.5            # a diagnosis below this is not reported
GENERIC_CAP = 0.4           # a generic rule (drift alone, crash alone) never reaches REPORT_MIN
BASE, STRENGTH, CONTRA = 0.5, 0.15, 0.3

# kinds that describe context, never a fault on their own
CONTEXT_KINDS = {"event.past", "restart.past", "rollout.progressing"}


@dataclass
class Diagnosis:
    family: str
    cause: str
    cause_location: str
    rule: str
    score: float
    supports: list[Signal] = field(default_factory=list)
    contradicts: list[Signal] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)
    rule_version: str = RULES_VERSION   # a diagnosis is a derived edge: signals → cause

    @property
    def signal_ids(self) -> list[str]:
        return [x.signal_id for x in self.supports]

    @property
    def label(self) -> str:
        return label_of(self.score)


@dataclass
class Outcome:
    """What the structured pipeline concludes: ranked diagnoses, or an abstention."""
    diagnoses: list[Diagnosis]
    outcome: str                 # "cause" | "no_incident" | "insufficient_evidence"

    @property
    def confidence(self) -> str:
        return self.diagnoses[0].label if self.diagnoses else "LOW"

    def root_cause(self) -> str:
        if self.outcome == "no_incident":
            return "No incident: no fault signal in the evidence."
        if self.outcome == "insufficient_evidence":
            return "Insufficient evidence: fault signals exist but no rule explains them."
        return " ".join(d.cause for d in self.diagnoses)


def label_of(score: float) -> str:
    return "HIGH" if score >= 0.75 else "MEDIUM" if score >= REPORT_MIN else "LOW"


# ── helpers ─────────────────────────────────────────────────────────────────────

def _ns(fqn: str) -> str:
    parts = fqn.split("/")
    return parts[1] if len(parts) == 3 else ""


def _name(fqn: str) -> str:
    return fqn.rsplit("/", 1)[-1]




def _score(n_strengthening_sources: int, n_contradicting: int, generic: bool) -> float:
    score = BASE + STRENGTH * n_strengthening_sources - CONTRA * n_contradicting
    score = max(0.0, min(1.0, score))
    return min(score, GENERIC_CAP) if generic else score


def _diag(rule: str, family: str, cause: str, location: str, required: list[Signal],
          strengthening: list[Signal] = (), contradicting: list[Signal] = (),
          checks: list[str] = (), generic: bool = False) -> Diagnosis:
    sources = {s.source for s in strengthening} - {s.source for s in required}
    return Diagnosis(family=family, cause=cause, cause_location=location, rule=rule,
                     score=_score(len(sources) + (1 if strengthening and not sources else 0),
                                  len(contradicting), generic),
                     supports=list(required) + list(strengthening),
                     contradicts=list(contradicting), checks=list(checks))


# ── rules (docs/structured-diagnosis.md § Step 2) ───────────────────────────────

def _dependency_missing(ss: SignalSet) -> list[Diagnosis]:
    out = []
    for s in ss.of("dependency.missing"):
        kind, name = s.attr("kind"), s.attr("name")
        if not kind or not name:
            continue
        kind_title = {"configmap": "ConfigMap", "secret": "Secret", "persistentvolumeclaim": "PersistentVolumeClaim",
                      "pvc": "PersistentVolumeClaim", "serviceaccount": "ServiceAccount"}.get(kind.lower(), kind)
        strong = [x for x in ss.signals if ss.subject(x) == ss.subject(s)
                  and x.kind in ("container.config_error", "container.crashloop")]
        out.append(_diag("dependency.missing", "config-missing",
                         f"The {kind_title} {name} referenced by {_name(ss.subject(s))} does not exist (it is missing).",
                         f"absent:{kind_title}/{_ns(ss.subject(s))}/{name}", [s], strong,
                         checks=[f"kubectl get {kind.lower()} {name} -n {_ns(ss.subject(s))}"]))
    return out


def _oom(ss: SignalSet) -> list[Diagnosis]:
    out = []
    memory_drifts = [d for d in ss.of("drift.config") if "memory" in d.attr("field")]
    for s in ss.of("container.oom_killed"):
        drift = next((d for d in memory_drifts if "limits" in d.attr("field") and ss.same_workload(ss.subject(s), ss.subject(d))), None) \
            or next((d for d in memory_drifts if ss.same_workload(ss.subject(s), ss.subject(d))), None)
        if drift:
            out.append(_diag("oom.memory_drift", "oom",
                             f"The memory limit of {_name(ss.subject(drift))} drifted from the declared value: "
                             f"{drift.attr('field')} is declared {drift.attr('declared')} but deployed "
                             f"{drift.attr('observed')}, and the container is OOMKilled.",
                             ss.subject(drift), [s, drift],
                             checks=[f"helm get values {_name(ss.subject(drift))} -n {_ns(ss.subject(drift))}"]))
        else:
            out.append(_diag("oom.limit", "oom",
                             f"The container {s.attr('container')} of {_name(ss.subject(s))} exceeds its memory limit (OOMKilled).",
                             ss.subject(s), [s]))
    return out


def _image_pull(ss: SignalSet) -> list[Diagnosis]:
    out = []
    for s in ss.of("image.pull_failed"):
        drift = next((d for d in ss.of("drift.config") if "image" in d.attr("field")
                      and ss.same_workload(ss.subject(s), ss.subject(d))), None)
        code = s.attr("code").lower()
        if drift:
            cause = (f"The image tag of {_name(ss.subject(drift))} drifted: declared {drift.attr('declared')}, "
                     f"deployed {drift.attr('observed')}, and that image cannot be pulled.")
            location = ss.subject(drift)
        elif code in ("401", "403", "unauthorized"):
            cause, location = f"The image of {_name(ss.subject(s))} cannot be pulled: the registry refuses the credentials.", ss.subject(s)
        elif code in ("404", "not found"):
            cause, location = f"The image of {_name(ss.subject(s))} cannot be pulled: the image or tag does not exist.", ss.subject(s)
        else:
            cause, location = f"The image of {_name(ss.subject(s))} cannot be pulled.", ss.subject(s)
        out.append(_diag("image.pull", "image-pull", cause, location, [s], [drift] if drift else []))
    return out


def _scheduling(ss: SignalSet) -> list[Diagnosis]:
    words = {"cpu": "not enough CPU on the nodes", "memory": "not enough memory on the nodes",
             "affinity": "no node matches its affinity or selector", "volume": "its volume is bound to another node",
             "unschedulable": "the matching nodes are cordoned", "taint": "the nodes carry taints it does not tolerate"}
    return [_diag("scheduling", "scheduling",
                  f"{_name(ss.subject(s))} cannot be scheduled: {words.get(s.attr('cls'), 'no node fits it')}.",
                  ss.subject(s), [s]) for s in ss.of("scheduling.failed")]


def _init_failed(ss: SignalSet) -> list[Diagnosis]:
    out = []
    for s in ss.of("init.failed"):
        pod, container = ss.subject(s), s.attr("container")
        exited = [x for x in ss.of("container.exited")
                  if ss.subject(x) == pod and x.attr("container") == container]
        detail = next((x.attr("detail") for x in exited if x.attr("detail")), "")
        reported = f": {detail}" if detail else ""
        out.append(_diag("init.failed", "init-container",
                         f"The init container {container} of {_name(pod)} fails (exit code {s.attr('exit_code')}"
                         f"{reported}), so the main containers never start.",
                         pod, [s], exited,
                         checks=[f"kubectl logs {_name(pod)} -c {container} -n {_ns(pod)}"]))
    return out


def _rbac(ss: SignalSet) -> list[Diagnosis]:
    """A denial names the ServiceAccount; an unbound ServiceAccount alone is not a fault."""
    out, seen = [], set()
    for s in ss.of("rbac.denied"):
        user = s.attr("user")                              # system:serviceaccount:<ns>:<sa>
        parts = user.split(":")
        if len(parts) != 4 or parts[1] != "serviceaccount":
            continue
        ns, sa = parts[2], parts[3]
        if (ns, sa) in seen:
            continue
        seen.add((ns, sa))
        unbound = [u for u in ss.of("rbac.unbound") if u.attr("serviceaccount") == sa]
        verb, resource = s.attr("verb"), s.attr("resource")
        what = f" to {verb} {resource}" if verb and resource else ""
        out.append(_diag("rbac", "rbac",
                         f"The ServiceAccount {sa} has no (Cluster)RoleBinding granting the permission{what}: "
                         f"its API calls are forbidden.",
                         f"ServiceAccount/{ns}/{sa}", [s], unbound,
                         checks=[f"kubectl auth can-i {verb or 'list'} {resource or 'pods'} "
                                 f"--as=system:serviceaccount:{ns}:{sa}",
                                 f"kubectl get rolebindings,clusterrolebindings -A -o wide | grep {sa}"]))
    return out


def _hpa_metrics(ss: SignalSet) -> list[Diagnosis]:
    """The HPA reports it cannot read resource metrics: the cause is the metrics API."""
    by_hpa: dict[str, list[Signal]] = {}
    for s in ss.of("hpa.metrics_unavailable"):
        by_hpa.setdefault(ss.subject(s), []).append(s)
    out = []
    for hpa, signals in sorted(by_hpa.items()):
        ns = _ns(hpa)
        out.append(_diag("hpa.metrics", "hpa",
                         f"The HPA {_name(hpa)} cannot read resource metrics (the metrics API returns none, "
                         f"usually metrics-server), so it cannot autoscale its target.",
                         "outside:APIService/v1beta1.metrics.k8s.io", signals[:1], signals[1:],
                         checks=["kubectl get apiservice v1beta1.metrics.k8s.io",
                                 f"kubectl top pods -n {ns}",
                                 f"kubectl describe hpa {_name(hpa)} -n {ns}"]))
    return out


def _cert_expired(ss: SignalSet) -> list[Diagnosis]:
    """An expired certificate in the app's TLS; a failing issuer in the namespace explains why
    it was not renewed. The issuer is linked by namespace only — a weak link, so it
    strengthens the diagnosis but is not required (docs/structured-diagnosis.md)."""
    by_ns: dict[str, list[Signal]] = {}
    for s in ss.of("tls.cert_expired"):
        by_ns.setdefault(_ns(ss.subject(s)), []).append(s)
    out = []
    for ns, expired in sorted(by_ns.items()):
        issuers = [i for i in ss.of("cert.issuer_failed") if _ns(ss.subject(i)) == ns]
        pods = sorted({_name(ss.subject(x)) for x in expired})
        if issuers:
            issuer = ss.subject(issuers[0])
            cause = (f"The TLS certificate used by {', '.join(pods)} has expired (x509), and the issuer "
                     f"{_name(issuer)} fails ({issuers[0].attr('reason') or 'error'}), so it was not renewed.")
            location = issuer
        else:
            cause = f"The TLS certificate used by {', '.join(pods)} has expired (x509)."
            location = ss.subject(expired[0])
        out.append(_diag("cert.expired", "certificate", cause, location, expired[:1], issuers,
                         checks=[f"kubectl get certificates,issuers -n {ns}",
                                 f"kubectl describe certificate -n {ns}"]))
    return out


def _etcd_slow(ss: SignalSet) -> list[Diagnosis]:
    """Error spans to etcd; the workload's probe failures are the symptom it explains."""
    by_workload: dict[str, list[Signal]] = {}
    for s in ss.of("etcd.slow"):
        subject = ss.subject(s)
        key = subject if subject.startswith("Deployment/") else next(
            (o for o in sorted(ss.workloads.get(subject, ())) if o.startswith("Deployment/")), subject)
        by_workload.setdefault(key, []).append(s)
    out = []
    for workload, spans in sorted(by_workload.items()):
        symptoms = [x for x in ss.signals if x.kind in ("probe.readiness_failed", "pod.not_ready")
                    and ss.same_workload(ss.subject(x), workload)]
        error = next((x.attr("error") for x in spans if x.attr("error")), "")
        reported = f" ({error})" if error else ""
        out.append(_diag("etcd.slow", "etcd",
                         f"Calls from {_name(workload)} to etcd ({spans[0].attr('span')}) fail{reported}: etcd is "
                         f"slow or compacting, and {_name(workload)} times out.",
                         "outside:etcd", spans[:1], symptoms,
                         checks=["kubectl get --raw /readyz/etcd",
                                 "kubectl -n kube-system get pods -l component=etcd"]))
    return out


def _config_drift(ss: SignalSet, explained: set[str]) -> list[Diagnosis]:
    """A configuration drift alone is generic; it is reported when a workload of the same
    release is failing and no more specific rule already used that drift."""
    faults = [s for s in ss.signals if s.kind not in CONTEXT_KINDS | {"drift.config"}]
    out = []
    for d in ss.of("drift.config"):
        if d.payload_ref in explained:
            continue
        failing = [f for f in faults if ss.same_workload(ss.subject(f), ss.subject(d))]
        out.append(_diag("config.drift", "helm-drift",
                         f"{_name(ss.subject(d))} drifted from its declared configuration: {d.attr('field')} is declared "
                         f"{d.attr('declared')} but deployed {d.attr('observed')}.",
                         ss.subject(d), [d], failing, generic=not failing))
    return out


def _crash(ss: SignalSet, explained_pods: set[str]) -> list[Diagnosis]:
    return [_diag("container.crash", "crashloop",
                  f"The container {s.attr('container')} of {_name(ss.subject(s))} keeps crashing; the cause is not identified.",
                  ss.subject(s), [s], generic=True)
            for s in ss.of("container.crashloop") if ss.subject(s) not in explained_pods]


# every rule id ``diagnose`` can produce — docs/diagnosis-coverage.md checks rows against it
RULE_IDS: frozenset[str] = frozenset({"dependency.missing", "oom.memory_drift", "oom.limit", "image.pull",
                                       "scheduling", "config.drift", "container.crash",
                                       "init.failed", "rbac", "hpa.metrics", "cert.expired", "etcd.slow"})

RULES: list[Callable[[SignalSet], list[Diagnosis]]] = [_dependency_missing, _oom, _image_pull, _scheduling,
                                                       _init_failed, _rbac, _hpa_metrics, _cert_expired, _etcd_slow]


def _starting_in_rollout(ss: SignalSet) -> set[str]:
    """Pods starting cleanly in a rollout that progresses while the Deployment stays
    available — the same rule as the seed guard (dedup/bfs.py, docs/test-cases.md § h026)."""
    rolling = [ss.subject(r) for r in ss.of("rollout.progressing") if r.attr("available") == "true"]
    return {ss.subject(p) for p in ss.of("pod.not_ready") if p.attr("starting") == "true"
            and any(ss.same_workload(ss.subject(p), dep) for dep in rolling)}


def _is_fault(ss: SignalSet, s: Signal, starting: set[str]) -> bool:
    # a drift or an unbound ServiceAccount is a configuration observation, not a symptom:
    # with nothing failing there is no incident
    if s.kind in CONTEXT_KINDS or s.kind in ("drift.config", "rbac.unbound"):
        return False
    if s.kind in ("pod.not_ready", "probe.readiness_failed") and ss.subject(s) in starting:
        return False
    return True


def diagnose(ss: SignalSet) -> Outcome:
    """Ranked diagnoses whose score reaches REPORT_MIN, or an abstention."""
    found: list[Diagnosis] = []
    for rule in RULES:
        found += rule(ss)
    explained_drifts = {s.payload_ref for d in found for s in d.supports if s.kind == "drift.config"}
    explained_pods = {ss.subject(s) for d in found for s in d.supports}
    found += _config_drift(ss, explained_drifts)
    found += _crash(ss, explained_pods)
    found.sort(key=lambda d: (-d.score, d.rule, d.cause_location))
    reported = []
    for d in found:
        if d.score >= REPORT_MIN and (d.family, d.cause_location) not in {(r.family, r.cause_location) for r in reported}:
            reported.append(d)
    if reported:
        return Outcome(reported, "cause")
    starting = _starting_in_rollout(ss)
    faults = [s for s in ss.signals if _is_fault(ss, s, starting)]
    return Outcome([], "insufficient_evidence" if faults else "no_incident")
