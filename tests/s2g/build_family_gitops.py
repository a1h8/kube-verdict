"""Builds S2-GitOps (docs/s2g/gitops-cases.md): g1-g5 and their negative g0.

Written 2026-11-10 from the family spec, as amended after the definitions were read, before any arm ran on these
cases. Every object is checked against the bundle's definitions with no omission allowed
(tests/unit/test_s2g_cases.py). A value neither in a definition nor in a documentation page is not written.

    python tests/s2g/build_family_gitops.py

Nothing in a fixture says which object is the cause or a decoy: ``expect.json`` and ``tests/s2g/evidence.yaml`` do.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "cases"
T0 = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)
REPO = "https://git.example.com/acme/deploy.git"
GIT_OLD, GIT_NEW = "4f1c2a9e7b3d5f60a8c1e2d3b4a5968778695a4b", "b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5"
NODES = ["worker-node-01", "worker-node-02", "worker-node-03"]


def ts(minutes: float) -> str:
    return (T0 + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def ns_epoch(minutes: float) -> str:
    return str(int((T0 + timedelta(minutes=minutes)).timestamp() * 1e9))


def h(*parts: str, n: int = 64) -> str:
    return hashlib.sha256("/".join(parts).encode()).hexdigest()[:n]


def uid(kind: str, ns: str, name: str) -> str:
    d = h(kind, ns, name, n=32)
    return f"{d[:8]}-{d[8:12]}-{d[12:16]}-{d[16:20]}-{d[20:32]}"


# ---------------------------------------------------------------------------------------------------- workloads

class App:
    def __init__(self, name, ns, tag, mem="512Mi", cpu="250m", replicas=2, port=8080, env=None, labels=None):
        self.name, self.ns, self.tag, self.mem, self.cpu, self.replicas, self.port = name, ns, tag, mem, cpu, replicas, port
        self.env = env or []
        self.labels = labels or {}
        self.rs_hash = h(name, ns, tag, mem, n=10)

    def container(self, mem=None, tag=None):
        c = {"name": self.name, "image": f"ghcr.io/acme/{self.name}:{tag or self.tag}",
             "ports": [{"containerPort": self.port, "name": "http", "protocol": "TCP"}],
             "resources": {"requests": {"cpu": self.cpu, "memory": mem or self.mem},
                           "limits": {"memory": mem or self.mem}},
             "readinessProbe": {"httpGet": {"path": "/ready", "port": self.port}, "periodSeconds": 10}}
        if self.env:
            c["env"] = copy.deepcopy(self.env)
        return c

    def sel(self):
        return {"app.kubernetes.io/name": self.name}

    def meta_labels(self):
        return {**self.sel(), "app.kubernetes.io/part-of": "acme", **self.labels}


def deployment(a: App, ready=None, conditions=None, managed=None, annotations=None, mem=None, tag=None,
               revision="1", generation=1):
    ready = a.replicas if ready is None else ready
    d = {"apiVersion": "apps/v1", "kind": "Deployment",
         "metadata": {"name": a.name, "namespace": a.ns, "uid": uid("Deployment", a.ns, a.name),
                      "generation": generation, "creationTimestamp": ts(-60 * 24 * 30), "labels": a.meta_labels(),
                      "annotations": {"deployment.kubernetes.io/revision": revision, **(annotations or {})}},
         "spec": {"replicas": a.replicas, "selector": {"matchLabels": a.sel()},
                  "template": {"metadata": {"labels": a.meta_labels()},
                               "spec": {"containers": [a.container(mem, tag)]}}},
         "status": {"observedGeneration": generation, "replicas": a.replicas, "readyReplicas": ready,
                    "availableReplicas": ready, "updatedReplicas": a.replicas,
                    "conditions": conditions or [
                        {"type": "Available", "status": "True" if ready else "False",
                         "reason": "MinimumReplicasAvailable" if ready else "MinimumReplicasUnavailable",
                         "message": "Deployment has minimum availability." if ready else
                         "Deployment does not have minimum availability.",
                         "lastTransitionTime": ts(-60 * 24), "lastUpdateTime": ts(-60 * 24)},
                        {"type": "Progressing", "status": "True", "reason": "NewReplicaSetAvailable",
                         "message": f'ReplicaSet "{a.name}-{a.rs_hash}" has successfully progressed.',
                         "lastTransitionTime": ts(-60 * 24 * 30), "lastUpdateTime": ts(-60 * 24)}]}}
    if not ready:
        d["status"].pop("readyReplicas")
        d["status"].pop("availableReplicas")
        d["status"]["unavailableReplicas"] = a.replicas
    if managed:
        d["metadata"]["managedFields"] = managed
    return d


def replicaset(a: App, replicas=None, rs_hash=None, mem=None, tag=None, revision="1", created=-60 * 24 * 30):
    rs_hash = rs_hash or a.rs_hash
    replicas = a.replicas if replicas is None else replicas
    name = f"{a.name}-{rs_hash}"
    labels = {**a.meta_labels(), "pod-template-hash": rs_hash}
    return {"apiVersion": "apps/v1", "kind": "ReplicaSet",
            "metadata": {"name": name, "namespace": a.ns, "uid": uid("ReplicaSet", a.ns, name),
                         "creationTimestamp": ts(created), "labels": labels,
                         "annotations": {"deployment.kubernetes.io/revision": revision},
                         "ownerReferences": [{"apiVersion": "apps/v1", "kind": "Deployment", "name": a.name,
                                              "uid": uid("Deployment", a.ns, a.name), "controller": True,
                                              "blockOwnerDeletion": True}]},
            "spec": {"replicas": replicas, "selector": {"matchLabels": {**a.sel(), "pod-template-hash": rs_hash}},
                     "template": {"metadata": {"labels": labels}, "spec": {"containers": [a.container(mem, tag)]}}},
            "status": {"replicas": replicas, "readyReplicas": replicas, "availableReplicas": replicas,
                       "observedGeneration": 1}}


def pod(a: App, i: int, node: str, rs_hash=None, ready=True, restarts=0, state=None, last=None, mem=None, tag=None,
        started=-60 * 24):
    rs_hash = rs_hash or a.rs_hash
    name = f"{a.name}-{rs_hash}-{h(a.name, str(i), rs_hash, n=5)}"
    tag = tag or a.tag
    cs = {"name": a.name, "image": f"ghcr.io/acme/{a.name}:{tag}",
          "imageID": f"ghcr.io/acme/{a.name}@sha256:{h(a.name, tag)}", "ready": ready, "started": ready,
          "restartCount": restarts,
          "state": state or {"running": {"startedAt": ts(started)}}}
    if last:
        cs["lastState"] = last
    return {"apiVersion": "v1", "kind": "Pod",
            "metadata": {"name": name, "namespace": a.ns, "uid": uid("Pod", a.ns, name),
                         "creationTimestamp": ts(started), "labels": {**a.meta_labels(), "pod-template-hash": rs_hash},
                         "ownerReferences": [{"apiVersion": "apps/v1", "kind": "ReplicaSet",
                                              "name": f"{a.name}-{rs_hash}",
                                              "uid": uid("ReplicaSet", a.ns, f"{a.name}-{rs_hash}"),
                                              "controller": True, "blockOwnerDeletion": True}]},
            "spec": {"nodeName": node, "containers": [a.container(mem, tag)]},
            "status": {"phase": "Running", "startTime": ts(started), "podIP": f"10.42.{i % 3}.{10 + i}",
                       "conditions": [{"type": "Ready", "status": "True" if ready else "False",
                                       "lastTransitionTime": ts(started)}],
                       "containerStatuses": [cs]}}


def service(a: App):
    return {"apiVersion": "v1", "kind": "Service",
            "metadata": {"name": a.name, "namespace": a.ns, "uid": uid("Service", a.ns, a.name),
                         "labels": a.meta_labels()},
            "spec": {"selector": a.sel(), "type": "ClusterIP",
                     "ports": [{"name": "http", "port": 80, "targetPort": a.port, "protocol": "TCP"}]}}


def event(kind, ns, name, reason, message, first, last=None, count=1, etype="Normal", component="kubelet", api="v1"):
    ename = f"{name}.{h(reason, message, str(first), n=12)}"
    return {"apiVersion": "v1", "kind": "Event",
            "metadata": {"name": ename, "namespace": ns, "uid": uid("Event", ns, ename),
                         "creationTimestamp": ts(first)},
            "type": etype, "reason": reason, "message": message,
            "involvedObject": {"apiVersion": api, "kind": kind, "name": name, "namespace": ns,
                               "uid": uid(kind, ns, name)},
            "count": count, "firstTimestamp": ts(first), "lastTimestamp": ts(last if last is not None else first),
            "source": {"component": component}, "reportingComponent": component}


def namespace(name):
    return {"apiVersion": "v1", "kind": "Namespace",
            "metadata": {"name": name, "uid": uid("Namespace", "", name), "labels": {"kubernetes.io/metadata.name": name}},
            "status": {"phase": "Active"}}


def node(name, i):
    return {"apiVersion": "v1", "kind": "Node",
            "metadata": {"name": name, "uid": uid("Node", "", name), "labels": {"kubernetes.io/hostname": name}},
            "status": {"conditions": [
                {"type": "Ready", "status": "True", "reason": "KubeletReady", "message": "kubelet is posting ready status",
                 "lastHeartbeatTime": ts(0), "lastTransitionTime": ts(-60 * 24 * 60)},
                {"type": "MemoryPressure", "status": "False", "reason": "KubeletHasSufficientMemory",
                 "message": "kubelet has sufficient memory available", "lastHeartbeatTime": ts(0),
                 "lastTransitionTime": ts(-60 * 24 * 60)}],
                "capacity": {"cpu": "8", "memory": "32Gi", "pods": "110"},
                "allocatable": {"cpu": "7800m", "memory": "31Gi", "pods": "110"},
                "nodeInfo": {"kubeletVersion": "v1.35.5", "architecture": "amd64", "bootID": h(name, n=8),
                             "containerRuntimeVersion": "containerd://2.1.4", "kernelVersion": "6.8.0",
                             "kubeProxyVersion": "v1.35.5", "machineID": h(name, "m", n=32),
                             "operatingSystem": "linux", "osImage": "Ubuntu 24.04", "systemUUID": uid("Node", "u", name)}}}


def alert(name, severity, labels, summary, description, active):
    return {"labels": {"alertname": name, "severity": severity, **labels},
            "annotations": {"summary": summary, "description": description},
            "state": "firing", "activeAt": ts(active), "value": "1"}


def as_list(kind, items):
    return {"apiVersion": "v1", "kind": f"{kind}List", "metadata": {}, "items": items}


# ---------------------------------------------------------------------------------------------------- GitOps objects

def mf(manager, operation, minutes, fields, subresource=None, api="apps/v1"):
    e = {"manager": manager, "operation": operation, "apiVersion": api, "time": ts(minutes),
         "fieldsType": "FieldsV1", "fieldsV1": fields}
    if subresource:
        e["subresource"] = subresource
    return e


def ckey(name):
    return 'k:{"name":"' + name + '"}'


def application(name, dest_ns, path, resources, sync="Synced", health="Healthy", revision=GIT_OLD, op=None,
                self_heal=True, ssa=True, reconciled=0):
    return {"apiVersion": "argoproj.io/v1alpha1", "kind": "Application",
            "metadata": {"name": name, "namespace": "argocd", "uid": uid("Application", "argocd", name),
                         "generation": 3, "creationTimestamp": ts(-60 * 24 * 90)},
            "spec": {"project": "default",
                     "source": {"repoURL": REPO, "path": path, "targetRevision": "main"},
                     "destination": {"server": "https://kubernetes.default.svc", "namespace": dest_ns},
                     "syncPolicy": {"automated": {"prune": True, "selfHeal": self_heal},
                                    "syncOptions": ["ServerSideApply=true"] if ssa else []}},
            "status": {"sync": {"status": sync, "revision": revision,
                                "comparedTo": {"source": {"repoURL": REPO, "path": path, "targetRevision": "main"},
                                               "destination": {"server": "https://kubernetes.default.svc",
                                                               "namespace": dest_ns}}},
                       "health": {"status": health},
                       "resources": [{"group": g, "version": v, "kind": k, "namespace": dest_ns, "name": n,
                                      "status": st, **({"health": {"status": hs}} if hs else {})}
                                     for g, v, k, n, st, hs in resources],
                       **({"operationState": op} if op else {}),
                       "reconciledAt": ts(reconciled), "sourceType": "Directory"}}


def operation(revision, started, finished, message, phase, automated=True):
    """``phase`` is required by the CRD, which gives no values. Argo CD's notification triggers name
    ``Succeeded`` for a successful sync and ``['Error', 'Failed']`` for a failed one, without saying which a given
    failure produces: ``Failed`` is used, to confirm live (docs/s2g/gitops-cases.md)."""
    op = {"operation": {"sync": {"revision": revision}, "initiatedBy": {"automated": automated}},
          "startedAt": ts(started), "finishedAt": ts(finished), "message": message,
          "syncResult": {"revision": revision, "source": {"repoURL": REPO, "targetRevision": "main"}},
          "phase": phase}
    return op


def condition(ctype, status, reason, message, minutes):
    return {"type": ctype, "status": status, "reason": reason, "message": message, "lastTransitionTime": ts(minutes),
            "observedGeneration": 2}


def kustomization(name, path, entries, conditions, applied, attempted):
    return {"apiVersion": "kustomize.toolkit.fluxcd.io/v1", "kind": "Kustomization",
            "metadata": {"name": name, "namespace": "flux-system", "uid": uid("Kustomization", "flux-system", name),
                         "generation": 2, "creationTimestamp": ts(-60 * 24 * 90)},
            "spec": {"interval": "5m", "path": path, "prune": True,
                     "sourceRef": {"kind": "GitRepository", "name": "flux-system"}},
            "status": {"observedGeneration": 2, "conditions": conditions,
                       "lastAppliedRevision": applied, "lastAttemptedRevision": attempted,
                       "inventory": {"entries": [{"id": f"{ns}_{n}_{g}_{k}", "v": v} for ns, n, g, k, v in entries]}}}


def git_repository(revision):
    return {"apiVersion": "source.toolkit.fluxcd.io/v1", "kind": "GitRepository",
            "metadata": {"name": "flux-system", "namespace": "flux-system",
                         "uid": uid("GitRepository", "flux-system", "flux-system"), "generation": 1},
            "spec": {"interval": "1m", "url": REPO, "ref": {"branch": "main"}},
            "status": {"observedGeneration": 1, "conditions": [
                condition("Ready", "True", "Succeeded", f"stored artifact for revision 'main@sha1:{revision}'", -10)],
                "artifact": {"path": f"gitrepository/flux-system/flux-system/{revision}.tar.gz",
                             "url": f"http://source-controller.flux-system.svc/gitrepository/flux-system/{revision}.tar.gz",
                             "revision": f"main@sha1:{revision}", "digest": f"sha256:{h(revision)}",
                             "lastUpdateTime": ts(-10)}}}


def helm_repository():
    return {"apiVersion": "source.toolkit.fluxcd.io/v1", "kind": "HelmRepository",
            "metadata": {"name": "acme", "namespace": "flux-system", "uid": uid("HelmRepository", "flux-system", "acme"),
                         "generation": 1},
            "spec": {"interval": "10m", "url": "https://charts.example.com/acme"},
            "status": {"observedGeneration": 1, "conditions": [
                condition("Ready", "True", "Succeeded", "stored artifact: revision 'sha256:" + h("idx", n=12) + "'", -30)]}}


def history_entry(version, chart_version, status, deployed):
    return {"name": "pricing", "namespace": "shop", "version": version, "status": status, "chartName": "pricing",
            "chartVersion": chart_version, "appVersion": chart_version, "configDigest": f"sha256:{h('cfg', chart_version)}",
            "digest": f"sha256:{h('rel', str(version))}", "firstDeployed": ts(-60 * 24 * 60), "lastDeployed": ts(deployed)}


def helmrelease(conditions, history, attempted, entries):
    return {"apiVersion": "helm.toolkit.fluxcd.io/v2", "kind": "HelmRelease",
            "metadata": {"name": "pricing", "namespace": "shop", "uid": uid("HelmRelease", "shop", "pricing"),
                         "generation": 4, "creationTimestamp": ts(-60 * 24 * 60)},
            "spec": {"interval": "10m",
                     "chart": {"spec": {"chart": "pricing", "version": ">=1.4.0",
                                        "sourceRef": {"kind": "HelmRepository", "name": "acme", "namespace": "flux-system"}}},
                     "upgrade": {"remediation": {"retries": 1}}},
            "status": {"observedGeneration": 4, "conditions": conditions, "history": history,
                       "lastAttemptedRevision": attempted,
                       "inventory": {"entries": [{"id": f"{ns}_{n}_{g}_{k}", "v": v} for ns, n, g, k, v in entries]}}}


def certificate(name, ns, ready=True):
    return {"apiVersion": "cert-manager.io/v1", "kind": "Certificate",
            "metadata": {"name": name, "namespace": ns, "uid": uid("Certificate", ns, name), "generation": 1},
            "spec": {"secretName": f"{name}-tls", "dnsNames": [f"{name}.acme.example"],
                     "issuerRef": {"name": "letsencrypt", "kind": "ClusterIssuer"}},
            "status": {"conditions": [{"type": "Ready", "status": "True" if ready else "False",
                                       "reason": "Ready", "message": "Certificate is up to date and has not expired",
                                       "lastTransitionTime": ts(-30), "observedGeneration": 1}],
                       "notAfter": ts(60 * 24 * 89), "renewalTime": ts(60 * 24 * 59), "revision": 4}}


# ---------------------------------------------------------------------------------------------------- the world

def platform():
    """Healthy objects every case has: the GitOps controllers, monitoring, DNS, three nodes."""
    apps = [App("argocd-server", "argocd", "v3.6.1", replicas=1), App("argocd-repo-server", "argocd", "v3.6.1", replicas=1),
            App("kustomize-controller", "flux-system", "v1.10.1", replicas=1),
            App("helm-controller", "flux-system", "v1.7.1", replicas=1),
            App("source-controller", "flux-system", "v1.10.1", replicas=1),
            App("prometheus", "monitoring", "v3.5.0", mem="2Gi", replicas=1),
            App("coredns", "kube-system", "1.12.3", mem="170Mi", replicas=2)]
    return apps


def shop_apps():
    return [App("cart", "shop", "2.3.1"), App("catalog", "shop", "3.2.0", replicas=3),
            App("checkout", "shop", "5.1.0", env=[{"name": "PAYMENT_TIMEOUT",
                                                   "valueFrom": {"configMapKeyRef": {"name": "checkout-config",
                                                                                     "key": "PAYMENT_TIMEOUT"}}}]),
            App("search", "shop", "2.4.0"), App("pricing", "shop", "1.4.0"), App("api", "shop", "4.0.2", replicas=3)]


def billing_apps():
    return [App("billing-worker", "billing", "1.8.0", mem="256Mi", replicas=2), App("billing-api", "billing", "1.8.0")]


def standard_objects(apps, skip=()):
    """Deployment, ReplicaSet, pods, Service for each healthy app."""
    out = {"deployments": [], "replicasets": [], "pods": [], "services": []}
    for a in apps:
        if a.name in skip:
            continue
        out["deployments"].append(deployment(a))
        out["replicasets"].append(replicaset(a))
        out["pods"] += [pod(a, i, NODES[i % 3]) for i in range(a.replicas)]
        out["services"].append(service(a))
    return out


def write_case(case, kube, expect, alerts=(), logs=(), manifest=None, values=None):
    d = OUT / case
    for sub in ("kube", "prometheus", "loki", "helm"):
        p = d / sub
        if p.exists():
            for f in p.iterdir():
                f.unlink()
    (d / "kube").mkdir(parents=True, exist_ok=True)
    for fname, (kind, items) in kube.items():
        (d / "kube" / f"{fname}.yaml").write_text(yaml.safe_dump(as_list(kind, items), sort_keys=False))
    (d / "prometheus").mkdir(exist_ok=True)
    (d / "prometheus" / "alerts.json").write_text(json.dumps(list(alerts), indent=2) + "\n")
    if logs:
        (d / "loki").mkdir(exist_ok=True)
        (d / "loki" / "streams.json").write_text(json.dumps(list(logs), indent=2) + "\n")
    (d / "helm").mkdir(exist_ok=True)
    (d / "helm" / "values.yaml").write_text(yaml.safe_dump(values or {}, sort_keys=False))
    if manifest:
        (d / "helm" / "manifest.yaml").write_text(yaml.safe_dump_all(manifest, sort_keys=False))
    base = {"set": "S2-GitOps", "spec": "docs/s2g/gitops-cases.md",
            "sources": ["https://argo-cd.readthedocs.io/en/stable/user-guide/auto_sync/",
                        "https://argo-cd.readthedocs.io/en/stable/user-guide/sync-options/",
                        "https://fluxcd.io/flux/components/kustomize/kustomizations/",
                        "https://fluxcd.io/flux/components/helm/helmreleases/",
                        "ontology/spec/upstream/bundle (Argo CD v3.6.1, Flux helm v1.7.1, kustomize v1.10.1 CRDs)"]}
    (d / "expect.json").write_text(json.dumps({**base, **expect}, indent=2) + "\n")


def world(extra_apps=(), skip=(), gitops=None, events=(), configmaps=(), extra=None):
    """The kube files of a case: platform + shop + billing, minus ``skip``, plus the case's own objects."""
    apps = platform() + shop_apps() + billing_apps()
    std = standard_objects(apps, skip=skip)
    for k, v in (extra or {}).items():
        std[k] = std.get(k, []) + v
    kube = {"namespaces": ("Namespace", [namespace(n) for n in ("argocd", "flux-system", "shop", "billing",
                                                                 "monitoring", "kube-system")]),
            "nodes": ("Node", [node(n, i) for i, n in enumerate(NODES)]),
            "deployments": ("Deployment", std["deployments"]), "replicasets": ("ReplicaSet", std["replicasets"]),
            "pods": ("Pod", std["pods"]), "services": ("Service", std["services"]),
            "events": ("Event", list(events)),
            "configmaps": ("ConfigMap", [checkout_config("30s")] + list(configmaps))}
    for fname, (kind, items) in (gitops or {}).items():
        kube[fname] = (kind, items)
    if "certificates" not in kube:
        kube["certificates"] = ("Certificate", [certificate("storefront", "shop")])
    return kube


