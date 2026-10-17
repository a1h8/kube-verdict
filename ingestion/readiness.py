"""
Readiness — dependencies a pod references but that are not there.

Spec: docs/test-cases.md → *Live parity*. One function for production and for the
offline fixtures, so the tests prove what runs live. It reads pod specs in the
Kubernetes API shape (``envFrom``, ``valueFrom.secretKeyRef``, ``volumes``…),
the names of what was listed, and what Kubernetes itself reported (event and
container messages).

Rules:
- A kind the account can list (ConfigMap, PVC, ServiceAccount) is *missing* when
  its name is absent from the listing.
- A kind whose listing is ``None`` (failed: 403, timeout, or not collected) is
  *unknown*, never missing: an RBAC gap must not invent missing dependencies.
- A Secret is never listed (docs/diagnostics.md § Step 4). It is missing only when
  Kubernetes says so: ``secret "<name>" not found``, or an image pull secret it
  could not retrieve.
- RBAC bindings and NetworkPolicies are checked only when their listings are given.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_SECRET_NOT_FOUND = re.compile(r'secrets? "([^"]+)" not found', re.IGNORECASE)
_PULL_SECRET_MISSING = re.compile(
    r"unable to retrieve some image pull secrets \(([^)]*)\)", re.IGNORECASE)


@dataclass
class Present:
    """Names that exist, per kind. ``None`` = unknown (listing failed or not collected)."""
    configmaps: set[str] | None = None
    pvcs: set[str] | None = None
    serviceaccounts: set[str] | None = None
    bound_serviceaccounts: set[str] | None = None   # subjects of a (Cluster)RoleBinding
    networkpolicies: list[dict] | None = None        # API-shaped NetworkPolicy dicts
    messages: list[str] = field(default_factory=list)  # event + container messages


def reported_missing_secrets(messages: list[str]) -> set[str]:
    out: set[str] = set()
    for msg in messages:
        out.update(_SECRET_NOT_FOUND.findall(msg or ""))
    return out


def reported_missing_pull_secrets(messages: list[str]) -> set[str]:
    out: set[str] = set()
    for msg in messages:
        for group in _PULL_SECRET_MISSING.findall(msg or ""):
            out.update(name.strip() for name in group.split(",") if name.strip())
    return out


def container_messages(pod: dict) -> list[str]:
    """Waiting / terminated messages Kubernetes put on the pod's containers."""
    out = []
    status = pod.get("status") or {}
    for cs in (status.get("containerStatuses") or []) + (status.get("initContainerStatuses") or []):
        for key in ("state", "lastState"):
            for detail in ((cs.get(key) or {}).values()):
                if isinstance(detail, dict) and detail.get("message"):
                    out.append(detail["message"])
    return out


def detect_missing(pod: dict, present: Present) -> dict[str, str]:
    """``missing.<kind>.<name>`` / ``netpol.<name>.*`` annotations for one API-shaped pod."""
    meta = pod.get("metadata") or {}
    spec = pod.get("spec") or {}
    ns = meta.get("namespace", "")
    messages = list(present.messages) + container_messages(pod)
    missing_secrets = reported_missing_secrets(messages)
    out: dict[str, str] = {}

    def secret(name: str, where: str) -> None:
        if name and name in missing_secrets:
            out[f"missing.secret.{name}"] = (
                f"Secret '{name}' referenced in {where} — Kubernetes reports it not found")

    def configmap(name: str, where: str) -> None:
        if name and present.configmaps is not None and name not in present.configmaps:
            out[f"missing.configmap.{name}"] = (
                f"ConfigMap '{name}' referenced in {where} — not found in cluster")

    containers = (spec.get("containers") or []) + (spec.get("initContainers") or []) \
        + (spec.get("ephemeralContainers") or [])
    for c in containers:
        cname = c.get("name", "?")
        for ef in c.get("envFrom") or []:
            secret((ef.get("secretRef") or {}).get("name", ""), f"envFrom (container {cname})")
            configmap((ef.get("configMapRef") or {}).get("name", ""), f"envFrom (container {cname})")
        for env in c.get("env") or []:
            vf = env.get("valueFrom") or {}
            skr, ckr = vf.get("secretKeyRef") or {}, vf.get("configMapKeyRef") or {}
            secret(skr.get("name", ""),
                   f"env.valueFrom.secretKeyRef (key {skr.get('key', '?')}, container {cname})")
            configmap(ckr.get("name", ""),
                      f"env.valueFrom.configMapKeyRef (key {ckr.get('key', '?')}, container {cname})")

    for vol in spec.get("volumes") or []:
        vname = vol.get("name", "")
        secret((vol.get("secret") or {}).get("secretName", ""), f"volume '{vname}'")
        configmap((vol.get("configMap") or {}).get("name", ""), f"volume '{vname}'")
        claim = (vol.get("persistentVolumeClaim") or {}).get("claimName", "")
        if claim and present.pvcs is not None and claim not in present.pvcs:
            out[f"missing.pvc.{claim}"] = (
                f"PVC '{claim}' referenced as volume '{vname}' — not found or not bound")

    missing_pull = reported_missing_pull_secrets(messages) | missing_secrets
    for ips in spec.get("imagePullSecrets") or []:
        name = ips.get("name", "")
        if name and name in missing_pull:
            out[f"missing.imagepullsecret.{name}"] = (
                f"imagePullSecret '{name}' — Kubernetes reports it missing; image pull will fail")

    # A pod that exists proves its ServiceAccount existed: the ServiceAccount admission
    # plugin rejects a pod whose ServiceAccount is missing (the ReplicaSet then reports
    # FailedCreate and no pod exists). So an absent ServiceAccount is never inferred from
    # a pod — it was flagged on every fixture that does not ship the object
    # (docs/structured-diagnosis.md, finding S1).
    sa = spec.get("serviceAccountName", "")
    if sa and sa != "default" and present.serviceaccounts is not None:
        if sa in present.serviceaccounts and present.bound_serviceaccounts is not None \
                and sa not in present.bound_serviceaccounts:
            out[f"missing.rbac.{sa}"] = (
                f"ServiceAccount '{sa}' exists but has no (Cluster)RoleBinding — "
                f"API calls will be Forbidden")

    for np in present.networkpolicies or []:
        np_meta = np.get("metadata") or {}
        if np_meta.get("namespace") and np_meta["namespace"] != ns:
            continue
        np_name = np_meta.get("name", "np-unknown")
        np_spec = np.get("spec") or {}
        match = (np_spec.get("podSelector") or {}).get("matchLabels") or {}
        labels = meta.get("labels") or {}
        if match and any(labels.get(k) != v for k, v in match.items()):
            continue
        if "egress" in np_spec and not np_spec.get("egress"):
            out[f"netpol.{np_name}.egress_blocked"] = (
                f"NetworkPolicy '{np_name}' selects this pod with empty egress rules "
                f"— ALL outbound traffic is blocked")
        elif "egress" not in np_spec and "ingress" in np_spec:
            continue   # ingress-only policy, egress unaffected
        else:
            out[f"netpol.{np_name}.applied"] = (
                f"NetworkPolicy '{np_name}' applies to this pod — "
                f"verify egress/ingress rules allow required traffic")
    return out
