"""The generic `valid` anchor evaluator (docs/s2g/gitops-cases.md, change 2). Fresh fixtures."""
from ontology.anchors import _valid
from ontology.discovery import APIResourceInfo
from ontology.dynamic_entity import GenericEntity

ANCHOR = {"params": {"field": "status.operationState.phase", "values": ["Running", "Succeeded"],
                     "message": "status.operationState.message"}}


def _obj(status, kind="Application"):
    info = APIResourceInfo(group="argoproj.io", version="v1alpha1", kind=kind, plural=kind.lower() + "s",
                           namespaced=True, verbs=["list"])
    return GenericEntity.from_api_object({"apiVersion": "argoproj.io/v1alpha1", "metadata": {
        "name": "a", "namespace": "argocd", "uid": "a"}, "status": status}, info)


def test_fires_on_a_value_outside_the_list_and_quotes_the_message():
    o = _obj({"operationState": {"phase": "Failed", "message": "Ingress is invalid"}})
    found = _valid(None, o, ANCHOR, None, None)
    assert found and found[1] is o
    assert "phase=Failed" in found[0] and "Ingress is invalid" in found[0]


def test_silent_on_a_listed_value():
    assert _valid(None, _obj({"operationState": {"phase": "Succeeded"}}), ANCHOR, None, None) is None


def test_no_claim_without_the_field():
    assert _valid(None, _obj({}), ANCHOR, None, None) is None


def test_no_message_path_means_no_quote():
    a = {"params": {"field": "status.operationState.phase", "values": ["Succeeded"]}}
    found = _valid(None, _obj({"operationState": {"phase": "Error", "message": "boom"}}), a, None, None)
    assert found and "boom" not in found[0]
