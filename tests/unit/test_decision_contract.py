"""Contract-first decision tests — policy gate, blast radius, IncidentReport schema."""
from __future__ import annotations

from types import SimpleNamespace

from models import BlastRadius, Decision, IncidentReport


# ── Policy gate (via the Decision model, which delegates to policy_gate.evaluate) ──
def test_policy_gate_auto():
    d = Decision.evaluate(
        score=0.90, risk="LOW", rollback_available=True,
        namespace="staging", mc_win_rate=0.90,
    )
    assert d.verdict == "AUTO"


def test_policy_gate_prod_human():
    d = Decision.evaluate(
        score=0.95, risk="LOW", rollback_available=True,
        namespace="production", mc_win_rate=1.0,
    )
    assert d.verdict == "HUMAN_REVIEW"
    assert any("production" in r for r in d.reasons)


def test_policy_gate_no_rollback_no_go():
    d = Decision.evaluate(
        score=0.95, risk="LOW", rollback_available=False, namespace="staging",
    )
    assert d.verdict == "NO_GO"
    assert any("rollback" in r.lower() for r in d.reasons)


# ── Blast radius ──────────────────────────────────────────────────────────────
def test_blast_radius_cluster_scoped_high():
    br = BlastRadius.from_remediation(
        remediation=[
            "kubectl create clusterrolebinding api-admin "
            "--clusterrole=admin --serviceaccount=default:api",
        ],
        affected=["ClusterRoleBinding/api-admin"],
        rollback_cmds=["kubectl delete clusterrolebinding api-admin"],
    )
    assert br.cluster_scoped is True
    assert br.risk == "HIGH"            # cluster-scoped escalates to HIGH
    assert br.rollback_available is True  # rollback present → stays HIGH, not CRITICAL


# ── Canonical IncidentReport schema ───────────────────────────────────────────
def test_incident_report_schema():
    pre = SimpleNamespace(score=0.85, label="HIGH", reasons=["anchors"])
    ctx = SimpleNamespace(
        pre_llm_confidence=pre,
        events=["Warning BackOff pod/api-xyz: Back-off restarting failed container"],
        alerts=[], traces=[], policy_violations=[], anchor_fixes=[],
    )
    report = SimpleNamespace(
        query="api pods crashlooping",
        summary="api pods crashlooping in staging",
        root_cause="ConfigMap api-config is missing",
        confidence="HIGH",
        affected=["Pod/api-xyz"],
        remediation=["kubectl create configmap api-config -n staging --from-file=app.conf"],
        rollback=["kubectl delete configmap api-config -n staging"],
        causal_chain=["configmap missing", "container cannot start", "crashloop"],
        context=ctx,
    )

    d = IncidentReport.from_rca(report, namespace="staging").to_dict()

    for key in (
        "summary", "root_cause", "confidence", "evidence", "reasoning_paths",
        "remediation", "rollback_plan", "blast_radius", "decision", "namespace", "query",
    ):
        assert key in d, f"missing canonical key: {key}"

    assert d["confidence"] == {"label": "HIGH", "score": 0.85}
    assert d["evidence"][0]["source"] == "event"
    assert d["reasoning_paths"][0] == "configmap missing"
    assert d["rollback_plan"]["available"] is True
    assert d["blast_radius"]["risk"] in ("LOW", "MEDIUM", "HIGH", "CRITICAL")
    assert d["decision"]["verdict"] in ("AUTO", "HUMAN_REVIEW", "NO_GO")
