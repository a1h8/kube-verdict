"""Rule checks contract — docs/diagnostics.md § Step 1, Safety nets.

Every RemediationEngine rule declares read-only checks (``checks()``) apart from
its actions (``commands()``): a check is never an action, an action is never a
check, and every check says what it verifies and what confirms the hypothesis.
"""
from __future__ import annotations

import pytest

from ontology.entities import Deployment
from rca.diagnostics import is_read_only
from rca.remediation_engine import _ALL_RULES, RemediationEngine
from tests.unit.test_analyzer_fallback import _demo_graph


def _graph():
    g = _demo_graph()
    g.add_entity(Deployment(uid="d1", name="checkout", namespace="demo",
                            replicas=3, ready_replicas=0))
    return g


@pytest.fixture(scope="module")
def hypotheses():
    return RemediationEngine().score(_graph())


def test_every_rule_fires_on_the_fixture_graph(hypotheses):
    fired = {h.rule_id for h in hypotheses}
    assert fired == {r.id for r in _ALL_RULES}


def test_every_rule_declares_checks(hypotheses):
    for h in hypotheses:
        assert h.checks, h.rule_id


def test_checks_are_read_only_and_explained(hypotheses):
    for h in hypotheses:
        for check in h.checks:
            assert is_read_only(check.command), (h.rule_id, check.command)
            assert check.purpose.strip(), (h.rule_id, check.command)
            assert check.confirms_if.strip(), (h.rule_id, check.command)


def test_commands_are_actions_only(hypotheses):
    for h in hypotheses:
        for cmd in h.commands:
            if cmd.strip().startswith("#"):
                continue
            assert not is_read_only(cmd), (h.rule_id, cmd)
