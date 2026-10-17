"""
Live parity — does production build the graph the offline tests assume?

Spec: docs/test-cases.md → *Live parity*. For every native case, the same
``kube/`` fixtures go through two paths: the test loader
(``case_loader.build_graph``) and the real ``K8sCollector`` fed by a fake API
(``fake_kube_api``). Where they disagree, the offline tests prove something
production does not do. Each known divergence is a strict xfail: fixing it
flips the test, and the mark must then be removed.
"""
from __future__ import annotations

import pytest

from ontology.entities import ResourceKind
from tests.integration.cases.case_loader import build_graph, list_cases, load_case
from tests.integration.fake_kube_api import collector_for
from tests.integration.workflow_harness import CASES_ROOT

CASES = [d.name for d in list_cases(CASES_ROOT)]


@pytest.fixture(scope="module")
def graphs():
    """case → (loader graph, production collector graph)."""
    out = {}
    for name in CASES:
        case = load_case(CASES_ROOT / name)
        ns = case["expect"].get("namespace", "default")
        loader = build_graph(case, wire_signals=False)
        live = collector_for(case["observed"], ns).collect(namespaces=[ns])
        out[name] = (case, loader, live)
    return out


def _names(graph, kind) -> set[str]:
    return {e.name for e in graph.entities(kind)}


def _pods(graph) -> dict:
    return {e.name: e for e in graph.entities(ResourceKind.POD)}


def _dependency_annotations(pod) -> set[str]:
    return {k for k in pod.annotations if k.startswith(("missing.", "netpol."))}


def _reasons(pod) -> set[str]:
    out = set()
    for cs in pod.container_statuses:
        for key in ("state", "lastState"):
            value = cs.get(key)
            if isinstance(value, dict):
                for detail in value.values():
                    if isinstance(detail, dict) and detail.get("reason"):
                        out.add(f"{key}:{detail['reason']}")
    return out


def test_both_paths_see_the_same_pods_and_deployments(graphs):
    for name, (_, loader, live) in graphs.items():
        assert _names(live, ResourceKind.POD) == _names(loader, ResourceKind.POD), name
        assert _names(live, ResourceKind.DEPLOYMENT) <= _names(loader, ResourceKind.DEPLOYMENT), name


_RBAC_NETPOL = ("missing.rbac.", "netpol.")


def _compare_annotations(graphs, keep) -> None:
    expected_somewhere = False
    for name, (_, loader, live) in graphs.items():
        live_pods = _pods(live)
        for pod_name, pod in _pods(loader).items():
            wanted = {k for k in _dependency_annotations(pod) if keep(k)}
            expected_somewhere |= bool(wanted)
            got = {k for k in _dependency_annotations(live_pods[pod_name]) if keep(k)}
            assert wanted == got, (name, pod_name, wanted ^ got)
    assert expected_somewhere


def test_missing_dependency_annotations_are_produced_live(graphs):
    """Was divergence 1 (detector only in the loader): both paths now call
    ingestion.readiness.detect_missing — Secret, ConfigMap, PVC, ServiceAccount."""
    _compare_annotations(graphs, lambda k: not k.startswith(_RBAC_NETPOL))


@pytest.mark.xfail(strict=True, reason=(
    "divergence 1b: RoleBindings and NetworkPolicies are not collected as typed "
    "objects live, so missing.rbac.* / netpol.* exist only offline; "
    "docs/test-cases.md § Live parity"))
def test_rbac_and_netpol_annotations_are_produced_live(graphs):
    _compare_annotations(graphs, lambda k: k.startswith(_RBAC_NETPOL))


def test_pod_dependency_edges_are_built_live(graphs):
    """Was divergence 2 (pods collected before what they reference): fixed 2026-10-13."""
    references = 0
    for name, (case, _, live) in graphs.items():
        present = {
            "ConfigMap": {d["metadata"]["name"] for d in case["observed"].get("configmaps", [])},
            "PersistentVolumeClaim": {d["metadata"]["name"] for d in case["observed"].get("pvcs", [])},
            "ServiceAccount": {d["metadata"]["name"] for d in case["observed"].get("serviceaccounts", [])},
        }
        for pod_raw in case["observed"].get("pods", []):
            spec = pod_raw.get("spec", {})
            wanted = {("ConfigMap", v["configMap"]["name"]) for v in spec.get("volumes", [])
                      if v.get("configMap", {}).get("name") in present["ConfigMap"]}
            wanted |= {("PersistentVolumeClaim", v["persistentVolumeClaim"]["claimName"])
                       for v in spec.get("volumes", [])
                       if v.get("persistentVolumeClaim", {}).get("claimName") in present["PersistentVolumeClaim"]}
            sa = spec.get("serviceAccountName")
            if sa in present["ServiceAccount"]:
                wanted.add(("ServiceAccount", sa))
            if not wanted:
                continue
            references += len(wanted)
            pod = _pods(live)[pod_raw["metadata"]["name"]]
            linked = {(e.kind.value, e.name) for e in live.neighbors(pod.uid)}
            assert wanted <= linked, (name, pod.name, wanted - linked)
    assert references


def test_container_state_reasons_survive_live(graphs):
    """Was divergence 3 (str(cs.state), no lastState): fixed 2026-10-13."""
    expected_somewhere = False
    for name, (_, loader, live) in graphs.items():
        live_pods = _pods(live)
        for pod_name, pod in _pods(loader).items():
            wanted = _reasons(pod)
            expected_somewhere |= bool(wanted)
            assert wanted == _reasons(live_pods[pod_name]), (name, pod_name)
    assert expected_somewhere


def test_oom_kill_rule_fires_on_the_live_graph(graphs):
    """The consequence of divergence 3: the rule could not fire on a live graph."""
    from rca.remediation_engine import RemediationEngine
    _, _, live = graphs["h003_oomkilled"]
    assert "oom_kill" in {h.rule_id for h in RemediationEngine().score(live)}
