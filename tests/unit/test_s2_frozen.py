"""S2's cases are frozen (docs/s2-correlation-cases.md, step 4): none changed since its hash was written."""
from __future__ import annotations

import json
import shutil

from tests.s2.frozen import CASES, FROZEN, case_hash, hashes


def test_every_case_is_frozen_and_unchanged():
    frozen = json.loads(FROZEN.read_text())["cases"]
    assert len(frozen) == 21
    assert hashes() == frozen


def test_a_single_changed_byte_changes_the_hash(tmp_path):                 # control
    src = CASES / "q1_quota_filled_by_request_drift"
    copy = tmp_path / src.name
    shutil.copytree(src, copy)
    assert case_hash(copy) == case_hash(src)
    p = copy / "kube/quota.yaml"
    p.write_text(p.read_text().replace("31Gi", "30Gi"))
    assert case_hash(copy) != case_hash(src)