def checkout_config(timeout, managed_minutes=None):
    cm = {"apiVersion": "v1", "kind": "ConfigMap",
          "metadata": {"name": "checkout-config", "namespace": "shop", "uid": uid("ConfigMap", "shop", "checkout-config"),
                       "creationTimestamp": ts(-60 * 24 * 90)},
          "data": {"PAYMENT_TIMEOUT": timeout, "PAYMENT_PROVIDER": "acquirer-b"}}
    if managed_minutes is not None:
        cm["metadata"]["managedFields"] = [mf("argocd-controller", "Apply", managed_minutes,
                                              {"f:data": {"f:PAYMENT_TIMEOUT": {}, "f:PAYMENT_PROVIDER": {}}}, api="v1")]
    return cm


def res(a: App, status="Synced", health="Healthy"):
    return ("apps", "v1", "Deployment", a.name, status, health)


def by_name(name):
    return next(a for a in shop_apps() + billing_apps() + platform() if a.name == name)


# ---------------------------------------------------------------------------------------------------- the cases

def g1():
    w = by_name("billing-worker")
    fixed = "768Mi"
    new_hash = h(w.name, w.ns, w.tag, fixed, n=10)
    argo_fields = {"f:metadata": {"f:labels": {"f:app.kubernetes.io/name": {}, "f:app.kubernetes.io/part-of": {}}},
                   "f:spec": {"f:replicas": {}, "f:selector": {}, "f:template": {"f:metadata": {"f:labels": {}},
                              "f:spec": {"f:containers": {ckey(w.name): {".": {}, "f:image": {}, "f:name": {},
                                         "f:ports": {}, "f:readinessProbe": {},
                                         "f:resources": {"f:limits": {"f:memory": {}},
                                                         "f:requests": {"f:cpu": {}, "f:memory": {}}}}}}}}}
    managed = [mf("argocd-controller", "Apply", 2, argo_fields),
               mf("kube-controller-manager", "Update", 3, {"f:metadata": {"f:annotations": {
                   "f:deployment.kubernetes.io/revision": {}}}, "f:status": {"f:conditions": {}, "f:replicas": {}}},
                  subresource="status")]
    oom = {"terminated": {"reason": "OOMKilled", "exitCode": 137, "startedAt": ts(4), "finishedAt": ts(21)}}
    waiting = {"waiting": {"reason": "CrashLoopBackOff", "message": f"back-off 2m40s restarting failed container={w.name}"}}
    worker_pods = [pod(w, i, NODES[i], ready=False, restarts=3 + i, state=waiting, last=oom, started=3) for i in range(2)]
    # the hand fix's ReplicaSet (revision 2), scaled to 0 by the revert; the original one is revision 3 again
    rs_fix = replicaset(w, replicas=0, rs_hash=new_hash, mem=fixed, revision="2", created=0)
    rs_fix["status"] = {"replicas": 0, "observedGeneration": 1}
    rs_orig = replicaset(w, revision="3")
    api = by_name("billing-api")
    past_oom = {"terminated": {"reason": "OOMKilled", "exitCode": 137, "startedAt": ts(-60 * 74),
                               "finishedAt": ts(-60 * 72)}}
    api_pods = [pod(api, 0, NODES[0], restarts=1, last=past_oom, started=-60 * 72), pod(api, 1, NODES[1])]
    events = [
        event("Deployment", "billing", w.name, "ScalingReplicaSet",
              f"Scaled up replica set {w.name}-{new_hash} from 0 to 1", 0, component="deployment-controller", api="apps/v1"),
        event("Deployment", "billing", w.name, "ScalingReplicaSet",
              f"Scaled down replica set {w.name}-{new_hash} from 1 to 0", 2, component="deployment-controller",
              api="apps/v1"),
        event("Deployment", "billing", w.name, "ScalingReplicaSet",
              f"Scaled up replica set {w.name}-{w.rs_hash} from 0 to 2", 2, component="deployment-controller",
              api="apps/v1"),
        *[event("Pod", "billing", p["metadata"]["name"], "BackOff", "Back-off restarting failed container "
                f"{w.name} in pod {p['metadata']['name']}_billing({p['metadata']['uid']})", 6, 22, count=9,
                etype="Warning") for p in worker_pods]]
    apps_status = [application("billing", "billing", "apps/billing", [res(w, health="Degraded"), res(api)],
                               health="Degraded",
                               op=operation(GIT_OLD, 2, 2.2, "successfully synced (all tasks run)", "Succeeded")),
                   application("storefront", "shop", "apps/shop", [res(by_name(n)) for n in ("cart", "catalog")],
                               sync="OutOfSync", reconciled=-1)]
    kube = world(skip={"billing-worker", "billing-api"}, events=events,
                 gitops={"applications": ("Application", apps_status)},
                 extra={"deployments": [deployment(w, ready=0, managed=managed, revision="3", generation=3),
                                        deployment(api)],
                        "replicasets": [rs_orig, rs_fix, replicaset(api)],
                        "pods": worker_pods + api_pods, "services": [service(w), service(api)]})
    git = copy.deepcopy(deployment(w))
    for k in ("status", "metadata"):
        git.pop(k)
    git["metadata"] = {"name": w.name, "namespace": "billing", "labels": w.meta_labels()}
    alerts = [alert("KubePodCrashLooping", "warning", {"namespace": "billing", "pod": p["metadata"]["name"],
                                                       "container": w.name},
                    "Pod is crash looping.", f"Pod billing/{p['metadata']['name']} ({w.name}) is in waiting state "
                    "(reason: \"CrashLoopBackOff\").", 10) for p in worker_pods]
    write_case("g1_argocd_selfheal_reverts_fix", kube, {
        "family": "gitops", "namespace": "billing", "release": "billing",
        "scenario": "an operator raised billing-worker's memory limit by hand; Argo CD's self-heal reapplied Git's "
                    "256Mi and the worker is OOMKilled again",
        "query": "why does billing-worker keep getting OOMKilled in billing",
        "expected_outcome": "cause", "cause_object": "Application/argocd/billing",
        "mechanism": "selfHeal reapplies the Git revision's 256Mi limit two minutes after the hand fix to 768Mi; the "
                     "fix belongs in Git",
        "pass_if": "names the Application's self-heal reverting the limit to Git's value",
        "partial_if_only": ["the memory limit is too low, without Argo CD"],
        "chain": ["billing-worker's pods are OOMKilled and crash-loop",
                  "the Deployment's memory limit is 256Mi, owned by argocd-controller since T0+2 min",
                  "a ReplicaSet of revision 2 with 768Mi was created at T0 and scaled to 0 at T0+2 min",
                  "Application/argocd/billing, selfHeal true, synced Git's revision at T0+2 min"],
        "decoys": [{"object": "Pod of billing-api, OOMKilled 72h ago", "why_not": "running and ready since"},
                   {"object": "Application/argocd/storefront OutOfSync", "why_not": "another namespace, healthy"},
                   {"object": "Certificate/shop/storefront", "why_not": "Ready, renewal in 59 days"}],
        "affected_resources": ["Deployment/billing/billing-worker"],
        "root_cause_concepts": ["argo|application|self-heal|selfheal|sync|git", "memory|oom|limit"]},
        alerts=alerts, manifest=[git], values={})


