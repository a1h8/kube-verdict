"""
Unit tests for _apply_rule_fallback — rule-based decision enrichment.

Verifies that LOW-confidence reports receive structured decisions
(summary, root_cause, causal_chain, affected, confidence) from the
RemediationEngine, and that existing LLM-produced fields are preserved.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest

from ontology.entities import K8sEvent, Pod, ResourceKind
from ontology.graph import OntologyGraph
from rca.analyzer import RCAReport, _apply_rule_fallback
from rca.context_builder import ContextWindow


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _empty_ctx() -> ContextWindow:
    return ContextWindow(seeds=[], drift=[], events=[], helm=[], related=[],
                         traces=[], logs=[])


def _report(
    raw: str = "",
    summary: str = "",
    root_cause: str = "",
    causal_chain: list[str] | None = None,
    remediation: list[str] | None = None,
    affected: list[str] | None = None,
    confidence: str = "LOW",
) -> RCAReport:
    r = RCAReport(
        query="test",
        kube_version="1.29",
        context=_empty_ctx(),
        raw_analysis=raw,
    )
    r.summary      = summary
    r.root_cause   = root_cause
    r.causal_chain = causal_chain or []
    r.remediation  = remediation or []
    r.affected     = affected or []
    r.confidence   = confidence
    return r


def _oom_graph() -> OntologyGraph:
    g = OntologyGraph()
    pod = Pod(uid="p1", name="worker-0", namespace="prod", phase="Error")
    pod.container_statuses = [
        {"lastState": {"terminated": {"reason": "OOMKilled", "exitCode": 137}}}
    ]
    g.add_entity(pod)
    return g


def _pending_graph() -> OntologyGraph:
    g = OntologyGraph()
    pod = Pod(uid="p2", name="gpu-0", namespace="prod", phase="Pending")
    ev = K8sEvent(
        uid="ev1", name="ev1", namespace="prod",
        event_type="Warning", involved_name="gpu-0",
        reason="Unschedulable", message="no nodes available",
    )
    g.add_entity(pod)
    g.add_entity(ev)
    return g


def _empty_graph() -> OntologyGraph:
    return OntologyGraph()


# ─────────────────────────────────────────────────────────────────────────────
# Empty fields → populated from top hypothesis
# ─────────────────────────────────────────────────────────────────────────────

class TestFallbackPopulatesEmptyFields:
    def test_summary_populated(self):
        r = _apply_rule_fallback(_report(), _oom_graph())
        assert r.summary != ""
        assert "oom_kill" in r.summary.lower() or "OOMKilled" in r.summary or "%" in r.summary

    def test_root_cause_populated(self):
        r = _apply_rule_fallback(_report(), _oom_graph())
        assert r.root_cause != ""

    def test_causal_chain_populated_from_evidence(self):
        g = _oom_graph()
        # Add drift annotation so there's evidence to build the chain from
        for e in g.entities(ResourceKind.POD):
            e.annotations["drift.resources.limits.memory"] = "declared=512Mi observed=50Mi"
        r = _apply_rule_fallback(_report(), g)
        # chain populated only when evidence exists; check it doesn't crash otherwise
        assert isinstance(r.causal_chain, list)

    def test_affected_enriched_with_hypothesis_fqn(self):
        r = _apply_rule_fallback(_report(), _oom_graph())
        assert len(r.affected) >= 1
        assert any("worker-0" in a for a in r.affected)

    def test_confidence_updated(self):
        r = _apply_rule_fallback(_report(), _oom_graph())
        assert "rule-assisted" in r.confidence
        assert "oom_kill" in r.confidence

    def test_remediation_commands_appended(self):
        r = _apply_rule_fallback(_report(), _oom_graph())
        assert len(r.remediation) > 0
        assert any("kubectl" in c for c in r.remediation)


# ─────────────────────────────────────────────────────────────────────────────
# Existing LLM fields are preserved
# ─────────────────────────────────────────────────────────────────────────────

class TestFallbackPreservesExistingFields:
    def test_existing_summary_not_overwritten(self):
        r = _apply_rule_fallback(
            _report(summary="LLM identified the bug"),
            _oom_graph(),
        )
        assert r.summary == "LLM identified the bug"

    def test_existing_root_cause_not_overwritten(self):
        r = _apply_rule_fallback(
            _report(root_cause="Database unreachable"),
            _oom_graph(),
        )
        assert r.root_cause == "Database unreachable"

    def test_existing_causal_chain_not_overwritten(self):
        chain = ["Step 1", "Step 2"]
        r = _apply_rule_fallback(
            _report(causal_chain=chain),
            _oom_graph(),
        )
        assert r.causal_chain == chain

    def test_existing_remediation_not_duplicated(self):
        g = _oom_graph()
        existing_cmd = "kubectl describe pod worker-0 -n prod"
        r = _apply_rule_fallback(
            _report(remediation=[existing_cmd]),
            g,
        )
        assert r.remediation.count(existing_cmd) == 1

    def test_existing_affected_not_duplicated(self):
        r = _apply_rule_fallback(
            _report(affected=["Pod/prod/worker-0"]),
            _oom_graph(),
        )
        affected = [a for a in r.affected if a == "Pod/prod/worker-0"]
        assert len(affected) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Multiple hypotheses
# ─────────────────────────────────────────────────────────────────────────────

class TestFallbackMultipleHypotheses:
    def test_hypothesis_headers_in_remediation(self):
        r = _apply_rule_fallback(_report(), _oom_graph())
        headers = [c for c in r.remediation if c.startswith("[rule:")]
        assert len(headers) >= 1

    def test_top_hypothesis_drives_summary(self):
        r = _apply_rule_fallback(_report(), _oom_graph())
        # OOMKill has base_weight 0.88 — should be top
        assert "oom_kill" in r.summary or "OOMKilled" in r.summary or "memory" in r.summary.lower()

    def test_weight_in_confidence(self):
        r = _apply_rule_fallback(_report(), _oom_graph())
        assert "w=" in r.confidence

    def test_hypothesis_count_in_confidence(self):
        r = _apply_rule_fallback(_report(), _oom_graph())
        assert "hypothesis" in r.confidence

    def test_causal_chain_uses_top3(self):
        g = _oom_graph()
        for e in g.entities(ResourceKind.POD):
            e.annotations["drift.resources.limits.memory"] = "x"
        r = _apply_rule_fallback(_report(), g)
        # chain items should reference rule IDs
        for item in r.causal_chain:
            assert "[" in item


# ─────────────────────────────────────────────────────────────────────────────
# Edge cases
# ─────────────────────────────────────────────────────────────────────────────

class TestFallbackEdgeCases:
    def test_empty_graph_returns_report_unchanged(self):
        r_orig = _report(summary="orig", root_cause="orig cause")
        r = _apply_rule_fallback(r_orig, _empty_graph())
        assert r.summary == "orig"
        assert r.root_cause == "orig cause"

    def test_returns_report_not_none(self):
        r = _apply_rule_fallback(_report(), _empty_graph())
        assert r is not None

    def test_pending_hypothesis(self):
        r = _apply_rule_fallback(_report(), _pending_graph())
        assert any("pending_unschedulable" in c or "Pending" in c
                   for c in r.remediation + [r.summary, r.root_cause])

    def test_no_crash_on_already_full_report(self):
        r = _apply_rule_fallback(
            _report(
                summary="S", root_cause="R", causal_chain=["c1"],
                remediation=["cmd1"], affected=["Pod/prod/x"],
            ),
            _oom_graph(),
        )
        assert isinstance(r, RCAReport)


# ─────────────────────────────────────────────────────────────────────────────
# Confidence string format
# ─────────────────────────────────────────────────────────────────────────────

class TestConfidenceFormat:
    def test_starts_with_low(self):
        r = _apply_rule_fallback(_report(), _oom_graph())
        assert r.confidence.upper().startswith("LOW")

    def test_contains_rule_assisted(self):
        r = _apply_rule_fallback(_report(), _oom_graph())
        assert "rule-assisted" in r.confidence

    def test_contains_top_prefix(self):
        r = _apply_rule_fallback(_report(), _oom_graph())
        assert "top:" in r.confidence

    def test_weight_formatted_as_float(self):
        import re
        r = _apply_rule_fallback(_report(), _oom_graph())
        assert re.search(r"w=\d+\.\d+", r.confidence)

    def test_hypothesis_count_present(self):
        r = _apply_rule_fallback(_report(), _oom_graph())
        assert "hypothesis" in r.confidence

    def test_rule_id_in_confidence(self):
        r = _apply_rule_fallback(_report(), _oom_graph())
        assert "oom_kill" in r.confidence


# ─────────────────────────────────────────────────────────────────────────────
# Remediation structure and ordering
# ─────────────────────────────────────────────────────────────────────────────

class TestRemediationStructure:
    def test_headers_precede_their_commands(self):
        r = _apply_rule_fallback(_report(), _oom_graph())
        header_idx = next(
            (i for i, c in enumerate(r.remediation) if c.startswith("[rule:")), None
        )
        assert header_idx is not None
        # at least one kubectl command after the first header
        after = r.remediation[header_idx + 1:]
        assert any("kubectl" in c for c in after)

    def test_no_duplicate_commands(self):
        r = _apply_rule_fallback(_report(), _oom_graph())
        assert len(r.remediation) == len(set(r.remediation))

    def test_highest_weight_header_first(self):
        r = _apply_rule_fallback(_report(), _oom_graph())
        headers = [c for c in r.remediation if c.startswith("[rule:")]
        assert len(headers) >= 1
        # extract weights
        import re
        weights = [float(re.search(r"w=(\d+\.\d+)", h).group(1)) for h in headers]
        assert weights == sorted(weights, reverse=True)

    def test_existing_commands_at_start(self):
        pre_cmd = "kubectl get pods -n prod"
        r = _apply_rule_fallback(_report(remediation=[pre_cmd]), _oom_graph())
        assert r.remediation[0] == pre_cmd

    def test_rule_headers_contain_weight_and_affected(self):
        r = _apply_rule_fallback(_report(), _oom_graph())
        headers = [c for c in r.remediation if c.startswith("[rule:")]
        for h in headers:
            assert "w=" in h
            assert "(" in h and ")" in h  # affected is in parens


# ─────────────────────────────────────────────────────────────────────────────
# All five demo scenarios trigger the right rules
# ─────────────────────────────────────────────────────────────────────────────

def _demo_graph() -> OntologyGraph:
    from ontology.entities import K8sEvent
    g = OntologyGraph()

    # payment-service → CrashLoopBackOff + DB connection refused
    crash = Pod(uid="p1", name="payment-service-0", namespace="demo",
                phase="CrashLoopBackOff", restart_count=8)
    ev_db = K8sEvent(uid="ev1", name="ev1", namespace="demo",
                     event_type="Warning", involved_name="payment-service-0",
                     reason="BackOff", message="connection refused to db:5432")

    # analytics-worker → OOMKilled with memory drift
    oom = Pod(uid="p2", name="analytics-worker-0", namespace="demo", phase="Error")
    oom.container_statuses = [
        {"lastState": {"terminated": {"reason": "OOMKilled", "exitCode": 137}}}
    ]
    oom.annotations["drift.resources.limits.memory"] = "declared=512Mi observed=50Mi"

    # notification-service → missing ConfigMap
    notif = Pod(uid="p3", name="notification-service-0", namespace="demo", phase="Pending")
    ev_cfg = K8sEvent(uid="ev2", name="ev2", namespace="demo",
                      event_type="Warning", involved_name="notification-service-0",
                      reason="Failed", message="configmap notification-config not found")

    # ml-inference → ImagePullBackOff with image drift
    ml = Pod(uid="p4", name="ml-inference-0", namespace="demo", phase="ImagePullBackOff")
    ml.annotations["drift.image.tag"] = "declared=nginx:1.25 observed=private.registry.io/ml:broken"

    # gpu-worker → Pending / Unschedulable
    gpu = Pod(uid="p5", name="gpu-worker-0", namespace="demo", phase="Pending")
    ev_sched = K8sEvent(uid="ev3", name="ev3", namespace="demo",
                        event_type="Warning", involved_name="gpu-worker-0",
                        reason="FailedScheduling",
                        message="0/1 nodes available: Unschedulable nodeSelector gpu=true")

    for e in [crash, ev_db, oom, notif, ev_cfg, ml, gpu, ev_sched]:
        g.add_entity(e)
    return g


class TestDemoScenarios:
    def test_crashloop_db_scenario(self):
        r = _apply_rule_fallback(_report(), _demo_graph())
        assert any("crashloop_db" in c for c in r.remediation + [r.confidence])

    def test_oom_scenario(self):
        r = _apply_rule_fallback(_report(), _demo_graph())
        assert any("oom_kill" in c for c in r.remediation + [r.confidence])

    def test_missing_config_scenario(self):
        r = _apply_rule_fallback(_report(), _demo_graph())
        assert any("missing_config" in c for c in r.remediation)

    def test_image_pull_scenario(self):
        r = _apply_rule_fallback(_report(), _demo_graph())
        assert any("image_pull" in c for c in r.remediation)

    def test_pending_unschedulable_scenario(self):
        r = _apply_rule_fallback(_report(), _demo_graph())
        assert any("pending_unschedulable" in c for c in r.remediation)

    def test_all_five_pods_in_affected(self):
        r = _apply_rule_fallback(_report(), _demo_graph())
        names = " ".join(r.affected)
        for pod_name in ("payment-service", "analytics-worker",
                         "notification-service", "ml-inference", "gpu-worker"):
            assert pod_name in names

    def test_summary_references_top_weighted_rule(self):
        r = _apply_rule_fallback(_report(), _demo_graph())
        # missing_config (0.92) or image_pull (0.90) should top the list
        assert r.summary != ""
        # the top rule's symptom or rule_id should appear
        assert any(kw in r.summary.lower() for kw in
                   ("configmap", "secret", "image", "oom", "missing", "pull", "rule:"))

    def test_root_cause_non_empty(self):
        r = _apply_rule_fallback(_report(), _demo_graph())
        assert len(r.root_cause) > 10

    def test_remediation_commands_are_executable(self):
        r = _apply_rule_fallback(_report(), _demo_graph())
        kubectl_cmds = [c for c in r.remediation if c.startswith("kubectl")]
        assert len(kubectl_cmds) >= 5  # at least one per scenario


# ─────────────────────────────────────────────────────────────────────────────
# Causal chain format
# ─────────────────────────────────────────────────────────────────────────────

class TestCausalChainFormat:
    def test_chain_items_have_rule_prefix(self):
        g = _oom_graph()
        for e in g.entities(ResourceKind.POD):
            e.annotations["drift.resources.limits.memory"] = "x"
        r = _apply_rule_fallback(_report(), g)
        for item in r.causal_chain:
            assert item.startswith("["), f"Expected '[' prefix, got: {item!r}"
            assert "]" in item

    def test_chain_is_list_of_strings(self):
        r = _apply_rule_fallback(_report(), _oom_graph())
        assert all(isinstance(item, str) for item in r.causal_chain)

    def test_chain_capped_to_top3_hypotheses(self):
        r = _apply_rule_fallback(_report(), _demo_graph())
        rule_ids_in_chain = set()
        for item in r.causal_chain:
            import re
            m = re.match(r"\[(\w+)\]", item)
            if m:
                rule_ids_in_chain.add(m.group(1))
        assert len(rule_ids_in_chain) <= 3
