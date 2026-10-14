"""ingestion.readiness — docs/test-cases.md § Live parity (phase 2)."""
from __future__ import annotations

from ingestion.readiness import Present, detect_missing


def _pod(spec: dict, status: dict | None = None, labels: dict | None = None) -> dict:
    return {"metadata": {"name": "api-0", "namespace": "prod", "labels": labels or {}},
            "spec": spec, "status": status or {}}


ENV_SECRET = {"containers": [{"name": "api", "env": [
    {"name": "P", "valueFrom": {"secretKeyRef": {"name": "db-creds", "key": "password"}}}]}]}


def test_secret_is_missing_only_when_kubernetes_says_so():
    assert detect_missing(_pod(ENV_SECRET), Present(messages=[])) == {}
    out = detect_missing(_pod(ENV_SECRET), Present(messages=['Error: secret "db-creds" not found']))
    assert list(out) == ["missing.secret.db-creds"]
    assert "Kubernetes reports it not found" in out["missing.secret.db-creds"]


def test_secret_reported_in_a_container_status_message():
    status = {"containerStatuses": [{"name": "api", "state": {"waiting": {
        "reason": "CreateContainerConfigError", "message": 'secret "db-creds" not found'}}}]}
    assert "missing.secret.db-creds" in detect_missing(_pod(ENV_SECRET, status), Present())


def test_another_secret_named_in_events_is_not_attributed():
    out = detect_missing(_pod(ENV_SECRET), Present(messages=['secret "other" not found']))
    assert out == {}


def test_configmap_from_envfrom_env_and_volume():
    spec = {"containers": [{"name": "api",
                            "envFrom": [{"configMapRef": {"name": "a"}}],
                            "env": [{"name": "X", "valueFrom": {"configMapKeyRef": {"name": "b", "key": "k"}}}]}],
            "volumes": [{"name": "cfg", "configMap": {"name": "c"}}]}
    out = detect_missing(_pod(spec), Present(configmaps={"b"}))
    assert set(out) == {"missing.configmap.a", "missing.configmap.c"}


def test_failed_listing_is_unknown_never_missing():
    spec = {"containers": [{"name": "api", "envFrom": [{"configMapRef": {"name": "a"}}]}],
            "volumes": [{"name": "data", "persistentVolumeClaim": {"claimName": "pvc-1"}}],
            "serviceAccountName": "api-sa"}
    assert detect_missing(_pod(spec), Present(configmaps=None, pvcs=None, serviceaccounts=None)) == {}


def test_pvc_and_serviceaccount():
    spec = {"containers": [{"name": "api"}],
            "volumes": [{"name": "data", "persistentVolumeClaim": {"claimName": "pvc-1"}}],
            "serviceAccountName": "api-sa"}
    out = detect_missing(_pod(spec), Present(pvcs=set(), serviceaccounts=set()))
    assert set(out) == {"missing.pvc.pvc-1", "missing.serviceaccount.api-sa"}


def test_default_serviceaccount_is_never_flagged():
    spec = {"containers": [{"name": "api"}], "serviceAccountName": "default"}
    assert detect_missing(_pod(spec), Present(serviceaccounts=set())) == {}


def test_rbac_binding_checked_only_when_given():
    spec = {"containers": [{"name": "api"}], "serviceAccountName": "api-sa"}
    assert detect_missing(_pod(spec), Present(serviceaccounts={"api-sa"})) == {}
    out = detect_missing(_pod(spec), Present(serviceaccounts={"api-sa"}, bound_serviceaccounts=set()))
    assert list(out) == ["missing.rbac.api-sa"]


def test_image_pull_secret_from_kubelet_message():
    spec = {"containers": [{"name": "api"}], "imagePullSecrets": [{"name": "regcred"}]}
    assert detect_missing(_pod(spec), Present()) == {}
    msg = "Unable to retrieve some image pull secrets (regcred); attempting to pull the image"
    assert list(detect_missing(_pod(spec), Present(messages=[msg]))) == ["missing.imagepullsecret.regcred"]


def test_networkpolicy_selection_and_egress():
    blocked = {"metadata": {"name": "deny", "namespace": "prod"},
               "spec": {"podSelector": {"matchLabels": {"app": "api"}}, "egress": []}}
    other = {"metadata": {"name": "web-only", "namespace": "prod"},
             "spec": {"podSelector": {"matchLabels": {"app": "web"}}, "egress": []}}
    ingress_only = {"metadata": {"name": "in", "namespace": "prod"},
                    "spec": {"podSelector": {}, "ingress": [{}]}}
    pod = _pod({"containers": [{"name": "api"}]}, labels={"app": "api"})
    out = detect_missing(pod, Present(networkpolicies=[blocked, other, ingress_only]))
    assert list(out) == ["netpol.deny.egress_blocked"]
    assert detect_missing(pod, Present(networkpolicies=None)) == {}
