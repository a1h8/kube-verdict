"""S2-GitOps' fixtures (docs/s2g/gitops-cases.md): valid against the definitions with no omission, every hop's
evidence present, the cause object in the fixtures, and the files unchanged since the freeze."""
import json
from pathlib import Path

import pytest
import yaml

from ontology.schema_registry import SchemaRegistry

ROOT = Path(__file__).resolve().parents[2] / "tests" / "s2g"
CASES = sorted(p for p in (ROOT / "cases").iterdir() if p.is_dir())


def test_six_cases():
    assert [c.name.split("_")[0] for c in CASES] == ["g0", "g1", "g2", "g3", "g4", "g5"]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_valid_against_the_definitions_with_no_omission(case):
    from tools.validate_fixtures import check_case
    n, problems = check_case(SchemaRegistry.bundled(), case)
    assert n > 50 and problems == []


def test_every_hop_has_its_evidence():
    evidence = yaml.safe_load((ROOT / "evidence.yaml").read_text())
    assert set(evidence) == {c.name for c in CASES if not c.name.startswith("g0")}
    for case, hops in evidence.items():
        assert len(hops) >= 2, case
        for hop in hops:
            text = (ROOT / "cases" / case / hop["file"]).read_text()
            assert hop["contains"] in text, (case, hop["hop"])


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_cause_objects_are_in_the_fixtures(case):
    expect = json.loads((case / "expect.json").read_text())
    assert expect["set"] == "S2-GitOps"
    causes = expect.get("cause_objects") or ([expect["cause_object"]] if expect["cause_object"] else [])
    assert (expect["expected_outcome"] == "no_incident") == (not causes)
    text = "\n".join(p.read_text() for p in (case / "kube").glob("*.yaml"))
    for c in causes:
        kind, ns, name = c.split("/")
        assert f"kind: {kind}" in text and f"name: {name}" in text and f"namespace: {ns}" in text, c


def test_frozen():
    from tests.s2.frozen import case_hash
    frozen = json.loads((ROOT / "frozen.json").read_text())["cases"]
    assert frozen == {c.name: case_hash(c) for c in CASES}
