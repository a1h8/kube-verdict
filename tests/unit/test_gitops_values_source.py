"""What the Git chart is rendered with — docs/fix-plan-001.md, H48 (only the deployed values
were used, so a values change against Git was never a drift)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ingestion.gitops_collector import GitopsCollector
from ontology.entities import HelmRelease
from ontology.graph import OntologyGraph


def _render_values(values_source, source="helm"):
    renderer = MagicMock(); renderer.render.return_value = []
    c = GitopsCollector(MagicMock(), renderer=renderer, values_source=values_source)
    c._resolve_chart = lambda release: ("/repo/charts/api", None)
    g = OntologyGraph()
    g.add_entity(HelmRelease(uid="r", name="api", namespace="prod", source=source,
                             values={"resources": {"limits": {"memory": "128Mi"}}}))
    c.collect(g)
    return renderer.render.call_args.kwargs["values"]


def test_git_mode_renders_with_git_values_only():
    assert _render_values("git") is None


def test_deployed_mode_keeps_the_deployed_values():                       # control
    assert _render_values("deployed") == {"resources": {"limits": {"memory": "128Mi"}}}


def test_a_helmfile_release_keeps_its_declared_values_in_git_mode():
    # Helmfile values come from Git already
    assert _render_values("git", source="helmfile") is not None


def test_an_unknown_mode_is_refused():
    with pytest.raises(ValueError):
        GitopsCollector(MagicMock(), values_source="live")
