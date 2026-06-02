"""Unit tests for Incident Memory feedback (PR2, Track 2).

Covers the ResolvedIncident.from_report seam off the canonical IncidentReport
and the entity_kinds extraction fix (previously always empty because the node
read a non-existent 'affected_resources' key).
"""
from __future__ import annotations

from decision.models import IncidentReport
from knowledge.example_store import (
    ResolvedIncident,
    _ExampleChunk,
    _entity_kinds,
)


# ── _entity_kinds ───────────────────────────────────────────────────────────────

class TestEntityKinds:
    def test_kind_ns_name_refs(self):
        assert _entity_kinds(["Pod/prod/api-xyz", "Deployment/prod/api"]) == [
            "Pod", "Deployment",
        ]

    def test_dedup_order_preserving(self):
        assert _entity_kinds(["Pod/a/x", "Pod/b/y", "Service/c/z"]) == [
            "Pod", "Service",
        ]

    def test_no_slash_uses_whole_token(self):
        assert _entity_kinds(["Pod"]) == ["Pod"]

    def test_empty(self):
        assert _entity_kinds([]) == []

    def test_blank_refs_skipped(self):
        assert _entity_kinds(["", "  ", "Pod/a/x"]) == ["Pod"]


# ── ResolvedIncident.from_report ────────────────────────────────────────────────

class TestFromReport:
    def _report(self) -> IncidentReport:
        return IncidentReport(
            query="api pods crashlooping",
            root_cause="memory limit too low",
            confidence="HIGH",
            affected=["Pod/payment/api-xyz", "Deployment/payment/api"],
            remediation=["kubectl set resources ..."],
        )

    def test_maps_report_fields(self):
        inc = ResolvedIncident.from_report(self._report())
        assert inc.query == "api pods crashlooping"
        assert inc.root_cause == "memory limit too low"
        assert inc.confidence == "HIGH"
        assert inc.remediation == ["kubectl set resources ..."]

    def test_entity_kinds_now_populated(self):
        # Regression guard: this was always [] before the fix.
        inc = ResolvedIncident.from_report(self._report())
        assert inc.entity_kinds == ["Pod", "Deployment"]

    def test_explicit_context_args(self):
        inc = ResolvedIncident.from_report(
            self._report(),
            hypothesis="OOMKilled — memory limit drift",
            anchor_violations=["Deployment/payment/api"],
        )
        assert inc.hypothesis == "OOMKilled — memory limit drift"
        assert inc.anchor_violations == ["Deployment/payment/api"]

    def test_defaults_when_context_omitted(self):
        inc = ResolvedIncident.from_report(self._report())
        assert inc.hypothesis == ""
        assert inc.anchor_violations == []

    def test_remediation_is_a_copy(self):
        report = self._report()
        inc = ResolvedIncident.from_report(report)
        inc.remediation.append("extra")
        assert report.remediation == ["kubectl set resources ..."]

    def test_empty_report_yields_empty_incident(self):
        inc = ResolvedIncident.from_report(IncidentReport())
        assert inc.entity_kinds == []
        assert inc.remediation == []
        assert inc.confidence == ""


# ── indexed text surfaces the entity kinds (the point of the fix) ────────────────

class TestIndexedText:
    def test_entities_appear_in_chunk_text(self):
        inc = ResolvedIncident.from_report(
            IncidentReport(
                query="q", root_cause="rc", confidence="HIGH",
                affected=["Pod/ns/x", "StatefulSet/ns/y"],
            )
        )
        text = _ExampleChunk(inc).to_text()
        assert "Entities: Pod, StatefulSet" in text
