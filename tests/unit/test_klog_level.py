"""klog severity is read — docs/fix-plan-001.md, H50 (client-go error lines were "info")."""
from __future__ import annotations

import pytest

from ingestion.loki_source import _detect_level

KLOG_E = ('E0514 11:01:00.000000       1 reflector.go:147] failed to list *v1.Pod: pods is forbidden: '
          'User "system:serviceaccount:monitoring:sa" cannot list resource "pods"')


@pytest.mark.parametrize("line,level", [
    (KLOG_E, "error"),
    ("W0514 11:01:00.000000 1 x.go:1] slow response", "warn"),
    ("F0514 11:01:00.000000 1 x.go:1] cannot start", "error"),
    ("I0514 11:01:00.000000 1 x.go:1] started, no errors so far", "info"),     # klog wins over words
    ("level=error msg=boom", "error"),                                         # control: other formats
    ("serving request status=200", "info"),
])
def test_level(line, level):
    assert _detect_level(line) == level