def g2():
    c = by_name("checkout")
    waiting = {"waiting": {"reason": "CrashLoopBackOff", "message": f"back-off 5m0s restarting failed container={c.name}"}}
    last = {"terminated": {"reason": "Error", "exitCode": 1, "startedAt": ts(-1), "finishedAt": ts(-0.9)}}
    pods = [pod(c, i, NODES[i], ready=False, restarts=14 + i, state=waiting, last=last, started=-60 * 3) for i in range(2)]
    msg = ('one or more objects failed to apply, reason: Ingress.networking.k8s.io "checkout" is invalid: '
           'spec.rules[0].http.paths[0].pathType: Unsupported value: "Prefixx": supported values: "Exact", '
           '"ImplementationSpecific", "Prefix"')
    apps_status = [application("checkout", "shop", "apps/checkout",
                               [res(c, status="Synced", health="Degraded"),
                                ("", "v1", "ConfigMap", "checkout-config", "OutOfSync", None),
                                ("networking.k8s.io", "v1", "Ingress", "checkout", "OutOfSync", "Missing")],
                               sync="OutOfSync", health="Degraded", revision=GIT_NEW,
                               op=operation(GIT_NEW, -40, -39.8, msg, "Failed")),
                   application("billing", "billing", "apps/billing", [res(by_name("billing-worker")),
                                                                     res(by_name("billing-api"))])]
    events = [event("Pod", "shop", p["metadata"]["name"], "BackOff", "Back-off restarting failed container "
                    f"{c.name} in pod {p['metadata']['name']}_shop({p['metadata']['uid']})", -170, -1, count=40,
                    etype="Warning") for p in pods]
    kube = world(skip={"checkout"}, events=events, gitops={"applications": ("Application", apps_status)},
                 extra={"deployments": [deployment(c, ready=0)], "replicasets": [replicaset(c)], "pods": pods,
                        "services": [service(c)]})
    kube["configmaps"] = ("ConfigMap", [checkout_config("30x", managed_minutes=-60 * 26)])
    logs = [{"stream": {"k8s_pod_name": p["metadata"]["name"], "k8s_namespace_name": "shop",
                        "k8s_container_name": c.name},
             "values": [[ns_epoch(m), 'level=fatal msg="invalid configuration" key=PAYMENT_TIMEOUT value="30x" '
                                      'err="time: unknown unit \\"x\\" in duration \\"30x\\""'] for m in (-60, -30, -1)]}
            for p in pods]
    alerts = [alert("KubePodCrashLooping", "warning", {"namespace": "shop", "pod": p["metadata"]["name"],
                                                       "container": c.name}, "Pod is crash looping.",
                    f"Pod shop/{p['metadata']['name']} ({c.name}) is in waiting state (reason: \"CrashLoopBackOff\").",
                    -150) for p in pods]
    write_case("g2_argocd_sync_failed_invalid_resource", kube, {
        "family": "gitops", "namespace": "shop", "release": "checkout",
        "scenario": "the fix of checkout's PAYMENT_TIMEOUT was merged in Git; the Application's sync of that revision "
                    "fails on an invalid Ingress of the same revision, so the live ConfigMap keeps 30x",
        "query": "why is checkout still failing after the fix was merged in shop",
        "expected_outcome": "cause", "cause_object": "Application/argocd/checkout",
        "mechanism": "its last sync of the Git revision with the fix failed on an invalid Ingress; the ConfigMap with "
                     "the bad value, written 26 h ago, stays live",
        "pass_if": "names the Application's failed sync as why the fix is not live",
        "partial_if_only": ["checkout crashes on its PAYMENT_TIMEOUT value, without the failed sync"],
        "chain": ["checkout's pods exit on PAYMENT_TIMEOUT=30x",
                  "ConfigMap/shop/checkout-config holds 30x, last written by argocd-controller 26 h ago, OutOfSync",
                  "Application/argocd/checkout's last sync of the new revision failed on Ingress checkout"],
        "decoys": [{"object": "ConfigMap/shop/checkout-config", "why_not": "holds the bad value but is the old "
                                                                           "revision's, not the cause of it staying"},
                   {"object": "Application/argocd/billing", "why_not": "Synced and Healthy"}],
        "affected_resources": ["Deployment/shop/checkout"],
        "root_cause_concepts": ["argo|application|sync|ingress", "fail|invalid|unsupported"]},
        alerts=alerts, logs=logs)


