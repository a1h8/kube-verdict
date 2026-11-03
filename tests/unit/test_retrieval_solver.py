"""R0: the corpus alone through the product's example path — docs/protocol-001-retrieval.md,
amendment 2026-10-24 (CPI-22)."""
from __future__ import annotations

import json

import yaml

from tools import incident_corpus as ic
from tools import veracity_replay as vr
from workflow import nodes

H001 = "h001_crashloopbackoff"          # query: "payment-service pods keep crashing in production"
H022 = "h022_no_incident"


def _corpus(tmp_path, *titles):
    d = tmp_path / "incidents"
    d.mkdir(parents=True)
    for i, title in enumerate(titles, 1):
        rec = {"id": f"inc-{i:04d}", "title": title, "family": "config-missing", "origin": "synthetic",
               "source": {"kind": "legacy-case", "ref": "cases/008_missing_configmap"},
               "symptoms": [title, "pods restart"], "signals": ["k8s_events"], "kinds": ["Pod"],
               "root_cause": f"the Secret the pods read was deleted ({i})", "cause_location": "production/Secret/x",
               "evidence": ["e"], "remediation": ["kubectl create secret generic x"], "rollback": ["kubectl delete secret x"]}
        (d / f"inc-{i:04d}.yaml").write_text(yaml.safe_dump(rec))
    return d


def test_a_record_maps_onto_the_products_incident():
    _, rec = ic.load_records()[0]
    inc = ic.to_incident(rec)
    assert inc.id == rec["id"] and inc.query.startswith(rec["title"]) and rec["symptoms"][0] in inc.query
    assert inc.entity_kinds == rec["kinds"] and inc.remediation == rec["remediation"] and inc.confidence == ""


def test_the_top_record_answers_above_the_threshold(tmp_path, monkeypatch):
    monkeypatch.setattr(nodes, "EXAMPLE_MATCH_THRESHOLD", 0.0)
    r = vr.solve_retrieval(H001, _corpus(tmp_path, "payment-service pods keep crashing in production",
                                         "nightly backup job takes too long"))
    assert r["matched"] == "inc-0001" and r["root_cause"].endswith("(1)") and r["confidence"] == "HIGH"
    assert [t["id"] for t in r["top"]][0] == "inc-0001" and r["corpus_records"] == 2


def test_below_the_threshold_r0_abstains(tmp_path, monkeypatch):
    monkeypatch.setattr(nodes, "EXAMPLE_MATCH_THRESHOLD", 1e9)
    r = vr.solve_retrieval(H001, _corpus(tmp_path, "payment-service pods keep crashing in production"))
    assert r["matched"] is None and r["root_cause"] == "" and r["confidence"] == "LOW"
    assert r["veracity"] == "FAIL" and r["top"]                     # scored, not taken


def test_an_abstention_passes_a_negative_case(tmp_path, monkeypatch):                # control
    monkeypatch.setattr(nodes, "EXAMPLE_MATCH_THRESHOLD", 1e9)
    assert vr.solve_retrieval(H022, _corpus(tmp_path, "anything at all"))["veracity"] == "PASS"
    monkeypatch.setattr(nodes, "EXAMPLE_MATCH_THRESHOLD", 0.0)            # always answers: a false alarm
    assert vr.solve_retrieval(H022, _corpus(tmp_path / "b", "anything at all"))["veracity"] == "FAIL"


def test_an_empty_corpus_abstains_everywhere(tmp_path):
    (tmp_path / "incidents").mkdir()
    r = vr.solve_retrieval(H001, tmp_path / "incidents")
    assert r["matched"] is None and r["confidence"] == "LOW" and r["corpus_records"] == 0 and r["top"] == []


def test_the_threshold_is_the_products():
    assert nodes.EXAMPLE_MATCH_THRESHOLD == 0.65                 # fixed before any run (CPI-22)


def test_a_retrieval_run_records_r0_and_the_corpus_hash(tmp_path):
    out = tmp_path / "retrieval-store_faiss-examples_on"
    assert vr.main(["--solver", "retrieval", "--cases", "h001", "--out", str(out)]) == 0
    rec = json.loads((out / "retrieval" / H001 / "run_1.json").read_text())
    assert rec["config"] == "R0" and rec["store"] == "faiss" and rec["examples"] == "on"
    assert rec["corpus_hash"] == ic.corpus_hash() and rec["invalid"] == [] and rec["solver"] == "retrieval"


def test_a_retrieval_run_cannot_cut_a_collector(tmp_path, capsys):
    import pytest
    with pytest.raises(SystemExit):
        vr.main(["--solver", "retrieval", "--cut", "loki", "--out", str(tmp_path / "x"), "--out-free"])
