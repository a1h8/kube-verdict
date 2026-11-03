"""`helm template` gets the values as written, and a rendered object is compared with the live
object of its own namespace — docs/fix-plan-001.md, H32 and H29."""
from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

from ingestion.manifest_differ import ManifestDiffer
from ingestion.manifest_renderer import ManifestRenderer
from ontology.entities import Deployment
from ontology.graph import OntologyGraph

VALUES = {"args": ["--port", "8080"], "env": {"JAVA_OPTS": "-Xms1g,-Xmx2g"}, "build": "0123",
          "image": {"tag": "1.2.3"}}


def test_values_reach_helm_unchanged(monkeypatch):
    seen = {}

    def fake_run(cmd, **_):
        files = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-f"]
        seen["cmd"], seen["values"] = cmd, yaml.safe_load(Path(files[-1]).read_text())
        seen["file"] = files[-1]
        return subprocess.CompletedProcess(cmd, 0, stdout="kind: ConfigMap\nmetadata: {name: x}\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    docs = ManifestRenderer().render("./chart", "api", "prod", values=VALUES, value_files=["base.yaml"])
    assert docs and seen["values"] == VALUES                 # lists, commas, leading zeros intact
    assert "--set" not in seen["cmd"]
    assert seen["cmd"].index("base.yaml") < seen["cmd"].index(seen["file"])   # inline values win
    assert not Path(seen["file"]).exists()                   # temporary file removed


def _graph(ns):
    g = OntologyGraph()
    g.add_entity(Deployment(uid="d", name="api", namespace=ns, replicas=1, ready_replicas=1))
    return g


RENDERED = [{"kind": "Deployment", "metadata": {"name": "api"}, "spec": {"replicas": 3}}]


def test_a_render_without_namespace_is_not_matched_in_another_namespace():
    drifts = ManifestDiffer(track_orphans=False).diff(RENDERED, _graph("staging"), namespace="prod")
    assert not any(d.field_path == "spec.replicas" for d in drifts)


def test_the_release_namespace_is_matched():                               # control
    drifts = ManifestDiffer(track_orphans=False).diff(RENDERED, _graph("prod"), namespace="prod")
    assert any(d.field_path == "spec.replicas" for d in drifts)
