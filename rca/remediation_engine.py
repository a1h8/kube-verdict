"""
RemediationEngine — rule-based fallback for KubeVerdict.

When the LLM returns LOW confidence, this engine scores the graph with
deterministic rules and returns weighted RemediationHypothesis objects.

Architecture
────────────
  Each Rule defines:
    • match(entity, graph)  → bool        — fires when True
    • checks(entity, graph) → list[Check] — read-only diagnostics (docs/diagnostics.md)
    • commands(entity, graph) → list[str] — actions that change the cluster
    • base_weight             float        — prior confidence [0, 1]
    • evidence_boosts         dict         — annotation/event keys → extra weight

  The engine iterates all entities × all rules, collects matching
  RemediationHypothesis objects, deduplicates, and returns them sorted
  by final_weight descending.

  Final weight = min(1.0, base_weight + sum(active boosts))

Usage
─────
    from rca.remediation_engine import RemediationEngine

    engine = RemediationEngine()
    hypotheses = engine.score(graph)
    for h in hypotheses:
        print(f"[{h.weight:.2f}] {h.symptom}")
        for cmd in h.commands:
            print(f"  $ {cmd}")
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ontology.entities import (
    DaemonSet,
    Deployment,
    K8sEntity,
    Pod,
    ResourceKind,
    StatefulSet,
)
from ontology.graph import OntologyGraph
from rca.diagnostics import Check

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RemediationHypothesis:
    rule_id:     str
    symptom:     str                    # human-readable trigger description
    affected:    str                    # kind/namespace/name
    weight:      float                  # 0.0 – 1.0, higher = more confident
    commands:    list[str]              # actions only: they change the cluster
    explanation: str = ""
    evidence:    list[str] = field(default_factory=list)
    checks:      list[Check] = field(default_factory=list)   # read-only diagnostics

    def __str__(self) -> str:
        lines = [
            f"[{self.weight:.2f}] {self.symptom}  →  {self.affected}",
        ]
        if self.explanation:
            lines.append(f"  {self.explanation}")
        for check in self.checks:
            lines.append(f"  ? {check.command}  — {check.purpose}")
        for cmd in self.commands:
            lines.append(f"  $ {cmd}")
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Rule base
# ─────────────────────────────────────────────────────────────────────────────

class _Rule:
    id:           str
    symptom:      str
    base_weight:  float
    explanation:  str

    def match(self, entity: K8sEntity, graph: OntologyGraph) -> bool:
        return False

    def checks(self, entity: K8sEntity, graph: OntologyGraph) -> list[Check]:
        return []

    def commands(self, entity: K8sEntity, graph: OntologyGraph) -> list[str]:
        return []

    def evidence_boosts(self, entity: K8sEntity, graph: OntologyGraph) -> list[tuple[str, float]]:
        """Return (evidence_description, weight_boost) pairs that apply."""
        return []


# ── Rule 1: OOMKilled ─────────────────────────────────────────────────────────

class _OOMKillRule(_Rule):
    id          = "oom_kill"
    symptom     = "Container OOMKilled — memory limit too low"
    base_weight = 0.88
    explanation = (
        "The container exceeded its memory limit and was killed by the OOM killer. "
        "Increase limits.memory or reduce the application's memory footprint."
    )

    def match(self, entity: K8sEntity, graph: OntologyGraph) -> bool:
        if not isinstance(entity, Pod):
            return False
        return any(
            cs.get("state", {}).get("terminated", {}).get("reason") == "OOMKilled"
            or cs.get("lastState", {}).get("terminated", {}).get("reason") == "OOMKilled"
            for cs in entity.container_statuses
        )

    def commands(self, entity: K8sEntity, graph: OntologyGraph) -> list[str]:
        ns   = entity.namespace or "default"
        owner = _owner_name(entity, graph)
        cmds: list[str] = []
        if owner:
            cmds.append(
                f"kubectl patch deployment {owner} -n {ns} --type=json "
                f"-p='[{{\"op\":\"replace\","
                f"\"path\":\"/spec/template/spec/containers/0/resources/limits/memory\","
                f"\"value\":\"512Mi\"}}]'"
            )
        return cmds

    def checks(self, entity: K8sEntity, graph: OntologyGraph) -> list[Check]:
        ns, name = entity.namespace or "default", entity.name
        return [
            Check(f"kubectl describe pod {name} -n {ns}",
                  "Shows why the container last terminated and its memory limit.",
                  "Last State is Terminated with reason OOMKilled and exit code 137.",
                  "The last termination reason is not OOMKilled."),
            Check(f"kubectl top pod {name} -n {ns}",
                  "Shows the pod's current memory use.",
                  "Memory use climbs close to the container's limits.memory before each restart.",
                  "Memory use stays far below the limit."),
        ]

    def evidence_boosts(self, entity: K8sEntity, graph: OntologyGraph) -> list[tuple[str, float]]:
        boosts = []
        # Drift on memory limit is strong corroboration
        for k in entity.annotations:
            if k.startswith("drift.") and "memory" in k.lower():
                boosts.append(("memory drift detected", 0.07))
                break
        # Signal anomaly on memory
        if any("memory" in k and "anomaly" in v.lower()
               for k, v in entity.annotations.items() if k.startswith("signal.")):
            boosts.append(("PatchTST memory anomaly", 0.03))
        # OOM event corroboration
        for ev in graph.entities(ResourceKind.EVENT):
            if (ev.is_warning and ev.involved_name == entity.name
                    and "OOM" in (ev.reason + ev.message).upper()):
                boosts.append(("OOM Warning event", 0.02))
                break
        return boosts


# ── Rule 2: CrashLoopBackOff — DB connection refused ─────────────────────────

class _CrashLoopDBRule(_Rule):
    id          = "crashloop_db"
    symptom     = "CrashLoopBackOff — backend dependency unreachable"
    base_weight = 0.80
    explanation = (
        "The container is restarting because it cannot connect to a backend service "
        "(database, cache, or API). Check endpoint availability and network policies."
    )

    def match(self, entity: K8sEntity, graph: OntologyGraph) -> bool:
        if not isinstance(entity, Pod):
            return False
        if entity.phase not in ("CrashLoopBackOff", "Error") and entity.restart_count < 3:
            return False
        # Look for connection-related events
        for ev in graph.entities(ResourceKind.EVENT):
            if ev.is_warning and ev.involved_name == entity.name:
                msg = (ev.reason + ev.message).lower()
                if any(kw in msg for kw in ("connection refused", "econnrefused",
                                             "connection timeout", "no route to host")):
                    return True
        # Also trigger on high restart count with no OOM
        return entity.restart_count >= 5 and not any(
            cs.get("lastState", {}).get("terminated", {}).get("reason") == "OOMKilled"
            for cs in entity.container_statuses
        )

    def checks(self, entity: K8sEntity, graph: OntologyGraph) -> list[Check]:
        ns = entity.namespace or "default"
        checks = [
            Check(f"kubectl logs {entity.name} -n {ns} --previous",
                  "Shows the last lines the crashed container wrote.",
                  "connection refused, timeout or no route to host towards a dependency.",
                  "The crash reason is unrelated to a dependency (config, panic, OOM)."),
            Check(f"kubectl get endpoints -n {ns}",
                  "Lists which services have ready backends.",
                  "The dependency's service has no endpoints.",
                  "The dependency's service has ready endpoints."),
            Check(f"kubectl get services -n {ns}",
                  "Lists the services the pod can reach in its namespace.",
                  "The service named in the error is missing or renamed."),
        ]
        db_host = entity.annotations.get("env.DB_HOST") or ""
        if db_host:
            svc = db_host.split(".")[0]
            checks += [
                Check(f"kubectl describe service {svc} -n {ns}",
                      f"Shows the selector and endpoints of {svc}.",
                      f"{svc} has no endpoints, or its selector matches no pod."),
                Check(f"kubectl get pods -n {ns} -l app={svc}",
                      f"Shows whether the {svc} pods are running.",
                      f"The {svc} pods are not Ready or do not exist.",
                      f"The {svc} pods are Running and Ready."),
            ]
        return checks

    def evidence_boosts(self, entity: K8sEntity, graph: OntologyGraph) -> list[tuple[str, float]]:
        boosts: list[tuple[str, float]] = []
        if entity.restart_count >= 10:
            boosts.append(("restart_count ≥ 10", 0.05))
        elif entity.restart_count >= 5:
            boosts.append(("restart_count ≥ 5", 0.03))
        # Signal anomaly on restarts
        for k, v in entity.annotations.items():
            if "restart_count" in k and "anomaly" in v.lower():
                boosts.append(("PatchTST restart anomaly", 0.03))
                break
        return boosts


# ── Rule 3: ImagePullBackOff ──────────────────────────────────────────────────

class _ImagePullRule(_Rule):
    id          = "image_pull_backoff"
    symptom     = "ImagePullBackOff — image unavailable or registry credentials missing"
    base_weight = 0.90
    explanation = (
        "Kubernetes cannot pull the container image. The image tag may not exist, "
        "the registry may be unreachable, or imagePullSecret is missing."
    )

    def match(self, entity: K8sEntity, graph: OntologyGraph) -> bool:
        if not isinstance(entity, Pod):
            return False
        # Phase or events
        if "ImagePullBackOff" in (entity.phase or ""):
            return True
        for ev in graph.entities(ResourceKind.EVENT):
            if ev.involved_name == entity.name and ev.is_warning:
                if any(kw in ev.reason for kw in ("ImagePullBackOff", "ErrImagePull",
                                                    "Failed", "BackOff")):
                    if "image" in ev.message.lower() or "pull" in ev.message.lower():
                        return True
        return False

    def commands(self, entity: K8sEntity, graph: OntologyGraph) -> list[str]:
        ns    = entity.namespace or "default"
        owner = _owner_name(entity, graph)
        cmds: list[str] = []
        if owner:
            cmds += [
                "# Fix: update image to a valid tag",
                f"kubectl set image deployment/{owner} "
                f"<container>=<registry>/<image>:<valid-tag> -n {ns}",
            ]
        cmds.append(
            f"# If private registry: kubectl create secret docker-registry regcred "
            f"--docker-server=<registry> --docker-username=<user> "
            f"--docker-password=<password> -n {ns}"
        )
        return cmds

    def checks(self, entity: K8sEntity, graph: OntologyGraph) -> list[Check]:
        ns    = entity.namespace or "default"
        owner = _owner_name(entity, graph)
        checks = [
            Check(f"kubectl describe pod {entity.name} -n {ns}",
                  "Shows the pull events and the exact image reference.",
                  "Failed to pull with manifest unknown / not found (bad tag) or "
                  "unauthorized / 401 (missing credentials).",
                  "The image was pulled and the failure is later (container start)."),
        ]
        if owner:
            checks.append(Check(
                f"kubectl get deployment {owner} -n {ns} "
                f"-o jsonpath='{{.spec.template.spec.containers[0].image}}'",
                "Shows the image the Deployment asks for.",
                "The tag differs from the one declared in the chart, or does not exist "
                "in the registry."))
        return checks

    def evidence_boosts(self, entity: K8sEntity, graph: OntologyGraph) -> list[tuple[str, float]]:
        boosts = []
        for k in entity.annotations:
            if k.startswith("drift.") and ("image" in k.lower() or "tag" in k.lower()):
                boosts.append(("image tag drift detected", 0.07))
                break
        return boosts


# ── Rule 4: Missing ConfigMap / Secret ───────────────────────────────────────

class _MissingConfigRule(_Rule):
    id          = "missing_config"
    symptom     = "CreateContainerConfigError — ConfigMap or Secret not found"
    base_weight = 0.92
    explanation = (
        "A required ConfigMap or Secret does not exist in the namespace. "
        "Create the missing resources before redeploying."
    )

    def match(self, entity: K8sEntity, graph: OntologyGraph) -> bool:
        if not isinstance(entity, Pod):
            return False
        for ev in graph.entities(ResourceKind.EVENT):
            if ev.involved_name == entity.name and ev.is_warning:
                msg = ev.message.lower()
                if any(kw in msg for kw in ("configmap", "secret", "not found",
                                             "createcontainerconfigerror")):
                    return True
        return False

    def commands(self, entity: K8sEntity, graph: OntologyGraph) -> list[str]:
        ns = entity.namespace or "default"
        cmds = [
            "# Create missing ConfigMap (edit values as needed):",
            f"kubectl create configmap <name> --from-literal=KEY=VALUE -n {ns}",
            "# Create missing Secret:",
            f"kubectl create secret generic <name> --from-literal=KEY=VALUE -n {ns}",
        ]
        return cmds

    def checks(self, entity: K8sEntity, graph: OntologyGraph) -> list[Check]:
        ns = entity.namespace or "default"
        return [
            Check(f"kubectl describe pod {entity.name} -n {ns}",
                  "Shows which ConfigMap or Secret the container is waiting for.",
                  'CreateContainerConfigError naming a configmap or secret "not found", '
                  "or a missing key in it."),
            Check(f"kubectl get configmaps -n {ns}",
                  "Lists the ConfigMaps that exist in the namespace.",
                  "The ConfigMap the pod references is not in the list.",
                  "It is in the list: look for a missing key instead."),
            Check(f"kubectl get secrets -n {ns}",
                  "Lists the Secrets that exist in the namespace (names only).",
                  "The Secret the pod references is not in the list.",
                  "It is in the list: look for a missing key instead."),
        ]

    def evidence_boosts(self, entity: K8sEntity, graph: OntologyGraph) -> list[tuple[str, float]]:
        # Multiple pods in same deployment all failing → higher confidence
        boosts = []
        siblings = [
            e for e in graph.entities(ResourceKind.POD)
            if isinstance(e, Pod) and e.owner_ref_name == entity.owner_ref_name
            and e.namespace == entity.namespace and e.uid != entity.uid
        ]
        if len(siblings) >= 1:
            boosts.append((f"{len(siblings)} sibling pod(s) also affected", 0.04))
        return boosts


# ── Rule 5: Pending — unsatisfiable scheduling constraints ──────────────────

class _PendingSchedulingRule(_Rule):
    id          = "pending_unschedulable"
    symptom     = "Pod Pending — no node satisfies scheduling constraints"
    base_weight = 0.87
    explanation = (
        "No node matches the pod's nodeSelector, affinity rules, or resource requests. "
        "Check node labels and available capacity."
    )

    def match(self, entity: K8sEntity, graph: OntologyGraph) -> bool:
        if not isinstance(entity, Pod):
            return False
        if entity.phase != "Pending":
            return False
        for ev in graph.entities(ResourceKind.EVENT):
            if ev.involved_name == entity.name and ev.is_warning:
                msg = (ev.reason + ev.message).lower()
                if any(kw in msg for kw in ("unschedulable", "nodeselector",
                                             "insufficient", "taint", "toleration",
                                             "no nodes available")):
                    return True
        return entity.phase == "Pending"

    def commands(self, entity: K8sEntity, graph: OntologyGraph) -> list[str]:
        ns    = entity.namespace or "default"
        owner = _owner_name(entity, graph)
        cmds: list[str] = []
        if owner:
            cmds += [
                "# Remove unsatisfiable nodeSelector:",
                f"kubectl patch deployment {owner} -n {ns} --type=json "
                f"-p='[{{\"op\":\"remove\","
                f"\"path\":\"/spec/template/spec/nodeSelector\"}}]'",
            ]
        return cmds

    def checks(self, entity: K8sEntity, graph: OntologyGraph) -> list[Check]:
        ns = entity.namespace or "default"
        return [
            Check(f"kubectl describe pod {entity.name} -n {ns}",
                  "Shows the scheduler's FailedScheduling message.",
                  "0/N nodes are available, with the reason (node selector, affinity, "
                  "taint, insufficient cpu / memory)."),
            Check("kubectl get nodes --show-labels",
                  "Lists node labels against the pod's nodeSelector / affinity.",
                  "No node carries the label the pod requires.",
                  "A node carries it: look at taints or capacity instead."),
            Check("kubectl describe nodes | grep -A5 Taints",
                  "Lists node taints.",
                  "Every candidate node has a taint the pod does not tolerate."),
        ]

    def evidence_boosts(self, entity: K8sEntity, graph: OntologyGraph) -> list[tuple[str, float]]:
        boosts = []
        # Unschedulable event is very specific
        for ev in graph.entities(ResourceKind.EVENT):
            if ev.involved_name == entity.name and "Unschedulable" in ev.reason:
                boosts.append(("Unschedulable event", 0.08))
                break
        return boosts


# ── Rule 6: Helm drift — declared ≠ observed ─────────────────────────────────

class _HelmDriftRule(_Rule):
    id          = "helm_drift"
    symptom     = "Helm drift — cluster state diverged from chart declaration"
    base_weight = 0.88
    explanation = (
        "A resource was manually changed after Helm deployment. "
        "Run helm upgrade to restore the declared state."
    )

    def match(self, entity: K8sEntity, graph: OntologyGraph) -> bool:
        return any(k.startswith("drift.") for k in entity.annotations)

    def commands(self, entity: K8sEntity, graph: OntologyGraph) -> list[str]:
        ns    = entity.namespace or "default"
        # Find the Helm release managing this entity
        release_name = entity.labels.get("app.kubernetes.io/instance") or entity.name
        chart_name   = entity.labels.get("helm.sh/chart", "").split("-")[0] or release_name
        return [
            "# Restore declared state:",
            f"helm upgrade {release_name} demo/charts/{chart_name} -n {ns}",
        ]

    def checks(self, entity: K8sEntity, graph: OntologyGraph) -> list[Check]:
        ns    = entity.namespace or "default"
        release_name = entity.labels.get("app.kubernetes.io/instance") or entity.name
        chart_name   = entity.labels.get("helm.sh/chart", "").split("-")[0] or release_name
        drifted = ", ".join(k.removeprefix("drift.") for k in entity.annotations
                            if k.startswith("drift.")) or "the drifted fields"
        return [
            Check(f"kubectl get {entity.kind.value.lower()} {entity.name} -n {ns} "
                  f"-o jsonpath='{{.spec}}'",
                  "Shows the live spec of the drifted resource.",
                  f"The live value of {drifted} differs from the chart's values."),
            Check(f"helm diff upgrade {release_name} demo/charts/{chart_name} -n {ns}",
                  "Shows what helm upgrade would change back.",
                  f"The diff restores {drifted}.",
                  "The diff is empty: the drift is not from this release."),
        ]

    def evidence_boosts(self, entity: K8sEntity, graph: OntologyGraph) -> list[tuple[str, float]]:
        drifts = [k for k in entity.annotations if k.startswith("drift.")]
        boosts = []
        if len(drifts) >= 3:
            boosts.append((f"{len(drifts)} drift fields", 0.06))
        elif len(drifts) >= 1:
            boosts.append((f"{len(drifts)} drift field(s)", 0.03))
        # Unhealthy entity that also has drift → strong signal
        is_unhealthy = (
            (isinstance(entity, Pod) and entity.is_unhealthy)
            or (isinstance(entity, Deployment) and entity.is_degraded)
        )
        if is_unhealthy:
            boosts.append(("entity is unhealthy + drift", 0.04))
        return boosts


# ── Rule 7: Deployment degraded — generic ─────────────────────────────────────

class _DegradedDeploymentRule(_Rule):
    id          = "degraded_deployment"
    symptom     = "Deployment degraded — fewer replicas ready than desired"
    base_weight = 0.72
    explanation = (
        "The deployment has fewer ready replicas than desired. "
        "Check pod logs and events for the root cause."
    )

    def match(self, entity: K8sEntity, graph: OntologyGraph) -> bool:
        if isinstance(entity, Deployment):
            return entity.is_degraded
        if isinstance(entity, StatefulSet):
            return entity.ready_replicas < entity.replicas
        if isinstance(entity, DaemonSet):
            return entity.ready < entity.desired
        return False

    def commands(self, entity: K8sEntity, graph: OntologyGraph) -> list[str]:
        return []   # generic symptom: only checks, the cause is in its pods' own rules

    def checks(self, entity: K8sEntity, graph: OntologyGraph) -> list[Check]:
        ns   = entity.namespace or "default"
        kind = entity.kind.value.lower()
        return [
            Check(f"kubectl describe {kind} {entity.name} -n {ns}",
                  "Shows desired vs ready replicas and the controller's conditions.",
                  "Available=False or Progressing=False with a reason."),
            Check(f"kubectl rollout status {kind}/{entity.name} -n {ns}",
                  "Shows whether a rollout is stuck.",
                  "The rollout waits on new replicas that never become ready.",
                  "The rollout completed: the pods failed afterwards."),
            Check(f"kubectl get pods -n {ns} -l app={entity.name}",
                  "Lists the pods behind it and their states.",
                  "The not-ready pods share one state (CrashLoopBackOff, Pending, "
                  "ImagePullBackOff): follow that pod's own hypothesis."),
            Check(f"kubectl logs -l app={entity.name} -n {ns} --tail=50 --prefix",
                  "Shows the recent logs of every pod behind it.",
                  "The same error line on every not-ready pod."),
        ]

    def evidence_boosts(self, entity: K8sEntity, graph: OntologyGraph) -> list[tuple[str, float]]:
        boosts = []
        if isinstance(entity, Deployment) and entity.replicas > 0:
            ratio = entity.ready_replicas / entity.replicas
            if ratio == 0:
                boosts.append(("0% replicas ready", 0.10))
            elif ratio < 0.5:
                boosts.append(("< 50% replicas ready", 0.05))
        return boosts


# ─────────────────────────────────────────────────────────────────────────────
# Engine
# ─────────────────────────────────────────────────────────────────────────────

_ALL_RULES: list[_Rule] = [
    _OOMKillRule(),
    _CrashLoopDBRule(),
    _ImagePullRule(),
    _MissingConfigRule(),
    _PendingSchedulingRule(),
    _HelmDriftRule(),
    _DegradedDeploymentRule(),
]


class RemediationEngine:
    """
    Scores an OntologyGraph with the rule base and returns weighted hypotheses.

    Parameters
    ----------
    rules: Override the default rule set (useful for testing / extension).
    """

    def __init__(self, rules: list[_Rule] | None = None) -> None:
        self._rules = rules if rules is not None else _ALL_RULES

    def score(self, graph: OntologyGraph) -> list[RemediationHypothesis]:
        """
        Return all matching hypotheses, sorted by weight descending.
        Duplicate (rule_id, affected) pairs are deduplicated — highest wins.
        """
        seen:   dict[tuple[str, str], RemediationHypothesis] = {}

        for entity in graph.entities():
            for rule in self._rules:
                try:
                    if not rule.match(entity, graph):
                        continue
                except Exception as exc:
                    log.debug("rule %s.match failed for %s: %s", rule.id, entity.uid, exc)
                    continue

                boosts  = rule.evidence_boosts(entity, graph)
                weight  = min(1.0, rule.base_weight + sum(b for _, b in boosts))
                evidence = [desc for desc, _ in boosts]

                try:
                    cmds = rule.commands(entity, graph)
                except Exception as exc:
                    log.debug("rule %s.commands failed: %s", rule.id, exc)
                    cmds = []
                try:
                    checks = rule.checks(entity, graph)
                except Exception as exc:
                    log.debug("rule %s.checks failed: %s", rule.id, exc)
                    checks = []

                hyp = RemediationHypothesis(
                    rule_id=rule.id,
                    symptom=rule.symptom,
                    affected=entity.fqn,
                    weight=weight,
                    commands=cmds,
                    explanation=rule.explanation,
                    evidence=evidence,
                    checks=checks,
                )

                key = (rule.id, entity.uid)
                if key not in seen or seen[key].weight < weight:
                    seen[key] = hyp

        hypotheses = sorted(seen.values(), key=lambda h: h.weight, reverse=True)
        log.info(
            "RemediationEngine: %d hypothesis(es) from %d rule(s)",
            len(hypotheses), len(self._rules),
        )
        return hypotheses


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _owner_name(pod: K8sEntity, graph: OntologyGraph) -> str:
    """Return the name of the controlling Deployment/StatefulSet, or ''."""
    if not isinstance(pod, Pod):
        return ""
    if pod.owner_ref_kind in ("ReplicaSet", "Deployment", "StatefulSet", "DaemonSet"):
        # For ReplicaSet, walk up to Deployment
        if pod.owner_ref_kind == "ReplicaSet":
            for e in graph.entities(ResourceKind.DEPLOYMENT):
                if isinstance(e, Deployment) and e.namespace == pod.namespace:
                    if pod.name.startswith(e.name + "-"):
                        return e.name
        return pod.owner_ref_name
    return ""
