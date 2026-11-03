"""The values diff shown for approval never starts from values it could not read —
docs/fix-plan-001.md, H13."""
from __future__ import annotations

import subprocess
from unittest.mock import patch

from workflow.nodes import _helm_values_diff

SET = ["--set", "image.tag=1.2.3"]


def _run(stdout="", stderr="", rc=0):
    return subprocess.CompletedProcess([], rc, stdout=stdout, stderr=stderr)


def test_a_failed_read_gives_no_diff():
    with patch("subprocess.run", return_value=_run(stderr="Error: release: not found", rc=1)):
        cmd, out, rc = _helm_values_diff("api", ["-n", "prod"], SET)
    assert rc == 1 and "unreadable" in out and "release: not found" in out
    assert "+ image.tag" not in out


def test_a_missing_binary_gives_no_diff():
    with patch("subprocess.run", side_effect=FileNotFoundError("helm")):
        _, out, rc = _helm_values_diff("api", [], SET)
    assert rc == 1 and "unreadable" in out


def test_no_user_values_is_an_empty_baseline():
    with patch("subprocess.run", return_value=_run(stdout="null\n")):
        _, out, rc = _helm_values_diff("api", [], SET)
    assert rc == 0 and "+ image.tag: 1.2.3" in out


def test_a_readable_value_is_diffed():                                   # control
    with patch("subprocess.run", return_value=_run(stdout='{"image": {"tag": "1.2.2"}}')):
        _, out, rc = _helm_values_diff("api", [], SET)
    assert rc == 0 and "- image.tag: 1.2.2" in out and "+ image.tag: 1.2.3" in out