FLUX_SHOP = [("shop", n, "apps", "Deployment", "v1") for n in ("cart", "catalog", "search", "api")] + \
            [("shop", n, "", "Service", "v1") for n in ("cart", "catalog", "search", "api")]


def g3():
    s = by_name("search")
    logs = [{"stream": {"k8s_pod_name": p["metadata"]["name"], "k8s_namespace_name": "shop",
                        "k8s_container_name": s.name},
             "values": [[ns_epoch(m), 'level=error msg="ranking failed" err="index out of range [3] with length 3" '
                                      'query_id=q-' + h(str(m), n=6)] for m in (-50, -20, -2)]}
            for p in standard_objects([s])["pods"]]
    ks = kustomization("apps", "./apps/shop", FLUX_SHOP,
                       [condition("Ready", "False", "BuildFailed",
                                  "kustomize build failed: accumulating resources: accumulation err='accumulating "
                                  "resources from 'search': read apps/shop/search/deployment.yaml: yaml: line 14: "
                                  "mapping values are not allowed in this context'", -55)],
                       applied=f"main@sha1:{GIT_OLD}", attempted=f"main@sha1:{GIT_NEW}")
    infra = kustomization("infra", "./infra", [("monitoring", "prometheus", "apps", "Deployment", "v1")],
                          [condition("Ready", "True", "ReconciliationSucceeded",
                                     f"Applied revision: main@sha1:{GIT_NEW}", -55)],
                          applied=f"main@sha1:{GIT_NEW}", attempted=f"main@sha1:{GIT_NEW}")
    alerts = [alert("HighErrorRate", "critical", {"namespace": "shop", "deployment": "search"},
                    "search error rate above 5%", "12% of GET /search requests fail.", -45)]
    kube = world(gitops={"kustomizations": ("Kustomization", [ks, infra]),
                         "gitrepositories": ("GitRepository", [git_repository(GIT_NEW)])})
    write_case("g3_flux_kustomization_build_failed", kube, {
        "family": "gitops", "namespace": "shop", "release": "search",
        "scenario": "the fix of search's ranking bug is in Git's new revision; Flux's Kustomization fails to build "
                    "that revision, so the last applied one, with the bug, stays live",
        "query": "why does search still return errors in shop",
        "expected_outcome": "cause", "cause_object": "Kustomization/flux-system/apps",
        "mechanism": "Ready=False BuildFailed on the new revision; lastAppliedRevision is the old one",
        "pass_if": "names the Kustomization's failed build as why the fix is not applied",
        "partial_if_only": ["search has a ranking bug, without Flux"],
        "chain": ["search logs index out of range errors", "Deployment/shop/search is listed in the Kustomization's "
                  "inventory", "Kustomization/flux-system/apps: Ready False BuildFailed, attempted the new revision, "
                  "applied the old"],
        "decoys": [{"object": "Kustomization/flux-system/infra", "why_not": "Ready, applied the new revision"},
                   {"object": "GitRepository/flux-system/flux-system", "why_not": "Ready, serves the new revision"}],
        "affected_resources": ["Deployment/shop/search"],
        "root_cause_concepts": ["kustomization|flux|build", "fail|buildfailed"]},
        alerts=alerts, logs=logs)


