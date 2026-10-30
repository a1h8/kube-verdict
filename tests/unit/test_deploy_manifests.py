"""The shipped manifests keep the database on the volume and pin kube-verdict's image —
docs/fix-plan-001.md H64."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def _docs(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def _images(doc: dict) -> list[str]:
    spec = doc.get("spec", {})
    pod = spec.get("jobTemplate", {}).get("spec", spec).get("template", {}).get("spec", {})
    return [c["image"] for c in pod.get("containers", [])]


def test_the_raw_manifests_put_the_database_on_the_volume():
    docs = _docs(ROOT / "k8s/kubeverdict.yaml")
    config = next(d for d in docs if d["kind"] == "ConfigMap")
    assert config["data"]["KUBEWHISPERER_DB"].startswith("/data/")
    for d in docs:                                              # control: /data is the mounted volume
        if d["kind"] in ("CronJob", "Job"):
            spec = d["spec"].get("jobTemplate", {}).get("spec", d["spec"])["template"]["spec"]
            assert any(m["mountPath"] == "/data" for m in spec["containers"][0]["volumeMounts"])


def test_the_raw_manifests_pin_kube_verdict():
    images = [im for d in _docs(ROOT / "k8s/kubeverdict.yaml") for im in _images(d)]
    assert images and all(":" in im and not im.endswith(":latest") for im in images)


@pytest.mark.skipif(shutil.which("helm") is None, reason="needs the helm binary")
def test_the_chart_pins_its_image_and_puts_the_database_on_the_volume():
    out = subprocess.run(["helm", "template", "kv", str(ROOT / "helm/kube-verdict")],
                         capture_output=True, text=True, check=True, timeout=60).stdout
    docs = [d for d in yaml.safe_load_all(out) if d]
    config = next(d for d in docs if d["kind"] == "ConfigMap" and "KUBEWHISPERER_DB" in d.get("data", {}))
    assert config["data"]["KUBEWHISPERER_DB"].startswith("/data/")
    chart = yaml.safe_load((ROOT / "helm/kube-verdict/Chart.yaml").read_text())
    dep = next(d for d in docs if d["kind"] == "Deployment")
    assert _images(dep)[0].endswith(f":v{chart['appVersion']}")
