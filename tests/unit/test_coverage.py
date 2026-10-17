"""The coverage matrix stays checkable — docs/diagnosis-coverage.md, tools/coverage.py."""
from __future__ import annotations

import re
from pathlib import Path

from tools.coverage import evaluate, load_rows, runbook_names

CASES = Path(__file__).resolve().parents[1] / "integration" / "cases"
FAMILIES_DOC = Path(__file__).resolve().parents[2] / "docs" / "incident-corpus.md"


def test_rows_cite_only_runbooks_of_the_pinned_catalog():
    names = set(runbook_names())
    assert len(names) == 60
    for r in load_rows():
        assert set(r.get("runbooks", [])) <= names, r["id"]
        assert not r.get("runbooks") or r.get("terms"), f"{r['id']}: a cited runbook needs terms to check"


def test_rows_cite_only_existing_cases():
    existing = {d.name.split("_", 1)[0] for d in CASES.glob("h0*/")}
    for r in load_rows():
        assert set(r.get("cases", [])) <= existing, r["id"]


def test_families_are_in_the_controlled_list():
    text = FAMILIES_DOC.read_text()
    controlled = set(re.findall(r"`([a-z-]+)`", text[text.index("`family` (controlled"):]))
    for r in load_rows():
        assert r["family"] in controlled, r["id"]


def test_ids_are_unique_and_live_is_judged_with_a_reason():
    rows = load_rows()
    assert len({r["id"] for r in rows}) == len(rows)
    for r in rows:
        assert r["live"] in ("yes", "partial", "no") and r.get("live_note"), r["id"]


def test_a_row_is_covered_only_with_signals_a_rule_and_a_passing_case():
    rows = [{"id": "x", "family": "oom", "signals": ["container.oom_killed"], "rule": "oom.limit",
             "cases": ["h012"], "live": "yes", "live_note": "-"}]
    assert evaluate(rows, passed=set())[0]["covered"] is False
    assert evaluate(rows, passed={"h012"})[0]["covered"] is True
    rows[0]["rule"] = None
    assert evaluate(rows, passed={"h012"})[0]["covered"] is False