def g4():
    p = by_name("pricing")
    new_hash = h(p.name, "1.5.0", n=10)
    hr = helmrelease(
        [condition("Released", "False", "UpgradeFailed",
                   "Helm upgrade failed for release shop/pricing with chart pricing@1.5.0: context deadline exceeded",
                   -25),
         condition("Remediated", "True", "RollbackSucceeded",
                   "Helm rollback to previous release shop/pricing.v4 with chart pricing@1.4.0 succeeded", -24)],
        [history_entry(6, "1.4.0", "deployed", -24), history_entry(5, "1.5.0", "failed", -30),
         history_entry(4, "1.4.0", "superseded", -60 * 24 * 7)],
        attempted="1.5.0", entries=[("shop", "pricing", "apps", "Deployment", "v1"), ("shop", "pricing", "", "Service", "v1")])
    events = [event("Deployment", "shop", p.name, "ScalingReplicaSet",
                    f"Scaled up replica set {p.name}-{new_hash} from 0 to 1", -30, component="deployment-controller",
                    api="apps/v1"),
              event("Deployment", "shop", p.name, "ScalingReplicaSet",
                    f"Scaled down replica set {p.name}-{new_hash} from 1 to 0", -24, component="deployment-controller",
                    api="apps/v1")]
    kube = world(events=events, gitops={"helmreleases": ("HelmRelease", [hr]),
                                        "helmrepositories": ("HelmRepository", [helm_repository()])})
    write_case("g4_flux_helmrelease_upgrade_rolled_back", kube, {
        "family": "gitops", "namespace": "shop", "release": "pricing",
        "scenario": "the HelmRelease's upgrade of pricing to 1.5.0 timed out and was rolled back to 1.4.0, the version "
                    "with the old behaviour",
        "query": "why is pricing still on the old behaviour in shop",
        "expected_outcome": "cause", "cause_object": "HelmRelease/shop/pricing",
        "mechanism": "Released=False UpgradeFailed for 1.5.0, then Remediated RollbackSucceeded to 1.4.0",
        "pass_if": "names the HelmRelease's failed upgrade and rollback",
        "partial_if_only": ["pricing runs 1.4.0, without the failed upgrade"],
        "chain": ["Deployment/shop/pricing runs image 1.4.0", "it is in HelmRelease/shop/pricing's inventory",
                  "the HelmRelease attempted 1.5.0, failed and rolled back (history v5 failed, v6 deployed 1.4.0)"],
        "decoys": [{"object": "ReplicaSet of pricing 1.5.0, scaled to 0", "why_not": "the effect of the rollback"},
                   {"object": "HelmRepository/flux-system/acme", "why_not": "Ready"}],
        "affected_resources": ["Deployment/shop/pricing"],
        "root_cause_concepts": ["helmrelease|helm|upgrade|rollback", "fail|upgradefailed|rolled back|rollback"]})


