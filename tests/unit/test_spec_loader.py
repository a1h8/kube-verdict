"""The declared ontology's loader (ontology/spec_loader.py): the real files load, and each kind of mistake is refused."""
from __future__ import annotations

import pytest

from ontology.spec_loader import SpecError, load, parse_anchors, parse_relations


def test_the_declared_files_load():
    rel, anc = load()
    assert {r.name for r in rel} >= {"OWNS", "EXPOSES", "CONFIGURES", "QUOTA_BLOCKS", "LIMITS", "MOUNTS_CONFIGMAP"}
    assert {a.id for a in anc} >= {"quota.within-hard", "limitrange.default-fits", "configmap.env-fresh"}
    assert all(r.sources for r in rel) and all(a.sources for a in anc)


_OK = {"name": "OWNS", "source_kind": "*", "target_kind": "*", "matcher": "owner_reference", "params": {},
       "sources": ["https://kubernetes.io/x"]}


@pytest.mark.parametrize("change, reason", [
    ({"name": "NOT_A_RELATION"}, "not a relation"),
    ({"matcher": "guess_by_name"}, "unknown matcher"),
    ({"sources": []}, "sources"),
    ({"matcher": "name_reference", "params": {}}, "missing params"),
    ({"matcher": "event_quote", "params": {"reasons": ["X"], "pattern": "no group", "link": "both"}}, "named group"),
    ({"matcher": "event_quote", "params": {"reasons": ["X"], "pattern": "(?P<name>x)", "link": "nowhere"}}, "link"),
])
def test_a_wrong_relation_is_refused(change, reason):
    with pytest.raises(SpecError) as err:
        parse_relations({"relations": [{**_OK, **change}]})
    assert any(reason in r for r in err.value.reasons)


_A = {"id": "a", "kind": "ResourceQuota", "type": "bound", "params": {"left": "x", "right": "y"},
      "variability": "volatile", "sources": ["https://kubernetes.io/x"]}


@pytest.mark.parametrize("change, reason", [
    ({"type": "looks_wrong"}, "unknown anchor type"),
    ({"variability": "sometimes"}, "unknown variability"),
    ({"params": {"left": "x"}}, "missing params"),
    ({"sources": ["not a url"]}, "sources"),
])
def test_a_wrong_anchor_is_refused(change, reason):
    with pytest.raises(SpecError) as err:
        parse_anchors({"anchors": [{**_A, **change}]})
    assert any(reason in r for r in err.value.reasons)


def test_duplicate_anchor_ids_are_refused():
    with pytest.raises(SpecError):
        parse_anchors({"anchors": [_A, dict(_A)]})


def test_a_controller_variability_names_its_controller_and_the_hop_to_it():
    ok = {**_A, "variability": "controller:hpa", "params": {"left": "x", "right": "y", "hop": "SCALES"}}
    assert parse_anchors({"anchors": [ok]})[0].variability == "controller:hpa"
    with pytest.raises(SpecError):                       # no hop
        parse_anchors({"anchors": [{**_A, "variability": "controller:hpa"}]})
    with pytest.raises(SpecError):                       # a hop that is no relation
        parse_anchors({"anchors": [{**ok, "params": {"left": "x", "right": "y", "hop": "GUESSED"}}]})
