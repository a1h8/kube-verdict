"""What the project's chart grants — docs/fix-plan-001.md, H45 (option A). Rendered by
`helm template`; CI installs Helm, so these tests never skip there."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART = Path(__file__).resolve().parents[2] / "helm/kube-verdict"

if not shutil.which("helm"):
    if os.environ.get("CI"):
        raise RuntimeError("helm is not installed: the chart's RBAC tests would not run")
    pytest.skip("helm not installed", allow_module_level=True)


def _render(*sets: str) -> list[dict]:
    args = [a for s in sets for a in ("--set", s)]
    out = subprocess.run(["helm", "template", "t", str(CHART), *args],
                         check=True, capture_output=True, text=True).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def _kind(docs, kind):
    return [d for d in docs if d["kind"] == kind]


def _rules(docs):
    return [r for d in _kind(docs, "ClusterRole") + _kind(docs, "Role") for r in d["rules"]]


def test_by_default_no_secret_is_readable():
    docs = _render()
    assert not any("secrets" in r["resources"] for r in _rules(docs))
    assert _kind(docs, "Role") == [] and _kind(docs, "RoleBinding") == []
    assert "HELM_RELEASE_NAMESPACES" not in _kind(docs, "ConfigMap")[0]["data"]


def test_each_listed_namespace_gets_its_own_role_on_secrets():
    docs = _render("rbac.helmReleaseNamespaces={apps,payments}")
    roles = {d["metadata"]["namespace"]: d for d in _kind(docs, "Role")}
    assert set(roles) == {"apps", "payments"}
    assert all(d["rules"] == [{"apiGroups": [""], "resources": ["secrets"], "verbs": ["get", "list"]}]
               for d in roles.values())
    bindings = {d["metadata"]["namespace"]: d for d in _kind(docs, "RoleBinding")}
    assert set(bindings) == {"apps", "payments"}
    for b in bindings.values():
        assert b["roleRef"]["name"] == roles["apps"]["metadata"]["name"]
        assert b["subjects"] == [{"kind": "ServiceAccount", "name": "t-kubeverdict", "namespace": "default"}]
    assert _kind(docs, "ConfigMap")[0]["data"]["HELM_RELEASE_NAMESPACES"] == "apps,payments"


def test_secrets_are_never_granted_cluster_wide():
    docs = _render("rbac.helmReleaseNamespaces={apps}")
    assert not any("secrets" in r["resources"] for d in _kind(docs, "ClusterRole") for r in d["rules"])


def test_the_cluster_role_reads_what_the_collectors_list():
    granted = {(g, res) for r in _rules(_render()) for g in r["apiGroups"] for res in r["resources"]
               if "list" in r["verbs"]}
    assert {("", "resourcequotas"), ("", "limitranges"),
            ("admissionregistration.k8s.io", "mutatingwebhookconfigurations")} <= granted


def test_no_impersonation_unless_configured():
    assert not any("impersonate" in r["verbs"] for r in _rules(_render()))


def test_impersonation_is_granted_by_name_only():
    rules = [r for r in _rules(_render("rbac.impersonate.user=alice", "rbac.impersonate.groups={team-a}"))
             if "impersonate" in r["verbs"]]
    assert {(r["resources"][0], tuple(r["resourceNames"])) for r in rules} == \
        {("users", ("alice",)), ("groups", ("team-a",))}


def test_without_rbac_nothing_is_granted():
    docs = _render("rbac.create=false", "rbac.helmReleaseNamespaces={apps}")
    assert _rules(docs) == [] and _kind(docs, "RoleBinding") == []