def g5():
    a = by_name("api")
    events = []
    for i, m in enumerate(range(-50, 0, 5)):
        up = i % 2 == 0
        events.append(event("Deployment", "shop", a.name, "ScalingReplicaSet",
                            f"Scaled {'up' if up else 'down'} replica set {a.name}-{a.rs_hash} from "
                            f"{3 if up else 5} to {5 if up else 3}", m, component="deployment-controller", api="apps/v1"))
    managed = [mf("argocd-controller", "Apply", -5, {"f:spec": {"f:replicas": {}}})]
    apps_status = [application("platform", "shop", "apps/platform", [res(a, status="OutOfSync")], sync="OutOfSync",
                               op=operation(GIT_OLD, -5, -4.9, "successfully synced (all tasks run)", "Succeeded"))]
    ks = kustomization("apps", "./apps/shop", FLUX_SHOP,
                       [condition("Ready", "True", "ReconciliationSucceeded", f"Applied revision: main@sha1:{GIT_OLD}", -2)],
                       applied=f"main@sha1:{GIT_OLD}", attempted=f"main@sha1:{GIT_OLD}")
    alerts = [alert("KubeDeploymentReplicasMismatch", "warning", {"namespace": "shop", "deployment": a.name},
                    "Deployment has not matched the expected number of replicas.",
                    f"Deployment shop/{a.name} has not matched the expected number of replicas for longer than 15 minutes.",
                    -30)]
    kube = world(skip={"api"}, events=events,
                 gitops={"applications": ("Application", apps_status), "kustomizations": ("Kustomization", [ks]),
                         "gitrepositories": ("GitRepository", [git_repository(GIT_OLD)])},
                 extra={"deployments": [deployment(a, managed=managed)], "replicasets": [replicaset(a)],
                        "pods": [pod(a, i, NODES[i % 3]) for i in range(a.replicas)], "services": [service(a)]})
    write_case("g5_two_managers_flap", kube, {
        "family": "gitops", "namespace": "shop", "release": "api",
        "scenario": "Deployment shop/api is in both Argo CD's Application platform and Flux's Kustomization apps, "
                    "which reconcile its replicas to different values",
        "query": "why does the replica count of api keep changing in shop",
        "expected_outcome": "cause", "cause_object": "Application/argocd/platform",
        "cause_objects": ["Application/argocd/platform", "Kustomization/flux-system/apps"],
        "mechanism": "two GitOps tools manage the same Deployment; each reconcile sets its own replica count",
        "pass_if": "names both managers owning the Deployment",
        "partial_if_only": ["one of the two managers only"],
        "chain": ["api's ReplicaSet is scaled 3 to 5 and back every 5 minutes",
                  "Deployment/shop/api is in Application/argocd/platform's resources",
                  "and in Kustomization/flux-system/apps's inventory"],
        "decoys": [{"object": "an HPA", "why_not": "there is none on api"}],
        "affected_resources": ["Deployment/shop/api"],
        "root_cause_concepts": ["argo|application", "flux|kustomization"]},
        alerts=alerts)


