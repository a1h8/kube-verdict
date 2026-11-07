"""Builds S2's LimitRange family (docs/s2/family-limitrange.md): l1, l2 and their negative l0.

Written 2026-11-07 from the family spec, before any arm ran on these cases. Frozen by hash before
the first run (docs/s2-correlation-cases.md). Nothing in a fixture says which object is the cause.

    python tests/s2/build_family_limitrange.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_family_quota import OUT, alert, as_list, event, ts, write  # noqa: E402

NODES = ["worker-node-01", "worker-node-02", "worker-node-03"]
# app: image tag, cpu request, memory request, memory limit declared (None = none), replicas
APPS = {"report-worker": ("2.6.0", "250m", "512Mi", None, 2),
        "notify-worker": ("1.1.0", "100m", "128Mi", None, 1),
        "cart-api": ("2.3.1", "250m", "512Mi", "1Gi", 2),
        "search-api": ("4.0.2", "500m", "1Gi", "2Gi", 3),
        "checkout-web": ("5.1.0", "200m", "256Mi", "512Mi", 2)}
RS = {"report-worker": "7d9c8b6f5", "notify-worker": "5c8b9d7f6", "cart-api": "5d6f8c7b9",
      "search-api": "6f9c8d7b4", "checkout-web": "9b8c7d6f5"}
SEARCH_NEW_RS = "7c8d9b6f5"


def limitrange(variant: str) -> dict:
    default_mem = "256Mi" if variant == "l1" else "1Gi"
    max_mem = "1Gi" if variant == "l2" else "4Gi"
    created = 0 if variant == "l1" else -43200
    lr = {"apiVersion": "v1", "kind": "LimitRange",
          "metadata": {"name": "container-defaults", "namespace": "shop", "uid": "lr-shop-container-defaults",
                       "creationTimestamp": ts(created),
                       "managedFields": [{"manager": "kubectl-client-side-apply", "operation": "Update",
                                          "time": ts(0 if variant in ("l1", "l2") else -43200)}]},
          "spec": {"limits": [{"type": "Container", "default": {"memory": default_mem},
                               "defaultRequest": {"memory": "128Mi"}, "max": {"memory": max_mem}}]}}
    return lr


def container(app: str, tag: str, cpu: str, mem: str, limit: str | None) -> dict:
    res = {"requests": {"cpu": cpu, "memory": mem}}
    if limit:
        res["limits"] = {"memory": limit}
    return {"name": app, "image": f"ghcr.io/acme/{app}:{tag}", "resources": res,
            "ports": [{"containerPort": 8080}]}


def build(variant: str) -> None:
    injected = "256Mi" if variant == "l1" else "1Gi"
    deployments, pods, events, rss = [], [], [], []
    for k, (app, (tag, cpu, mem, limit, n)) in enumerate(APPS.items()):
        dtag = "4.1.0" if (variant == "l2" and app == "search-api") else tag
        stuck = variant == "l2" and app == "search-api"
        conditions = [{"type": "Available", "status": "True"}]
        if stuck:
            conditions += [{"type": "Progressing", "status": "True", "reason": "ReplicaSetUpdated",
                            "message": f'ReplicaSet "search-api-{SEARCH_NEW_RS}" is progressing.'},
                           {"type": "ReplicaFailure", "status": "True", "reason": "FailedCreate",
                            "message": f'pods "search-api-{SEARCH_NEW_RS}-" is forbidden: maximum memory usage per '
                                       f'Container is 1Gi, but limit is 2Gi'}]
        deployments.append({
            "apiVersion": "apps/v1", "kind": "Deployment",
            "metadata": {"name": app, "namespace": "shop", "uid": f"dep-shop-{app}", "generation": 7 if stuck else 4,
                         "labels": {"app.kubernetes.io/name": app, "app.kubernetes.io/managed-by": "Helm"},
                         "annotations": {"meta.helm.sh/release-name": app, "meta.helm.sh/release-namespace": "shop"}},
            "spec": {"replicas": n, "selector": {"matchLabels": {"app.kubernetes.io/name": app}},
                     "template": {"metadata": {"labels": {"app.kubernetes.io/name": app}},
                                  "spec": {"containers": [container(app, dtag, cpu, mem, limit)]}}},
            "status": {"observedGeneration": 7 if stuck else 4, "replicas": n, "readyReplicas": n,
                       "availableReplicas": n, "updatedReplicas": 0 if stuck else n, "conditions": conditions}})
        rss.append({"apiVersion": "apps/v1", "kind": "ReplicaSet",
                    "metadata": {"name": f"{app}-{RS[app]}", "namespace": "shop",
                                 "ownerReferences": [{"kind": "Deployment", "name": app, "controller": True}]},
                    "spec": {"replicas": n}, "status": {"replicas": n, "readyReplicas": n}})
        if stuck:
            rss.append({"apiVersion": "apps/v1", "kind": "ReplicaSet",
                        "metadata": {"name": f"search-api-{SEARCH_NEW_RS}", "namespace": "shop",
                                     "ownerReferences": [{"kind": "Deployment", "name": app, "controller": True}]},
                        "spec": {"replicas": 1}, "status": {"replicas": 0, "readyReplicas": 0,
                                                            "conditions": [{"type": "ReplicaFailure", "status": "True",
                                                                            "reason": "FailedCreate"}]}})
        for i in range(n):
            name = f"{app}-{RS[app]}-{(i * 7919 + len(app) * 104729) % 1048576:05x}"
            eff_limit = limit or injected
            started = -4000
            restarts, last, state = 0, {}, {"running": {"startedAt": ts(-4000)}}
            annotations = {}
            if limit is None:
                annotations["kubernetes.io/limit-ranger"] = f"LimitRanger plugin set: memory limit for container {app}"
            if variant == "l1" and limit is None:
                started = 31 + i * 2                          # recreated by the drain
                state = {"running": {"startedAt": ts(started)}}
            if variant == "l1" and app == "report-worker":
                restarts = 2
                last = {"terminated": {"reason": "OOMKilled", "exitCode": 137, "startedAt": ts(52 + i),
                                       "finishedAt": ts(72 + i)}}
                state = {"running": {"startedAt": ts(73 + i)}}
            pods.append({
                "apiVersion": "v1", "kind": "Pod",
                "metadata": {"name": name, "namespace": "shop", "uid": f"pod-shop-{name}",
                             "annotations": annotations,
                             "labels": {"app.kubernetes.io/name": app, "pod-template-hash": RS[app]},
                             "ownerReferences": [{"apiVersion": "apps/v1", "kind": "ReplicaSet",
                                                  "name": f"{app}-{RS[app]}", "controller": True}]},
                "spec": {"nodeName": NODES[(i + k) % 3],
                         "containers": [container(app, tag, cpu, mem, eff_limit)]},
                "status": {"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}],
                           "containerStatuses": [{"name": app, "image": f"ghcr.io/acme/{app}:{tag}", "ready": True,
                                                  "restartCount": restarts, "state": state, "lastState": last}]}})

    # healthy neighbours in other namespaces, as a real cluster has
    for ns, app, rs_hash, n, tag in (("monitoring", "prometheus-server", "5b6c7d8e9", 1, "2.53.0"),
                                     ("monitoring", "alertmanager", "6c7d8e9f5", 1, "0.27.0"),
                                     ("kube-system", "coredns", "7db6d8ff4", 2, "1.11.3")):
        deployments.append({"apiVersion": "apps/v1", "kind": "Deployment",
                            "metadata": {"name": app, "namespace": ns, "uid": f"dep-{ns}-{app}"},
                            "spec": {"replicas": n, "selector": {"matchLabels": {"app.kubernetes.io/name": app}},
                                     "template": {"metadata": {"labels": {"app.kubernetes.io/name": app}},
                                                  "spec": {"containers": [container(app, tag, "100m", "256Mi", "512Mi")]}}},
                            "status": {"replicas": n, "readyReplicas": n, "availableReplicas": n, "updatedReplicas": n,
                                       "conditions": [{"type": "Available", "status": "True"}]}})
        for i in range(n):
            name = f"{app}-{rs_hash}-{(i * 7919 + len(app) * 104729) % 1048576:05x}"
            pods.append({"apiVersion": "v1", "kind": "Pod",
                         "metadata": {"name": name, "namespace": ns, "uid": f"pod-{ns}-{name}",
                                      "labels": {"app.kubernetes.io/name": app, "pod-template-hash": rs_hash},
                                      "ownerReferences": [{"apiVersion": "apps/v1", "kind": "ReplicaSet",
                                                           "name": f"{app}-{rs_hash}", "controller": True}]},
                         "spec": {"nodeName": NODES[i % 3], "containers": [container(app, tag, "100m", "256Mi", "512Mi")]},
                         "status": {"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}],
                                    "containerStatuses": [{"name": app, "image": f"ghcr.io/acme/{app}:{tag}", "ready": True,
                                                           "restartCount": 0,
                                                           "state": {"running": {"startedAt": ts(-20000)}}, "lastState": {}}]}})
    services = [{"apiVersion": "v1", "kind": "Service",
                 "metadata": {"name": d["metadata"]["name"], "namespace": d["metadata"]["namespace"],
                              "uid": f"svc-{d['metadata']['namespace']}-{d['metadata']['name']}"},
                 "spec": {"selector": {"app.kubernetes.io/name": d["metadata"]["name"]},
                          "ports": [{"port": 80, "targetPort": 8080}]}} for d in deployments]
    namespaces = [{"apiVersion": "v1", "kind": "Namespace",
                   "metadata": {"name": n, "uid": f"ns-{n}", "labels": {"kubernetes.io/metadata.name": n}}}
                  for n in ("shop", "monitoring", "kube-system")]

    nodes = [{"apiVersion": "v1", "kind": "Node",
              "metadata": {"name": n, "uid": f"node-{n}", "labels": {"kubernetes.io/hostname": n}},
              "spec": {}, "status": {"conditions": [{"type": "Ready", "status": "True"}],
                                     "allocatable": {"cpu": "8", "memory": "32Gi"}}} for n in NODES]

    drain = 30 if variant == "l1" else -2000
    events += [event("Node", "worker-node-02", "NodeNotSchedulable", "Node worker-node-02 status is now: NodeNotSchedulable",
                     drain, ns="default", component="kubelet"),
               event("Node", "worker-node-02", "NodeSchedulable", "Node worker-node-02 status is now: NodeSchedulable",
                     drain + 14, ns="default", component="kubelet")]
    if variant == "l1":
        for p in pods:
            if p["metadata"]["name"].startswith("report-worker-"):
                events.append(event("Pod", p["metadata"]["name"], "BackOff",
                                    f"Back-off restarting failed container report-worker in pod {p['metadata']['name']}",
                                    72, 74, count=2, etype="Warning", component="kubelet"))
    if variant == "l2":
        events += [event("Deployment", "search-api", "ScalingReplicaSet",
                         f"Scaled up replica set search-api-{SEARCH_NEW_RS} to 1", 15, component="deployment-controller"),
                   event("ReplicaSet", f"search-api-{SEARCH_NEW_RS}", "FailedCreate",
                         f'Error creating: pods "search-api-{SEARCH_NEW_RS}-v2kq8" is forbidden: maximum memory usage '
                         f'per Container is 1Gi, but limit is 2Gi', 15, 29, count=8, etype="Warning",
                         component="replicaset-controller")]

    quota = {"apiVersion": "v1", "kind": "ResourceQuota",
             "metadata": {"name": "shop-quota", "namespace": "shop", "uid": "rq-shop-quota"},
             "spec": {"hard": {"requests.memory": "16Gi", "pods": "30"}},
             "status": {"hard": {"requests.memory": "16Gi", "pods": "30"},
                        "used": {"requests.memory": "9600Mi",
                                 "pods": str(sum(p["metadata"]["namespace"] == "shop" for p in pods))}}}

    alerts = [alert("CPUThrottlingHigh", "info", {"namespace": "shop",
                                                  "pod": [p for p in pods if p["metadata"]["name"].startswith("search-api-")][0]["metadata"]["name"],
                                                  "container": "search-api"},
                    "Processes experience elevated CPU throttling.",
                    "29.1% throttling of CPU in namespace shop for container search-api.", -800)]
    if variant == "l1":
        alerts += [alert("ContainerOOMKilled", "warning", {"namespace": "shop", "pod": p["metadata"]["name"],
                                                            "container": "report-worker"},
                         "Container was OOMKilled.",
                         f"Container report-worker in pod shop/{p['metadata']['name']} was OOMKilled 2 times in the last hour.", 73)
                   for p in pods if p["metadata"]["name"].startswith("report-worker-")]

    release = "search-api" if variant == "l2" else "report-worker"
    tag, cpu, mem, limit, n = APPS[release]
    dtag = "4.1.0" if variant == "l2" else tag
    git_values = {"image": {"tag": dtag}, "replicaCount": n,
                  "resources": {"requests": {"cpu": cpu, "memory": mem}, **({"limits": {"memory": limit}} if limit else {})},
                  "logLevel": "debug" if release == "report-worker" else "info"}
    deployed = {**git_values, "logLevel": "info"}
    applied = {k: v for k, v in [d for d in deployments if d["metadata"]["name"] == release][0].items() if k != "status"}

    files = {"kube/namespaces.yaml": as_list("Namespace", namespaces),
             "kube/services.yaml": as_list("Service", services),
             "kube/nodes.yaml": as_list("Node", nodes),
             "kube/limitrange.yaml": limitrange(variant),
             "kube/quota.yaml": quota,
             "kube/deployments.yaml": as_list("Deployment", deployments),
             "kube/replicasets.yaml": as_list("ReplicaSet", rss),
             "kube/pods.yaml": as_list("Pod", pods),
             "kube/events.yaml": as_list("Event", sorted(events, key=lambda e: e["firstTimestamp"])),
             "helm/values.yaml": git_values, "helm/release.json": deployed,
             "prometheus/alerts.json": alerts}
    write(CASE[variant], files, {**COMMON, "release": release, **EXPECT[variant]})
    (OUT / CASE[variant] / "helm" / "manifest.yaml").write_text(yaml.safe_dump(applied, sort_keys=False, width=100000))


CASE = {"l1": "l1_limitrange_default_injected", "l2": "l2_limitrange_max_lowered", "l0": "l0_limitrange_no_incident"}
SOURCES = ["https://kubernetes.io/docs/concepts/policy/limit-range/",
           "prometheus-operator/runbooks@a685d14 KubePodCrashLooping",
           "prometheus-operator/runbooks@a685d14 KubeDeploymentReplicasMismatch"]
COMMON = {"set": "S2", "family": "limitrange", "namespace": "shop", "sources": SOURCES,
          "spec": "docs/s2/family-limitrange.md"}
EXPECT = {
    "l1": {"scenario": "report-worker is OOMKilled; a namespace default memory limit was injected into pods that declare none",
           "query": "why does report-worker keep crashing in shop", "expected_outcome": "cause",
           "cause_object": "LimitRange/shop/container-defaults",
           "mechanism": "its default memory limit (256Mi), created at T0, was set by admission on report-worker's pods "
                        "recreated by a node drain; report-worker declares no limit and needs about 700Mi, so it is OOMKilled",
           "pass_if": "names the LimitRange's default as the source of the 256Mi limit",
           "partial_if_only": ["the memory limit is too low, without the LimitRange"],
           "chain": ["report-worker's pods are OOMKilled about every 20 minutes",
                     "the pods' container has limits.memory 256Mi and the kubernetes.io/limit-ranger annotation",
                     "Deployment/shop/report-worker and its chart declare requests only",
                     "LimitRange/shop/container-defaults, created at T0, defaults memory limits to 256Mi"],
           "decoys": [{"object": "Node/worker-node-02 (drain)", "why_not": "recreated the pods, does not explain the crash"},
                      {"object": "Deployment/shop/cart-api", "why_not": "declares its own limits, unaffected"},
                      {"object": "report-worker logLevel (debug in Git, info deployed)", "why_not": "harmless"}],
           "affected_resources": ["Deployment/shop/report-worker"],
           "root_cause_concepts": ["limitrange|limit range|container-defaults|default limit|limit-ranger", "memory|oom"]},
    "l2": {"scenario": "search-api's rollout is stuck; a lowered namespace maximum refuses its new pods",
           "query": "why is the search-api rollout stuck in shop", "expected_outcome": "cause",
           "cause_object": "LimitRange/shop/container-defaults",
           "mechanism": "its maximum memory per container was lowered to 1Gi at T0, below search-api's 2Gi limit, so "
                        "admission refuses every new pod of the rollout while the old pods keep running",
           "pass_if": "names the LimitRange's maximum against search-api's limit",
           "partial_if_only": ["admission refuses the pods, or the limit is too high, without the LimitRange"],
           "chain": ["search-api's rollout to 4.1.0 stalls: 3 old pods Running, the new ReplicaSet at 0",
                     "ReplicaSet search-api-7c8d9b6f5 FailedCreate: maximum memory usage per Container is 1Gi, but limit is 2Gi",
                     "search-api's chart sets a 2Gi limit, unchanged",
                     "LimitRange/shop/container-defaults had max.memory lowered to 1Gi at T0"],
           "decoys": [{"object": "search-api 4.1.0", "why_not": "the version the rollout ships; the old one would be refused too"},
                      {"object": "Pod/shop/search-api (CPUThrottlingHigh)", "why_not": "real, unrelated"},
                      {"object": "ResourceQuota/shop/shop-quota", "why_not": "at 60 %, room left"}],
           "affected_resources": ["Deployment/shop/search-api"],
           "root_cause_concepts": ["limitrange|limit range|container-defaults|maximum memory|max", "search-api|2gi|limit"]},
    "l0": {"scenario": "No incident: the same objects, a LimitRange that fits every workload",
           "query": "is something wrong in shop", "expected_outcome": "no_incident", "cause_object": None,
           "decoys": [{"object": "Node/worker-node-02 (old drain)", "why_not": "long ago"},
                      {"object": "report-worker logLevel diff", "why_not": "harmless"},
                      {"object": "Pod/shop/search-api (CPUThrottlingHigh)", "why_not": "unrelated"},
                      {"object": "ResourceQuota/shop/shop-quota", "why_not": "at 60 %"}],
           "affected_resources": [], "root_cause_contains": [],
           "notes": "Passes only on an explicit no-incident answer (protocol 002, A2)."},
}

if __name__ == "__main__":
    for v in ("l1", "l2", "l0"):
        build(v)
    print("written:", ", ".join(sorted(CASE.values())))
