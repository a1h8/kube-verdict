"""The test suite writes no real store or database — docs/fix-plan-001.md, H81."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# the files that wrote sessions into ./kubeverdict.db, or created it, before H81
WRITERS = ["tests/unit/test_webhook_alert_text.py", "tests/unit/test_api_kube_target.py",
           "tests/unit/test_session_routes_auth.py",
           # collecting it opened ./kubeverdict.db: pytest inspects api.session_store's lazy proxy
           "tests/integration/api/test_api_sessions.py"]


def test_every_store_points_at_the_session_temp_dir(isolated_store_root):
    import config as cfg
    from knowledge.chart_store import ChartStore
    from knowledge.doc_store import DocStore
    from knowledge.example_store import default_example_dir
    from persistence.db import db_path

    for path in (Path(db_path()), default_example_dir(), DocStore()._dir, ChartStore()._dir,
                 Path(cfg.VECTOR_STORE_PATH)):
        assert path.is_relative_to(isolated_store_root), path


def test_the_former_writers_leave_nothing_where_they_run(tmp_path):
    env = {k: v for k, v in os.environ.items()
           if k not in ("KUBEWHISPERER_DB", "KUBEVERDICT_EXAMPLE_DIR", "KUBEVERDICT_CHART_DIR")}
    env.update(PYTHONPATH=str(ROOT), HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                           "-m", "not integration", "--rootdir", str(ROOT),
                           *(str(ROOT / w) for w in WRITERS)],
                          cwd=tmp_path, env=env, capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stdout[-2000:]
    assert sorted(p.name for p in tmp_path.iterdir()) == []