def g0():
    w = by_name("billing-worker")
    apps_status = [application("billing", "billing", "apps/billing", [res(w), res(by_name("billing-api"))],
                               op=operation(GIT_NEW, -60 * 20, -60 * 20 + 0.2, "successfully synced (all tasks run)", "Succeeded")),
                   application("checkout", "shop", "apps/checkout", [res(by_name("checkout"))])]
    ks = kustomization("apps", "./apps/shop", FLUX_SHOP,
                       [condition("Ready", "True", "ReconciliationSucceeded", f"Applied revision: main@sha1:{GIT_NEW}", -3)],
                       applied=f"main@sha1:{GIT_NEW}", attempted=f"main@sha1:{GIT_NEW}")
    hr = helmrelease([condition("Released", "True", "UpgradeSucceeded",
                                "Helm upgrade succeeded for release shop/pricing.v5 with chart pricing@1.5.0", -60 * 22)],
                     [history_entry(5, "1.5.0", "deployed", -60 * 22), history_entry(4, "1.4.0", "superseded", -60 * 24 * 7)],
                     attempted="1.5.0", entries=[("shop", "pricing", "apps", "Deployment", "v1")])
    old_oom = {"terminated": {"reason": "OOMKilled", "exitCode": 137, "startedAt": ts(-60 * 50), "finishedAt": ts(-60 * 49)}}
    api = by_name("billing-api")
    events = [event("Deployment", "billing", w.name, "ScalingReplicaSet",
                    f"Scaled up replica set {w.name}-{w.rs_hash} from 1 to 2", -60 * 20, component="deployment-controller",
                    api="apps/v1")]
    kube = world(skip={"billing-api"}, events=events,
                 gitops={"applications": ("Application", apps_status), "kustomizations": ("Kustomization", [ks]),
                         "helmreleases": ("HelmRelease", [hr]), "gitrepositories": ("GitRepository", [git_repository(GIT_NEW)]),
                         "helmrepositories": ("HelmRepository", [helm_repository()])},
                 extra={"deployments": [deployment(api)], "replicasets": [replicaset(api)],
                        "pods": [pod(api, 0, NODES[0], restarts=1, last=old_oom, started=-60 * 49), pod(api, 1, NODES[1])],
                        "services": [service(api)]})
    write_case("g0_gitops_no_incident", kube, {
        "family": "gitops", "namespace": "billing", "release": "billing",
        "scenario": "every Application Synced and Healthy, the Kustomization Ready, the HelmRelease Released; an "
                    "OOMKill two days ago, recovered",
        "query": "is anything wrong with billing",
        "expected_outcome": "no_incident", "cause_object": "",
        "decoys": [{"object": "Pod of billing-api, OOMKilled 49h ago", "why_not": "running and ready since"},
                   {"object": "HelmRelease/shop/pricing history", "why_not": "superseded revision, not a failure"}],
        "affected_resources": [], "root_cause_concepts": []})


