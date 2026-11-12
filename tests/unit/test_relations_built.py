"""Every relation the ontology declares is built by production code, or listed here as not built yet (2026-11-09).

Found by the diagnostics of 2026-10-17 (D1.2) and left open for three weeks: QUOTA_BLOCKS, LIMITS and SCALES were read
by the context walk but built nowhere, and nothing failed. This test fails on a declared relation that is neither built
nor listed, and on a listed one that is now built, so the list stays the truth.
"""
from __future__ import annotations

import ast
from pathlib import Path

from ontology.relationships import RelationshipType

ROOT = Path(__file__).resolve().parents[2]
PRODUCTION = ("ingestion", "ontology", "rca", "workflow", "signals", "decision", "dedup", "services", "remediation",
              "reasoning", "knowledge", "vectorstore", "api")
BUILDERS = {"Edge", "_edge", "_lazy_edge", "add_edge"}

# declared, not built yet: each one is a known gap (docs/correlation-engine.md § 1), removed from here when built
NOT_BUILT_YET = {
    "SPAWNED_BY": "Job → CronJob: built as OWNS from ownerReferences instead",
    "PROVISIONED_BY": "PVC → StorageClass",
    "OVERRIDES_VALUES": "helmfile environment values → release",
    "MOUNTS_SECRET": "Secrets are never collected",
}


def _built() -> set[str]:
    """Built in code (an Edge call naming the relation), or declared in ontology/spec/relations.yaml with a matcher
    ontology/declared_relations.py implements (M2 step 2)."""
    from ontology.declared_relations import MATCHERS
    from ontology.spec_loader import load
    names: set[str] = {r.name for r in load()[0] if r.matcher in MATCHERS}
    for top in PRODUCTION:
        for path in (ROOT / top).rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
                if fn not in BUILDERS:
                    continue
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name) and sub.value.id in (
                            "RelationshipType", "R"):
                        names.add(sub.attr)
    return names


def test_every_declared_relation_is_built_or_listed():
    declared = {r.name for r in RelationshipType}
    built = _built()
    missing = declared - built - set(NOT_BUILT_YET)
    assert not missing, f"declared but built nowhere and not listed in NOT_BUILT_YET: {sorted(missing)}"


def test_a_listed_relation_that_is_now_built_leaves_the_list():
    stale = set(NOT_BUILT_YET) & _built()
    assert not stale, f"built now, remove from NOT_BUILT_YET: {sorted(stale)}"
