"""PR/MR-first phase 1 — verdict → declared values.yaml patch (offline)."""
from __future__ import annotations

from pathlib import Path

import yaml

from remediation.patch_builder import (
    PatchBuilder,
    PatchProposal,
    parse_set_overrides,
)

H012_VALUES = (
    Path(__file__).parents[1]
    / "integration/cases/h012_gitops_render_vs_live/chart/values.yaml"
)


# ── parse_set_overrides ──────────────────────────────────────────────────────

def test_parse_extracts_release_namespace_and_sets():
    changes, release, ns = parse_set_overrides([
        "helm upgrade api ./chart -n production "
        "--set replicaCount=3 --set resources.limits.memory=512Mi",
    ])
    assert release == "api"
    assert ns == "production"
    assert changes == {"replicaCount": 3, "resources.limits.memory": "512Mi"}


def test_parse_ignores_kubectl_commands():
    changes, release, ns = parse_set_overrides([
        "kubectl rollout restart deployment/api -n prod",
        "kubectl scale deployment/api --replicas=3 -n prod",
    ])
    assert changes == {}
    assert release == ""


def test_parse_last_set_wins_and_coerces_types():
    changes, _, _ = parse_set_overrides([
        "helm upgrade api ./c --set enabled=false --set enabled=true --set count=2",
    ])
    assert changes == {"enabled": True, "count": 2}


# ── PatchBuilder.build ───────────────────────────────────────────────────────

def test_build_produces_declared_change_and_diff():
    values = "replicaCount: 1\nresources:\n  limits:\n    memory: 128Mi\n"
    proposal = PatchBuilder().build(
        ["helm upgrade api ./chart -n production "
         "--set replicaCount=3 --set resources.limits.memory=512Mi"],
        values,
        file_path="chart/values.yaml",
    )
    assert isinstance(proposal, PatchProposal)
    assert not proposal.is_empty
    assert proposal.release == "api"
    assert proposal.namespace == "production"
    # New content reflects the declared fix
    tree = yaml.safe_load(proposal.new_content)
    assert tree["replicaCount"] == 3
    assert tree["resources"]["limits"]["memory"] == "512Mi"
    # Unified diff shows the change
    assert proposal.diff.startswith("--- a/chart/values.yaml")
    assert "+replicaCount: 3" in proposal.diff
    assert "-replicaCount: 1" in proposal.diff


def test_build_is_empty_without_helm_upgrade():
    proposal = PatchBuilder().build(
        ["kubectl rollout restart deployment/api -n prod"],
        "replicaCount: 1\n",
        file_path="chart/values.yaml",
    )
    assert proposal.is_empty
    assert proposal.changes == {}
    assert proposal.diff == ""


def test_build_noop_when_change_already_declared():
    """h012: the drift is live-only; values.yaml already declares the fix, so a
    values patch is a no-op (there is nothing to open a PR for on git side)."""
    values = H012_VALUES.read_text()
    proposal = PatchBuilder().build(
        ["helm upgrade api ./chart -n production "
         "--set replicaCount=3 --set resources.limits.memory=512Mi"],
        values,
        file_path="chart/values.yaml",
    )
    # Change was parsed, but it matches the declared state → no diff.
    assert proposal.changes  # overrides were parsed
    assert proposal.is_empty  # ...but produce no change to values.yaml
    assert proposal.diff.strip() == ""


def test_build_patches_real_h012_chart_when_values_drifted():
    """Start from a values.yaml that drifted below intent; the verdict's --set
    brings it back and yields a concrete diff against the real chart shape."""
    drifted = yaml.safe_dump(
        {**yaml.safe_load(H012_VALUES.read_text()), "replicaCount": 1},
        sort_keys=False,
    )
    proposal = PatchBuilder().build(
        ["helm upgrade api ./chart -n production --set replicaCount=3"],
        drifted,
        file_path="chart/values.yaml",
    )
    assert not proposal.is_empty
    assert yaml.safe_load(proposal.new_content)["replicaCount"] == 3
    assert "+replicaCount: 3" in proposal.diff


def test_to_dict_roundtrip_shape():
    proposal = PatchBuilder().build(
        ["helm upgrade api ./c -n prod --set replicaCount=2"],
        "replicaCount: 1\n",
        file_path="values.yaml",
    )
    d = proposal.to_dict()
    assert set(d) == {
        "release", "namespace", "file_path", "changes",
        "diff", "new_content", "source_remediation",
    }
    assert d["changes"] == {"replicaCount": 2}
