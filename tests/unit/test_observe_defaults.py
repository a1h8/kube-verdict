"""tools/observe_defaults.py with kubectl faked: no cluster (docs/schema-registry.md, step 6)."""
import json

import pytest
import yaml

from ontology.schema_registry import SchemaRegistry
from tools.fetch_specs import verify
from tools.observe_defaults import added, observe


def test_only_what_the_server_added_and_not_server_set_metadata():
    sub = {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": "p"},
           "spec": {"containers": [{"name": "c", "image": "i:1"}]}}
    ans = {"apiVersion": "v1", "kind": "Pod",
           "metadata": {"name": "p", "uid": "u", "creationTimestamp": "t", "namespace": "ns",
                        "managedFields": [{"manager": "kubectl"}]},
           "spec": {"containers": [{"name": "c", "image": "i:1", "imagePullPolicy": "IfNotPresent",
                                    "terminationMessagePath": "/dev/termination-log"}],
                    "restartPolicy": "Always", "dnsPolicy": "ClusterFirst"},
           "status": {"phase": "Pending"}}
    assert added(sub, ans) == {"spec.containers[name=c].imagePullPolicy": "IfNotPresent",
                               "spec.containers[name=c].terminationMessagePath": "/dev/termination-log",
                               "spec.restartPolicy": "Always", "spec.dnsPolicy": "ClusterFirst"}


def fake_kubectl(answers):
    calls = []

    def run(cmd, stdin):
        calls.append(cmd)
        if "version" in cmd:
            return json.dumps({"serverVersion": {"gitVersion": "v1.35.5+k3s1"}}).encode()
        assert "--dry-run=server" in cmd and "create" in cmd        # never a real create
        obj = yaml.safe_load(stdin)
        return json.dumps(answers(obj)).encode()
    return run, calls


def test_observe_writes_records_and_a_lock_the_registry_reads(tmp_path):
    minimal = tmp_path / "minimal"
    minimal.mkdir()
    (minimal / "pod.yaml").write_text("apiVersion: v1\nkind: Pod\nmetadata: {name: a}\n"
                                      "spec: {containers: [{name: c, image: 'i:1'}]}\n")
    (minimal / "pod-latest.yaml").write_text("apiVersion: v1\nkind: Pod\nmetadata: {name: b}\n"
                                             "spec: {containers: [{name: c, image: 'i:latest'}]}\n")

    def answers(obj):
        c = obj["spec"]["containers"][0]
        policy = "Always" if c["image"].endswith(":latest") else "IfNotPresent"
        return {**obj, "spec": {"containers": [{**c, "imagePullPolicy": policy}], "restartPolicy": "Always"}}
    run, calls = fake_kubectl(answers)
    bundle = tmp_path / "bundle"
    entry = observe("k3d-dev", "kv-defaults", bundle, minimal, run, now="2026-11-09T00:00:00+00:00")
    assert entry["tag"] == "v1.35.5+k3s1" and sorted(entry["files"]) == ["pod-latest.json", "pod.json"]
    assert all("--context" in c and "k3d-dev" in c for c in calls)
    assert verify(bundle) == []

    reg = SchemaRegistry()
    for name in sorted(entry["files"]):
        reg.add_observed(json.loads((bundle / "observed-defaults" / name).read_text()))
    obs = {o["input"]: o["defaults"] for o in reg.observed_defaults("", "Pod")}
    assert obs["pod.yaml"]["spec.containers[name=c].imagePullPolicy"] == "IfNotPresent"
    assert obs["pod-latest.yaml"]["spec.containers[name=c].imagePullPolicy"] == "Always"   # the conditional default
    assert obs["pod.yaml"]["spec.restartPolicy"] == "Always"


def test_existing_sources_of_the_lock_are_kept(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "lock.yaml").write_text(yaml.safe_dump({"version": 1, "sources": [
        {"name": "kubernetes", "tag": "v1.35.5", "format": "openapi-v3", "files": {}}]}))
    minimal = tmp_path / "m"
    minimal.mkdir()
    (minimal / "svc.yaml").write_text("apiVersion: v1\nkind: Service\nmetadata: {name: s}\nspec: {ports: [{port: 80}]}\n")
    run, _ = fake_kubectl(lambda o: {**o, "spec": {**o["spec"], "type": "ClusterIP"}})
    observe("ctx", "ns", bundle, minimal, run)
    names = [s["name"] for s in yaml.safe_load((bundle / "lock.yaml").read_text())["sources"]]
    assert names == ["kubernetes", "observed-defaults"]


def test_a_context_is_required(tmp_path):
    with pytest.raises(ValueError, match="explicit --context"):
        observe("", "ns", tmp_path, tmp_path, lambda c, s: b"{}")


def test_minimal_inputs_are_valid_objects():
    # each input must be accepted by its definition, or the dry-run would refuse it
    from pathlib import Path

    from ontology.schema_registry import validate
    reg = SchemaRegistry.bundled()
    for p in sorted((Path(__file__).resolve().parents[2] / "ontology/spec/upstream/minimal").glob("*.yaml")):
        assert validate(reg, yaml.safe_load(p.read_text())) == [], p.name
