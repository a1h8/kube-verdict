"""A graded case is never rewritten from a run's output — docs/fix-plan-001.md, H75."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tools import case_contract
from tools.case_contract import is_graded_case, recalibrate_all, update_expect_from_sim

ROOT = Path(__file__).resolve().parents[2]
SIM = {"tree": {"score": 0.91, "raw_analysis": "memory limit drift OOMKilled",
                "root_cause": "memory limit drift", "remediation": ["helm upgrade api --set x=1"]}}


def test_the_native_cases_are_graded():
    assert is_graded_case(ROOT / "tests/integration/cases/h003_oomkilled")
    assert not is_graded_case(ROOT / "cases/003_oomkilled")                      # control


def test_updating_a_graded_case_from_a_run_is_refused():
    case = ROOT / "tests/integration/cases/h003_oomkilled"
    before = (case / "expect.json").read_text()
    with pytest.raises(ValueError, match="graded case"):
        update_expect_from_sim(case, SIM, dry_run=False)
    assert (case / "expect.json").read_text() == before


def test_recalibrate_all_skips_graded_cases(tmp_path, monkeypatch):
    graded_root = tmp_path / "graded"
    shutil.copytree(ROOT / "tests/integration/cases/h003_oomkilled", graded_root / "h003_oomkilled")
    synthetic = tmp_path / "synthetic"
    shutil.copytree(ROOT / "cases/003_oomkilled", synthetic / "h003_oomkilled")
    sims = tmp_path / "sims"
    sims.mkdir()
    (sims / "h003_oomkilled.json").write_text(json.dumps(SIM))
    monkeypatch.setattr(case_contract, "GRADED_CASES_ROOT", graded_root.resolve())

    before = (graded_root / "h003_oomkilled/expect.json").read_text()
    assert recalibrate_all(sims, graded_root) == {}
    assert (graded_root / "h003_oomkilled/expect.json").read_text() == before
    # control: the same run still recalibrates a synthetic case
    assert recalibrate_all(sims, synthetic)["h003_oomkilled"]


def test_the_ui_hides_the_update_for_graded_cases():
    # read the source rather than import ui.app (Streamlit, 3 800 lines)
    src = (ROOT / "ui" / "app.py").read_text()
    block = src[src.index("with ac2:"):src.index("with ac3:")]
    assert block.index("is_graded_case(case_dir)") < block.index("update_expect_from_sim(")
