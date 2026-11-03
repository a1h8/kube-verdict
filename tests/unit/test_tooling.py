"""Tooling does what it says — docs/fix-plan-001.md, H76."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "init-k3s.sh"
_RENDER_TOOLS = ("bash", "awk", "sed", "grep", "mktemp", "cat", "rm", "dirname", "date")


def _render(*args):
    """Run the script's render-only mode with a PATH that holds the render tools and nothing
    else: if the script ever ignored --print-manifest again (the old one ignored unknown
    arguments and went on to `curl get.k3s.io | sh`), curl, kubectl and k3s are not found."""
    import tempfile
    bin_dir = Path(tempfile.mkdtemp(prefix="kv-render-bin-"))
    for tool in _RENDER_TOOLS:
        (bin_dir / tool).symlink_to(shutil.which(tool))
    out = subprocess.run([str(bin_dir / "bash"), str(SCRIPT), *args, "--print-manifest"],
                         capture_output=True, text=True, env={"PATH": str(bin_dir)})
    shutil.rmtree(bin_dir)
    return out.returncode, out.stdout, out.stderr


needs_bash = pytest.mark.skipif(any(shutil.which(t) is None for t in _RENDER_TOOLS),
                                reason="needs bash and the text tools")


@needs_bash
def test_the_image_option_replaces_the_manifests_image():
    rc, out, err = _render("--image", "registry.example/kv:9.9")
    assert rc == 0, err
    images = re.findall(r"image:\s*(\S*kube-verdict\S*|registry\.example\S*)", out)
    assert images == ["registry.example/kv:9.9"]


@needs_bash
def test_the_on_demand_job_template_is_not_applied():
    rc, out, _ = _render()
    kinds = [d["kind"] for d in yaml.safe_load_all(out) if d]
    assert kinds == ["ConfigMap", "PersistentVolumeClaim", "CronJob"]
    assert "kubeverdict-ondemand" not in out
    # control: the source manifest still carries the template, for `kubectl create job`
    assert "kubeverdict-ondemand" in (ROOT / "k8s" / "kubeverdict.yaml").read_text()


@needs_bash
def test_an_unknown_argument_is_refused():
    assert _render("--imgae", "x")[0] != 0


def test_make_test_runs_the_ci_selection():
    target = (ROOT / "Makefile").read_text().split("\ntest:", 1)[1].split("\n\n", 1)[0]
    assert '-m "not integration"' in target and "HF_HUB_OFFLINE=1" in target


def test_the_scaleway_cluster_version_is_pinned():
    main = (ROOT / "terraform" / "scaleway" / "main.tf").read_text()
    assert 'version = "latest"' not in main and "var.kubernetes_version" in main
