"""The structured diagnosis in the product — docs/structured-in-product.md (its test table).

Controls that can fail are marked. The workflow runs offline on the native cases."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ontology.entities import Deployment, Pod
from ontology.graph import OntologyGraph
from rca.diagnosis import diagnose
from rca.signals import extract_signals
from tests.integration.cases import case_loader as cl
from tests.integration.workflow_harness import run_case
from workflow.nodes import analyze_node, diagnose_node, log_policy_decision_node

CASES = sorted(Path(__file__).resolve().parents[1].joinpath("integration", "cases").glob("h0*"))
DRIFT = ("drift field=container.api.resources.limits.memory declared=512Mi observed=128Mi "
         "severity=warning")


def _oom_graph():
    g = OntologyGraph()
    dep = Deployment(uid="d", name="api", namespace="prod", replicas=1, selector={"app": "api"})
    dep.annotations["gitops.container.api.resources.limits.memory"] = DRIFT
    g.add_entity(dep)
    g.add_entity(Pod(uid="p", name="api-7d9f-x", namespace="prod", phase="Running", labels={"app": "api"},
                     container_statuses=[{"name": "api", "ready": False, "restartCount": 3,
                                          "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                                          "lastState": {"terminated": {"reason": "OOMKilled"}}}]))
    return g


def _healthy_graph():
    g = OntologyGraph()
    g.add_entity(Pod(uid="p", name="api-1", namespace="prod", phase="Running",
                     container_statuses=[{"name": "c", "ready": True, "state": {"running": {}}}]))
    return g


def _cfg(graph, llm=None):
    c = {"configurable": {"graph": graph, "store": MagicMock(), "thread_id": "sip"}}
    if llm is not None:
        c["configurable"]["llm"] = llm
    return c


def _diagnosed(graph, stats=None):
    state = {"query": "why", "ingestion_stats": stats or {}}
    return {**state, **diagnose_node(state, _cfg(graph))}


def test_the_report_is_the_diagnosis_without_the_old_engine(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "none")
    with patch("rca.remediation_engine.RemediationEngine.score", side_effect=AssertionError):
        state = _diagnosed(_oom_graph())
        out = analyze_node(state, _cfg(_oom_graph()))
    rd = out["report_dict"]
    assert "512Mi" in rd["root_cause"] and rd["affected"] == ["Deployment/prod/api"]
    assert out["confidence"] == state["diagnosis"]["confidence"] and rd["remediation"] == []


def test_a_failed_collector_turns_no_incident_into_insufficient_evidence():
    failed = _diagnosed(_healthy_graph(), {"prometheus": {"fallback": True, "error": "x"}})
    assert failed["diagnosis"]["outcome"] == "insufficient_evidence"
    assert "prometheus" in failed["diagnosis"]["root_cause"]
    assert _diagnosed(_healthy_graph())["diagnosis"]["outcome"] == "no_incident"        # control


def _llm(root_cause):
    report = MagicMock(confidence="HIGH", raw_analysis="x", kube_version="")
    report.context.total_chunks = 1
    report.to_dict.return_value = {"root_cause": root_cause, "summary": "s",
                                   "remediation": ["kubectl rollout restart deployment/api -n prod"],
                                   "confidence": "HIGH", "diagnostics": []}
    analyzer = MagicMock(); analyzer.analyze.return_value = report
    return analyzer


@pytest.mark.parametrize("llm_text,disagrees", [
    ("The image registry is down for payments.", True),
    ("api runs out of memory: its limit was lowered to 128Mi.", False),          # control
])
def test_the_llm_cannot_override_the_diagnosis(llm_text, disagrees):
    state = _diagnosed(_oom_graph())
    with patch("workflow.nodes.RCAAnalyzer", return_value=_llm(llm_text)), \
            patch("workflow.nodes.ContextBuilder"):
        out = analyze_node(state, _cfg(_oom_graph(), llm=MagicMock()))
    assert out["report_dict"]["root_cause"] == state["diagnosis"]["root_cause"]
    assert out["report_dict"]["explanation"] == llm_text
    assert bool(out["llm_disagreement"]) is disagrees
    assert out["report_dict"]["remediation"]                    # its proposals are kept


def test_the_gate_scores_the_diagnosis_not_the_llm_label():
    state = {"diagnosis": {"outcome": "cause", "score": 0.5}, "confidence": "HIGH", "report_dict": {"confidence": "HIGH"},
             "blast_radius": {"risk": "LOW", "rollback_available": True, "namespaces": ["dev"],
                              "command_count": 1}}
    out = log_policy_decision_node(state)
    assert out["edge_log"][-1]["snapshot"]["score"] == 0.5
    assert out["verdict"] != "AUTO"


def test_no_llm_never_calls_one_and_is_never_auto():
    r = run_case("h003", llm="none")
    assert r.llm is None
    assert r.state["report_dict"]["remediation"] == []
    assert r.state.get("verdict") != "AUTO"


@pytest.mark.parametrize("case_dir", CASES, ids=lambda d: d.name.split("_")[0])
def test_the_product_is_what_is_measured(case_dir):
    """The workflow's report equals the structured solver's answer (no LLM), every case."""
    key = case_dir.name.split("_")[0]
    expected = diagnose(extract_signals(cl.build_graph(cl.load_case(case_dir))))
    r = run_case(key, llm="none")
    diag = r.state["diagnosis"]
    assert [(d["rule"], d["location"]) for d in diag["diagnoses"]] == \
        [(d.rule, d.cause_location) for d in expected.diagnoses]
    if diag["outcome"] == expected.outcome:
        assert r.state["report_dict"]["root_cause"] == expected.root_cause()
    else:                     # only H5: a failed collector turns no_incident into an abstention
        assert (expected.outcome, diag["outcome"]) == ("no_incident", "insufficient_evidence")
        assert diag["failed_collectors"]


def test_an_abstention_is_reviewed_never_blocked_nor_auto():
    state = {"diagnosis": {"outcome": "no_incident", "score": 0.0}, "report_dict": {},
             "blast_radius": {"risk": "LOW", "rollback_available": True, "command_count": 0}}
    assert log_policy_decision_node(state)["verdict"] == "HUMAN_REVIEW"


def test_the_report_carries_no_verdict_of_its_own():                          # fix plan H66
    # the LLM report's `decision` re-ran the gate on the pre-LLM score (h013: NO_GO beside the
    # workflow's HUMAN_REVIEW); the only verdict is the policy gate's
    llm = _llm("api runs out of memory: its limit was lowered to 128Mi.")
    llm.analyze.return_value.to_dict.return_value["decision"] = {"verdict": "NO_GO", "score": 0.42}
    state = _diagnosed(_oom_graph())
    with patch("workflow.nodes.RCAAnalyzer", return_value=llm), patch("workflow.nodes.ContextBuilder"):
        out = analyze_node(state, _cfg(_oom_graph(), llm=MagicMock()))
    assert "decision" not in out["report_dict"]
    assert out["report_dict"]["remediation"]                    # control: the rest of the LLM report stays
