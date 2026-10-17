"""Grading v2 (tools/veracity.py) — docs/scenarios-v2.md § Grading v2."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.b13_capture import GRADING_KEYS, SCENARIOS
from tools.veracity import grade, normalize_label

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "tests/integration/cases"
VERACITY = ROOT / "tests/veracity"

CERT = {"root_cause_contains": ["certificate", "expired", "x509", "tls"],
        "root_cause_must_not_contain": ["memory", "oom"]}


class TestCause:
    def test_half_the_keywords_pass(self):
        r = grade("The x509 certificate expired", CERT)
        assert r["veracity"] == "PASS"
        assert r["matched_keywords"] == ["certificate", "expired", "x509"]
        assert r["forbidden_matched"] == []

    def test_below_threshold_fails(self):
        assert grade("TLS handshake fails", CERT)["veracity"] == "FAIL"

    def test_min_one_keyword(self):
        assert grade("etcd is slow", {"root_cause_contains": ["etcd"]})["veracity"] == "PASS"

    def test_case_insensitive(self):
        assert grade("X509 CERTIFICATE EXPIRED", CERT)["veracity"] == "PASS"

    def test_decoy_fails_even_with_the_right_keywords(self):
        r = grade("x509 certificate expired, and a memory leak", CERT)
        assert r["veracity"] == "FAIL"
        assert r["forbidden_matched"] == ["memory"]
        assert "decoy" in r["reason"]

    def test_without_must_not_contain_rule_is_unchanged(self):
        expect = {"root_cause_contains": CERT["root_cause_contains"]}
        assert grade("x509 certificate expired, and a memory leak", expect)["veracity"] == "PASS"

    def test_confidence_is_ignored(self):
        assert grade("x509 certificate expired", CERT, confidence="LOW")["veracity"] == "PASS"

    def test_no_keywords_is_a_spec_error(self):
        with pytest.raises(ValueError):
            grade("anything", {"root_cause_contains": []})

    def test_unknown_outcome_is_a_spec_error(self):
        with pytest.raises(ValueError):
            grade("anything", {"expected_outcome": "maybe"})


class TestContainsAll:
    EXPECT = {"root_cause_contains_all": [["secret", "queue-credentials", "missing"],
                                          ["oom", "memory", "limit", "256mi"]]}

    def test_both_causes_pass(self):
        r = grade("Secret queue-credentials is missing; api OOM: memory limit 256Mi", self.EXPECT)
        assert r["veracity"] == "PASS"
        assert len(r["matched_groups"]) == 2

    def test_one_cause_only_fails(self):
        r = grade("Secret queue-credentials is missing for worker", self.EXPECT)
        assert r["veracity"] == "FAIL"
        assert "[1]" in r["reason"]

    def test_replaces_root_cause_contains(self):
        expect = {**self.EXPECT, "root_cause_contains": ["unrelated"]}
        r = grade("Secret queue-credentials is missing; api OOM: memory limit 256Mi", expect)
        assert r["veracity"] == "PASS"


@pytest.mark.parametrize("outcome", ["no_incident", "insufficient_evidence"])
class TestNegative:
    def expect(self, outcome):
        return {"expected_outcome": outcome, "root_cause_must_not_contain": ["crash", "oom"]}

    def test_low_and_no_cause_passes(self, outcome):
        r = grade("No clear incident in the evidence", self.expect(outcome), confidence="low")
        assert r["veracity"] == "PASS"

    def test_high_fails(self, outcome):
        r = grade("No clear incident", self.expect(outcome), confidence="HIGH")
        assert r["veracity"] == "FAIL"
        assert "HIGH" in r["reason"]

    def test_missing_label_fails(self, outcome):
        assert grade("No clear incident", self.expect(outcome))["veracity"] == "FAIL"

    def test_low_but_names_a_cause_fails(self, outcome):
        r = grade("Probably an OOM crash", self.expect(outcome), confidence="LOW")
        assert r["veracity"] == "FAIL"
        assert r["forbidden_matched"] == ["crash", "oom"]


class TestExpectedVerdict:
    EXPECT = {"expected_outcome": "no_incident", "expected_verdict": "HUMAN_REVIEW"}

    def test_matching_verdict_passes(self):
        assert grade("No incident", self.EXPECT, confidence="LOW",
                     verdict="HUMAN_REVIEW")["veracity"] == "PASS"

    @pytest.mark.parametrize("verdict", ["NO_GO", "AUTO", ""])
    def test_other_verdict_fails(self, verdict):
        r = grade("No incident", self.EXPECT, confidence="LOW", verdict=verdict)
        assert r["veracity"] == "FAIL"
        assert "HUMAN_REVIEW expected" in r["reason"]

    def test_wrong_label_reason_wins(self):
        r = grade("No incident", self.EXPECT, confidence="HIGH", verdict="NO_GO")
        assert "LOW expected" in r["reason"]

    def test_unset_verdict_is_not_checked(self):
        assert grade("x509 certificate expired", CERT, verdict="NO_GO")["veracity"] == "PASS"


class TestLiveCorpus:
    @pytest.mark.parametrize("scenario_id", [k for k, v in SCENARIOS.items()
                                             if any(g in v for g in GRADING_KEYS)])
    def test_capture_registry_matches_expect_json(self, scenario_id):
        """SCENARIOS copies its grading keys from expect.json; they must not drift."""
        expect = json.loads((CASES / scenario_id / "expect.json").read_text())
        scenario = SCENARIOS[scenario_id]
        for key in GRADING_KEYS:
            assert scenario.get(key) == expect.get(key), key

    @pytest.mark.parametrize("capture,decoy", [("real_003", "memory"), ("real_004", "image")])
    def test_failed_captures_still_fail_and_name_the_decoy(self, capture, decoy):
        record = json.loads((VERACITY / f"{capture}.json").read_text())
        expect = json.loads((CASES / record["scenario_id"] / "expect.json").read_text())
        r = grade(record["actual_root_cause"], expect)
        assert r["veracity"] == record["veracity"] == "FAIL"
        assert decoy in r["forbidden_matched"]


def test_write_veracity_records_confidence_next_to_the_grade(tmp_path, monkeypatch):
    import tools.b13_capture as b13

    monkeypatch.setattr(b13, "VERACITY_DIR", tmp_path)
    summary = {
        "root_cause": "A memory issue with the billing-gateway pods",
        "confidence": "HIGH",
        "pre_llm_confidence": {"score": 0.54},
        "verdict": "HUMAN_REVIEW",
        "_ingestion_stats": {"loki": {"fallback": True, "error": "down"},
                             "prometheus": {"fallback": False}},
    }
    path = b13.write_veracity(9, "h014_cert_expiry", summary, SCENARIOS["h014_cert_expiry"])
    record = json.loads(path.read_text())
    assert path.name == "real_009.json"
    assert record["veracity"] == "FAIL"
    assert record["forbidden_matched"] == ["memory"]
    assert record["root_cause_must_not_contain"] == ["memory", "oom"]
    assert record["confidence"] == "HIGH"
    assert record["pre_llm_confidence_score"] == 0.54
    assert record["verdict"] == "HUMAN_REVIEW"
    assert record["fallbacks"] == ["loki"]


@pytest.mark.parametrize("raw,label", [
    ("LOW", "LOW"), ("MEDIUM.", "MEDIUM"), ("high — the logs agree", "HIGH"),
    ("LOW (rule-assisted — top: oom_kill w=0.88, 3 hypothesis(es))", "LOW"),
    ("", ""), ("  ", ""),
])
def test_normalize_label(raw, label):
    assert normalize_label(raw) == label


def test_decorated_low_label_passes_a_negative_case():
    expect = {"expected_outcome": "no_incident"}
    assert grade("No incident", expect, confidence="LOW.")["veracity"] == "PASS"


class TestSynonyms:
    """docs/evidence-score.md § Prerequisites 1: right answers in other words."""

    def test_h004_answer_from_the_baseline_now_passes(self):
        expect = {"root_cause_contains": ["configmap", "api-gateway-config", "not found", "missing"]}
        answer = ("The root cause is that the necessary ConfigMap and Secret have not been "
                  "created or are not properly linked to the Pod.")
        assert grade(answer, expect)["veracity"] == "PASS"

    def test_h005_answer_from_the_baseline_now_passes(self):
        expect = {"root_cause_contains": ["forbidden", "serviceaccount", "audit-exporter-sa",
                                          "rbac", "cannot list"]}
        answer = ("The service account `audit-exporter-sa` in the `monitoring` namespace does "
                  "not have the necessary permissions to list and watch pods.")
        assert grade(answer, expect)["veracity"] == "PASS"

    def test_matched_keywords_are_reported_as_authored(self):
        r = grade("the secret was never created", {"root_cause_contains": ["secret", "missing"]})
        assert r["matched_keywords"] == ["secret", "missing"]

    def test_inline_alternatives(self):
        expect = {"root_cause_contains": ["pool|max_connections"]}
        assert grade("max_connections reached", expect)["veracity"] == "PASS"

    def test_synonyms_apply_to_the_decoy_too(self):
        expect = {"root_cause_contains": ["x509"], "root_cause_must_not_contain": ["missing"]}
        assert grade("x509 cert, the secret is absent", expect)["veracity"] == "FAIL"

    def test_a_wrong_answer_stays_wrong(self):
        expect = {"root_cause_contains": ["certificate", "expired", "x509", "tls"]}
        assert grade("Helm drift on replicaCount", expect)["veracity"] == "FAIL"


def test_synonyms_come_from_the_data_file_not_the_code(tmp_path, monkeypatch):
    from tools import veracity
    custom = tmp_path / "syn.yaml"
    custom.write_text("missing:\n  - vanished\n")
    monkeypatch.setenv("VERACITY_SYNONYMS", str(custom))
    veracity.synonyms.cache_clear()
    try:
        expect = {"root_cause_contains": ["missing"]}
        assert grade("the secret vanished", expect)["veracity"] == "PASS"
        assert grade("the secret is absent", expect)["veracity"] == "FAIL"   # not in this file
    finally:
        monkeypatch.delenv("VERACITY_SYNONYMS")
        veracity.synonyms.cache_clear()