EVIDENCE = {
    "g1_argocd_selfheal_reverts_fix": [
        {"hop": "billing-worker's pods are OOMKilled", "file": "kube/pods.yaml", "contains": "reason: OOMKilled",
         "join": "the pods' ReplicaSet, then the Deployment, by ownerReferences"},
        {"hop": "the Deployment's memory limit is 256Mi, owned by argocd-controller", "file": "kube/deployments.yaml",
         "contains": "manager: argocd-controller", "join": "argocd-controller is Argo CD's server-side apply manager"},
        {"hop": "the hand fix's ReplicaSet with 768Mi, scaled to 0", "file": "kube/replicasets.yaml",
         "contains": "memory: 768Mi", "join": "the events of T0 and T0+2 min"},
        {"hop": "the Application lists the Deployment and self-heals", "file": "kube/applications.yaml",
         "contains": "selfHeal: true", "join": "status.resources[] names Deployment billing-worker"}],
    "g2_argocd_sync_failed_invalid_resource": [
        {"hop": "checkout exits on PAYMENT_TIMEOUT=30x", "file": "loki/streams.json", "contains": "value=\\\"30x\\\"",
         "join": "the env's configMapKeyRef, checkout-config"},
        {"hop": "the ConfigMap holds 30x, OutOfSync", "file": "kube/configmaps.yaml", "contains": "PAYMENT_TIMEOUT: 30x",
         "join": "the Application's status.resources[] lists it OutOfSync"},
        {"hop": "the last sync failed on the Ingress", "file": "kube/applications.yaml",
         "contains": "one or more objects failed to apply", "join": "—"}],
    "g3_flux_kustomization_build_failed": [
        {"hop": "search logs errors", "file": "loki/streams.json", "contains": "index out of range",
         "join": "the Deployment search"},
        {"hop": "search is in the Kustomization's inventory", "file": "kube/kustomizations.yaml",
         "contains": "shop_search_apps_Deployment", "join": "the inventory id"},
        {"hop": "the Kustomization failed its build", "file": "kube/kustomizations.yaml", "contains": "reason: BuildFailed",
         "join": "lastAttemptedRevision differs from lastAppliedRevision"}],
    "g4_flux_helmrelease_upgrade_rolled_back": [
        {"hop": "pricing runs 1.4.0", "file": "kube/deployments.yaml", "contains": "ghcr.io/acme/pricing:1.4.0",
         "join": "the HelmRelease's inventory"},
        {"hop": "the upgrade failed", "file": "kube/helmreleases.yaml", "contains": "reason: UpgradeFailed", "join": "—"},
        {"hop": "and was rolled back", "file": "kube/helmreleases.yaml", "contains": "reason: RollbackSucceeded",
         "join": "history: v5 failed, v6 deployed 1.4.0"}],
    "g5_two_managers_flap": [
        {"hop": "api's replicas alternate", "file": "kube/events.yaml", "contains": "from 5 to 3", "join": "the Deployment"},
        {"hop": "Argo CD lists it", "file": "kube/applications.yaml", "contains": "name: api", "join": "status.resources[]"},
        {"hop": "Flux lists it", "file": "kube/kustomizations.yaml", "contains": "shop_api_apps_Deployment",
         "join": "status.inventory.entries[]"}],
}


def main():
    for build in (g0, g1, g2, g3, g4, g5):
        build()
    (ROOT / "evidence.yaml").write_text(
        "# The evidence for each hop of each S2-GitOps case (docs/s2g/gitops-cases.md), written with the builder.\n"
        + yaml.safe_dump(EVIDENCE, sort_keys=False, allow_unicode=True))


if __name__ == "__main__":
    main()
