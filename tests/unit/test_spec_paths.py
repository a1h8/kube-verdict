"""Every path of the declared ontology exists in its kind's definition (ontology/spec_paths.py)."""
import pytest

from ontology import spec_paths
from ontology.schema_registry import SchemaRegistry
from ontology.spec_loader import AnchorSpec, RelationSpec, load

# Known, dated 2026-11-09: the product names MutatingWebhookConfiguration objects "MutatingWebhook"
# (ontology/entities.py), a name no definition has. To fix by naming the entity as the API does.
KNOWN = ["relations[8] ADMITS: kind 'MutatingWebhook' is in no definition"]


@pytest.fixture(scope="module")
def reg():
    return SchemaRegistry.from_bundle()


def test_declared_files_against_the_bundle(reg):
    spec_paths.CHECKED.clear()
    assert spec_paths.check_declared(reg) == KNOWN
    assert len(spec_paths.CHECKED) >= 20               # the check is not vacuous


def rel(**kw):
    base = dict(name="MANAGES", source_kind="Kustomization", target_kind="*", matcher="inventory",
                params={"list": "status.inventory.entries[]", "id": "flux-id"})
    base.update(kw)
    return RelationSpec(**base)


def test_wrong_inventory_path_is_reported(reg):
    assert spec_paths.check_relations(reg, [rel()]) == []
    bad = rel(params={"list": "status.inventory.items[]", "id": "flux-id"})
    assert any("is not in its definition" in w for w in spec_paths.check_relations(reg, [bad]))


def test_inventory_fields_are_checked(reg):
    # Argo CD's entries have group/kind/namespace/name; Flux's have id, not those
    bad = rel(params={"list": "status.inventory.entries[]", "id": "fields"})
    assert any("entries[].kind" in w for w in spec_paths.check_relations(reg, [bad]))


def test_helmrelease_has_no_last_applied_revision(reg):
    a = AnchorSpec("x", "HelmRelease.helm.toolkit.fluxcd.io", "bound",
                   {"left": "status.lastAppliedRevision", "right": "status.lastAttemptedRevision"})
    assert spec_paths.check_anchors(reg, [a], []) == [
        "anchor x: HelmRelease.helm.toolkit.fluxcd.io.status.lastAppliedRevision is not in its definition"]


def test_group_map_expanded(reg):
    relations, _ = load()
    limits = next(r for r in relations if r.name == "LIMITS")
    assert spec_paths.check_relations(reg, [limits]) == []
    params = {**limits.params, "group_map": {"bound": {"maximum": "maxx", "minimum": "min"}}}
    bad = RelationSpec(limits.name, limits.source_kind, limits.target_kind, limits.matcher, params)
    assert any(".maxx." in w for w in spec_paths.check_relations(reg, [bad]))


def test_unknown_and_ambiguous_kinds_said():
    r = SchemaRegistry()
    for g in ("a.io", "b.io"):
        r.add_crds([{"kind": "CustomResourceDefinition", "spec": {"group": g, "names": {"kind": "Thing"}, "versions": [
            {"name": "v1", "schema": {"openAPIV3Schema": {"type": "object", "properties": {
                "spec": {"type": "object", "properties": {"x": {"type": "string"}}}}}}}]}}])
    why: list[str] = []
    spec_paths._check(r, "t", "Thing", "spec.x", why)
    spec_paths._check(r, "t", "Thing.a.io", "spec.x", why)
    spec_paths._check(r, "t", "Nope", "spec.x", why)
    assert why == ["t: kind 'Thing' is served by several groups ['a.io', 'b.io']; qualify it as Kind.group",
                   "t: kind 'Nope' is in no definition"]


def test_fresh_consumer_read_on_the_hop_target(reg):
    relations, anchors = load()
    fresh = next(a for a in anchors if a.type == "fresh")
    spec_paths.CHECKED.clear()
    assert spec_paths.check_anchors(reg, [fresh], relations) == []
    assert ("Pod", "status.startTime") in spec_paths.CHECKED
