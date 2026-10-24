"""Every committed record holds what docs/incident-corpus.md requires, and is kept apart from the
native cases (tools/corpus_exclusions.py)."""
from __future__ import annotations

import pytest
import yaml

from tools import corpus_exclusions as ce
from tools import incident_corpus as ic

RECORDS = ic.load_records()


def test_the_corpus_v1_is_committed():
    # docs/incident-corpus.md § Amendment 2026-10-24: one record per kept legacy source
    kept = [v for v in ce.run()[0] if v.kept]
    assert len(RECORDS) == len(kept) and {r["source"]["ref"] for _, r in RECORDS} == {v.candidate for v in kept}


@pytest.mark.parametrize("path, rec", RECORDS, ids=[p.stem for p, _ in RECORDS])
def test_a_record_holds_what_the_spec_requires(path, rec):
    assert ic.problems(path, rec) == []


def test_no_record_is_excluded():
    _, records = ce.run()
    assert [v for v in records if not v.kept] == []


def test_the_exclusions_file_is_up_to_date():
    assert (ce.ROOT / "corpus/EXCLUSIONS.md").read_text() == ce.render(*ce.run())


GOOD = {"id": "inc-0001", "title": "t", "family": "oom", "origin": "synthetic",
        "source": {"kind": "legacy-case", "ref": "cases/007_prometheus_alert"},
        "symptoms": ["pods restart every few minutes", "last state OOMKilled, exit code 137"],
        "signals": ["k8s_events"], "kinds": ["Pod"], "root_cause": "r", "cause_location": "production/Deployment/x",
        "evidence": ["e"], "remediation": ["helm upgrade x ./chart --set resources.limits.memory=1Gi"],
        "rollback": ["helm rollback x"]}


def _write(tmp_path, **changes):
    rec = {**GOOD, **changes}
    p = tmp_path / f"{rec['id']}.yaml"
    p.write_text(yaml.safe_dump(rec))
    return p, rec


def test_a_sound_record_has_no_problem(tmp_path):                                    # control
    assert ic.problems(*_write(tmp_path)) == []


@pytest.mark.parametrize("changes, problem", [
    ({"symptoms": ["the deployed limit drifted from values", "x"]}, "symptom names the cause (drift)"),
    ({"symptoms": ["the memory limit is reached", "x"]}, "symptom names the cause (memory limit"),
    ({"family": "config-missing", "symptoms": ['configmap "x" not found', "y"]}, "symptom names the cause (not found)"),
    ({"symptoms": ["only one symptom"]}, "fewer than 2 symptoms"),
    ({"family": "flaky"}, "family 'flaky' not in the controlled list"),
    ({"remediation": ["kubectl get pods -n production", "kubectl describe pod x"]}, "remediation is read-only"),
    ({"rollback": []}, "no rollback and no rollback_note"),
    ({"source": {"kind": "legacy-case", "ref": "cases/999_none"}}, "does not resolve"),
    ({"id": "inc-7"}, "is not inc-NNNN"),
    ({"evidence": []}, "missing evidence"),
])
def test_a_broken_record_is_refused(tmp_path, changes, problem):
    assert any(problem in p for p in ic.problems(*_write(tmp_path, **changes)))


def test_a_rollback_note_stands_for_a_rollback(tmp_path):
    assert ic.problems(*_write(tmp_path, rollback=[], rollback_note="nothing to undo")) == []


def test_the_corpus_hash_changes_with_any_record(tmp_path):
    assert ic.corpus_hash(tmp_path) is None
    _write(tmp_path)
    h1 = ic.corpus_hash(tmp_path)
    _write(tmp_path, title="t2")
    assert h1 and ic.corpus_hash(tmp_path) != h1
    _write(tmp_path, title="t")
    assert ic.corpus_hash(tmp_path) == h1
