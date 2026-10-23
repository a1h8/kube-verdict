"""PatchBuilder reads --set the way Helm does — docs/fix-plan-001.md, H69.

The MCP tool ``propose_patch`` builds its values.yaml patch (and, with ``open_pr``, a draft PR)
from these overrides, so a misread ``--set`` would propose a wrong declared change.
"""
from __future__ import annotations

import shutil
import subprocess

import pytest
import yaml

from remediation.patch_builder import PatchBuilder, parse_set_overrides


def _tree(remediation, values="{}\n"):
    proposal = PatchBuilder().build(remediation, values, file_path="values.yaml")
    return yaml.safe_load(proposal.new_content), proposal


def test_one_patch_covers_one_release():
    changes, release, _ = parse_set_overrides([
        "helm upgrade api ./c --set replicas=3",
        "helm upgrade db ./c --set replicas=1",
    ])
    assert release == "api" and changes == {"replicas": 3}
    changes, release, _ = parse_set_overrides(
        ["helm upgrade api ./c --set replicas=3", "helm upgrade db ./c --set replicas=1"],
        release="db")
    assert changes == {"replicas": 1}


def test_a_decimal_stays_a_string():
    tree, _ = _tree(["helm upgrade api ./c --set image.tag=1.10"])
    assert tree == {"image": {"tag": "1.10"}}


def test_set_string_converts_nothing():
    tree, _ = _tree(["helm upgrade api ./c --set-string image.tag=123 --set replicas=3"])
    assert tree == {"image": {"tag": "123"}, "replicas": 3}                       # control


def test_commas_separate_pairs_unless_escaped():
    tree, _ = _tree([r"helm upgrade api ./c --set a=1,b=2 --set opts=-Xms1g\,-Xmx2g"])
    assert tree == {"a": 1, "b": 2, "opts": "-Xms1g,-Xmx2g"}


def test_list_indexes():
    tree, _ = _tree(["helm upgrade api ./c --set env[1].value=x"])
    assert tree == {"env": [None, {"value": "x"}]}


def test_the_equals_form():
    tree, _ = _tree(["helm upgrade api ./c --set=replicas=3"])
    assert tree == {"replicas": 3}


def test_a_scalar_replaced_by_a_mapping_is_reported():
    tree, proposal = _tree(["helm upgrade api ./c --set image.tag=1.2.0"], "image: nginx:1.0\n")
    assert tree == {"image": {"tag": "1.2.0"}}
    assert proposal.warnings == ["image: 'nginx:1.0' replaced by a mapping"]


CASES = [
    "--set image.tag=1.10",
    "--set-string image.tag=123",
    r"--set a=1,b=true,c=null,d=v2 --set opts=-Xms1g\,-Xmx2g",
    "--set env[1].value=x --set env[0].name=A",
    "--set=replicas=3 --set enabled=false",
]


@pytest.mark.skipif(shutil.which("helm") is None, reason="needs the helm binary")
@pytest.mark.parametrize("flags", CASES)
def test_the_tree_is_the_one_helm_builds(tmp_path, flags):
    chart = tmp_path / "c"
    (chart / "templates").mkdir(parents=True)
    (chart / "Chart.yaml").write_text("apiVersion: v2\nname: c\nversion: 0.1.0\n")
    (chart / "values.yaml").write_text("{}\n")
    (chart / "templates" / "values.yaml").write_text(
        "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: v\ndata:\n"
        "  values: {{ toJson .Values | quote }}\n")
    # split on spaces, as a shell would here: shlex would drop the backslash of "\,"
    out = subprocess.run(["helm", "template", "r", str(chart), *flags.split()],
                         capture_output=True, text=True, check=True).stdout
    import json
    helm_tree = json.loads(yaml.safe_load(out)["data"]["values"])
    ours, _ = _tree([f"helm upgrade r ./c {flags}"])
    assert ours == helm_tree
