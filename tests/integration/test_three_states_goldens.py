"""The committed renders of the native cases are what `helm template` gives for their chart —
docs/three-states.md § Replay (as tests/integration/test_render_vs_live_h012.py does for h012)."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CASES = [d for d in sorted(Path(__file__).parent.joinpath("cases").glob("h0*"))
         if (d / "helm" / "manifest.yaml").exists()]


def _docs(text):
    return [d for d in yaml.safe_load_all(text) if isinstance(d, dict)]


def _render(case_dir, *extra):
    expect = json.loads((case_dir / "expect.json").read_text())
    return subprocess.run(["helm", "template", expect["release"], str(case_dir / "chart"),
                           "--namespace", expect.get("namespace", "default"), *extra],
                          capture_output=True, text=True, check=True).stdout


@pytest.mark.skipif(shutil.which("helm") is None, reason="needs the helm binary")
@pytest.mark.parametrize("case_dir", CASES, ids=lambda d: d.name.split("_")[0])
def test_goldens_are_renders_of_the_chart(case_dir):
    applied = _render(case_dir, "-f", str(case_dir / "helm" / "release.json"))
    assert _docs(applied) == _docs((case_dir / "helm" / "manifest.yaml").read_text())
    declared = case_dir / "rendered" / "declared.yaml"
    if declared.exists():
        assert _docs(_render(case_dir)) == _docs(declared.read_text())
