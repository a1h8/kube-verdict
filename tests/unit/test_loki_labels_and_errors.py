"""Loki: configurable stream labels, error lines first — docs/fix-plan-001.md, H34."""
from __future__ import annotations

from ingestion.loki_source import LokiSource
from ontology.entities import LokiLog, Pod, ResourceKind
from ontology.graph import OntologyGraph
from tests.integration.cases.case_loader import fixture_loki_query

ERROR = "ERROR db: remaining connection slots are reserved"


def _streams(pod_label="pod", ns_label="namespace"):
    values = [[str(1000 + i), f"INFO request {i} served"] for i in range(30)] + [["999", ERROR]]
    return [{"stream": {pod_label: "api-1", ns_label: "prod"}, "values": values}]


def _collect(source, streams):
    g = OntologyGraph()
    g.add_entity(Pod(uid="p", name="api-1", namespace="prod", phase="Pending"))
    source._query = fixture_loki_query(streams)
    source.collect(g)
    return [e.log_line for e in g.entities(ResourceKind.LOKI_LOG) if isinstance(e, LokiLog)]


def test_promtail_labels_are_configurable():
    src = LokiSource(url="http://x", pod_label="pod", namespace_label="namespace")
    assert _collect(src, _streams())
    assert _collect(LokiSource(url="http://x"), _streams()) == []           # control: wrong labels


def test_an_error_is_not_pushed_out_by_recent_info_lines():
    lines = _collect(LokiSource(url="http://x", max_logs_per_pod=20,
                                pod_label="pod", namespace_label="namespace"), _streams())
    assert ERROR in lines and len(lines) == 20
