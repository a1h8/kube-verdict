"""A chart archive cannot write outside its temporary directory — docs/fix-plan-001.md, H36."""
from __future__ import annotations

import io
import tarfile

from ingestion.chart_parser import ChartParser


def _tgz(path, members):
    with tarfile.open(path, "w:gz") as tar:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


def test_a_member_with_dot_dot_is_not_written_outside(tmp_path):
    escaped = tmp_path / "escaped.txt"
    archive = tmp_path / "evil.tgz"
    # tempfile dirs live one level under the system temp dir: climb far enough to reach tmp_path
    _tgz(archive, [("app/Chart.yaml", b"name: app\nversion: 1.0.0\n"),
                   ("../" * 12 + str(escaped).lstrip("/"), b"pwned")])
    ChartParser().from_tgz(archive)
    assert not escaped.exists()


def test_a_normal_chart_still_parses(tmp_path):
    archive = tmp_path / "app.tgz"
    _tgz(archive, [("app/Chart.yaml", b"name: app\nversion: 1.0.0\n"),
                   ("app/values.yaml", b"replicaCount: 2\n")])
    chart = ChartParser().from_tgz(archive)
    assert chart is not None and chart.name == "app"                                 # control
