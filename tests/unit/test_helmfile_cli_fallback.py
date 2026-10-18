"""When `helmfile build` fails, the collector parses the YAML it said it would —
docs/fix-plan-001.md, H27 (it returned no release at all)."""
from __future__ import annotations

import subprocess
from unittest.mock import patch

from ingestion.helmfile_collector import HelmfileCollector

HELMFILE = """
releases:
  - name: api
    namespace: prod
    chart: ./charts/api
"""


def _collector(tmp_path):
    f = tmp_path / "helmfile.yaml"
    f.write_text(HELMFILE)
    return HelmfileCollector(helmfile_path=str(f), environment="default", use_cli=True)


def test_a_failed_build_falls_back_to_the_yaml(tmp_path):
    c = _collector(tmp_path)
    err = subprocess.CalledProcessError(1, "helmfile", stderr="boom")
    with patch.object(HelmfileCollector, "_helmfile_available", return_value=True), \
            patch("subprocess.run", side_effect=err):
        specs = c._load_specs()
    assert [r["name"] for s in specs for r in s.get("releases", [])] == ["api"]


def test_a_working_build_is_used(tmp_path):                                # control
    c = _collector(tmp_path)
    built = subprocess.CompletedProcess([], 0, stdout='{"releases": [{"name": "built"}]}')
    with patch.object(HelmfileCollector, "_helmfile_available", return_value=True), \
            patch("subprocess.run", return_value=built):
        specs = c._load_specs()
    assert [r["name"] for s in specs for r in s.get("releases", [])] == ["built"]
