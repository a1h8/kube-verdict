"""Builds S2's shared ConfigMap family (docs/s2/family-configmap.md): c1, c2 and their negative c0.

Written 2026-11-07 from the family spec, before any arm ran on these cases. Frozen by hash before
the first run (docs/s2-correlation-cases.md). Nothing in a fixture says which object is the cause.

    python tests/s2/build_family_configmap.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_family_quota import OUT, alert, as_list, event, ns_epoch, ts, write  # noqa: E402

NODES = ["worker-node-01", "worker-node-02", "worker-node-03"]
# app: (tag, reads shop-env, mounts feature-flags, mounts search-config)
APPS = {"cart-api": ("2.3.1", True, False, False), "checkout": ("1.9.2", True, False, False),
        "orders-api": ("3.0.0", True, False, False), "search-api": ("4.0.2", False, True, True),
        "web": ("4.3.0", False, True, False)}
RS = {"cart-api": "5d6f8c7b9", "checkout": "6d9f7c8b5", "orders-api": "7f8c9d6b4", "search-api": "6f9c8d7b4",
      "web": "9c8d7b6f5", "orders-db": "6c7d8b9f4", "coredns": "7db6d8ff4"}
GOOD_HOST = "orders-db.data.svc.cluster.local"
BAD_HOST = "orders-db-v2.data.svc.cluster.local"
FLAGS_OK = ("flags:\n  new-checkout:\n    enabled: true\n    rollout: 25\n  dark-mode:\n    enabled: false\n"
            "  search-v2:\n    enabled: true\n")
FLAGS_PREVIOUS = FLAGS_OK.replace("rollout: 25", "rollout: 10")
FLAGS_BAD = ("flags:\n  new-checkout:\n    enabled: true\n    rollout: 25\n  dark-mode:\n    enabled: false\n"
             "\tsearch-v2:\n    enabled: true\n")                       # a tab on line 7


def container(app: str, tag: str) -> dict:
    _, env, flags, search = APPS[app]
    c = {"name": app, "image": f"ghcr.io/acme/{app}:{tag}", "ports": [{"containerPort": 8080}],
         "resources": {"requests": {"cpu": "200m", "memory": "256Mi"}, "limits": {"memory": "512Mi"}}}
    if env:
        c["envFrom"] = [{"configMapRef": {"name": "shop-env"}}]
    mounts = []
    if flags:
        mounts.append({"name": "flags", "mountPath": "/etc/flags", "readOnly": True})
    if search:
        mounts.append({"name": "search-config", "mountPath": "/etc/search", "readOnly": True})
    if mounts:
        c["volumeMounts"] = mounts
    return c


def volumes(app: str) -> list:
    _, _, flags, search = APPS[app]
    v = []
    if flags:
        v.append({"name": "flags", "configMap": {"name": "feature-flags"}})
    if search:
        v.append({"name": "search-config", "configMap": {"name": "search-config"}})
    return v


def crashing(variant: str, app: str, i: int) -> bool:
    if variant == "c1":
        return (app, i) in (("cart-api", 1), ("checkout", 0))      # recreated by the drain
    if variant == "c2":
        return app in ("search-api", "web")
    return False


def build(variant: str) -> None:
    namespaces = [{"apiVersion": "v1", "kind": "Namespace",
                   "metadata": {"name": n, "uid": f"ns-{n}", "labels": {"kubernetes.io/metadata.name": n}}}
                  for n in ("shop", "data", "kube-system")]
    deployments, rss, pods, events, streams = [], [], [], [], []
    for app, (tag, _, _, _) in APPS.items():
        bad = [i for i in range(2) if crashing(variant, app, i)]
        deployments.append({
            "apiVersion": "apps/v1", "kind": "Deployment",
            "metadata": {"name": app, "namespace": "shop", "uid": f"dep-shop-{app}",
                         "labels": {"app.kubernetes.io/name": app, "app.kubernetes.io/managed-by": "Helm"},
                         "annotations": {"meta.helm.sh/release-name": app, "meta.helm.sh/release-namespace": "shop"}},
            "spec": {"replicas": 2, "selector": {"matchLabels": {"app.kubernetes.io/name": app}},
                     "template": {"metadata": {"labels": {"app.kubernetes.io/name": app}},
                                  "spec": {"containers": [container(app, tag)], "volumes": volumes(app)}}},
            "status": {"replicas": 2, "readyReplicas": 2 - len(bad), "availableReplicas": 2 - len(bad),
                       "updatedReplicas": 2,
                       "conditions": [{"type": "Available", "status": "True" if len(bad) < 2 else "False"}]}})
        rss.append({"apiVersion": "apps/v1", "kind": "ReplicaSet",
                    "metadata": {"name": f"{app}-{RS[app]}", "namespace": "shop",
                                 "ownerReferences": [{"kind": "Deployment", "name": app, "controller": True}]},
                    "spec": {"selector": {"matchLabels": {"app.kubernetes.io/name": app}}, "replicas": 2},
                    "status": {"replicas": 2, "readyReplicas": 2 - len(bad)}})
        for i in range(2):
            name = f"{app}-{RS[app]}-{(i * 7919 + len(app) * 104729) % 1048576:05x}"
            crash = crashing(variant, app, i)
            started = -6000
            if app == "web":
                started = -60                                       # redeployed an hour before T0
            if variant == "c1" and crash:
                started = 21 + i
            state = {"running": {"startedAt": ts(started)}}
            last = {}
            restarts = 0
            if crash:
                state = {"waiting": {"reason": "CrashLoopBackOff",
                                     "message": f"back-off 2m40s restarting failed container={app} pod={name}"}}
                last = {"terminated": {"reason": "Error", "exitCode": 1, "startedAt": ts(52), "finishedAt": ts(52.1)}}
                restarts = 6
            pods.append({"apiVersion": "v1", "kind": "Pod",
                         "metadata": {"name": name, "namespace": "shop", "uid": f"pod-shop-{name}",
                                      "creationTimestamp": ts(started),
                                      "labels": {"app.kubernetes.io/name": app, "pod-template-hash": RS[app]},
                                      "ownerReferences": [{"apiVersion": "apps/v1", "kind": "ReplicaSet",
                                                           "name": f"{app}-{RS[app]}", "controller": True}]},
                         "spec": {"nodeName": "worker-node-02" if (variant == "c1" and crash) else NODES[(i * 2) % 3],
                                  "containers": [container(app, tag)], "volumes": volumes(app)},
                         "status": {"phase": "Running",
                                    "conditions": [{"type": "Ready", "status": "False" if crash else "True"}],
                                    "containerStatuses": [{"name": app, "image": f"ghcr.io/acme/{app}:{tag}",
                                                           "ready": not crash, "restartCount": restarts,
                                                           "state": state, "lastState": last}]}})
            if crash:
                events.append(event("Pod", name, "BackOff", f"Back-off restarting failed container {app} in pod {name}",
                                    23 if variant == "c1" else 2, 58, count=20, etype="Warning", component="kubelet"))
                msg = (f"level=fatal msg=\"cannot connect to database\" err=\"dial tcp: lookup {BAD_HOST}: no such host\""
                       if variant == "c1" else
                       "level=fatal msg=\"failed to load /etc/flags/features.yaml: yaml: line 7: found character that "
                       "cannot start any token\"")
                streams.append({"stream": {"k8s_pod_name": name, "k8s_namespace_name": "shop", "k8s_container_name": app},
                                "values": [[ns_epoch((22 if variant == "c1" else 1) + t), msg] for t in (0, 6, 15)]})

    for ns, app, n, image in (("data", "orders-db", 1, "docker.io/library/postgres:15.4"),
                              ("kube-system", "coredns", 2, "registry.k8s.io/coredns/coredns:v1.11.3")):
        deployments.append({"apiVersion": "apps/v1", "kind": "Deployment",
                            "metadata": {"name": app, "namespace": ns, "uid": f"dep-{ns}-{app}"},
                            "spec": {"replicas": n, "selector": {"matchLabels": {"app.kubernetes.io/name": app}},
                                     "template": {"metadata": {"labels": {"app.kubernetes.io/name": app}},
                                                  "spec": {"containers": [{"name": app, "image": image}]}}},
                            "status": {"replicas": n, "readyReplicas": n, "availableReplicas": n,
                                       "conditions": [{"type": "Available", "status": "True"}]}})
        for i in range(n):
            name = f"{app}-{RS[app]}-{'q2w3e' if i == 0 else 'r4t5y'}"
            pods.append({"apiVersion": "v1", "kind": "Pod",
                         "metadata": {"name": name, "namespace": ns, "uid": f"pod-{ns}-{name}",
                                      "labels": {"app.kubernetes.io/name": app},
                                      "ownerReferences": [{"apiVersion": "apps/v1", "kind": "ReplicaSet",
                                                           "name": f"{app}-{RS[app]}", "controller": True}]},
                         "spec": {"containers": [{"name": app, "image": image}]},
                         "status": {"phase": "Running", "podIP": f"10.42.3.{20 + i}",
                                    "conditions": [{"type": "Ready", "status": "True"}],
                                    "containerStatuses": [{"name": app, "image": image, "ready": True, "restartCount": 0,
                                                           "state": {"running": {"startedAt": ts(-30000)}},
                                                           "lastState": {}}]}})

    services = [{"apiVersion": "v1", "kind": "Service",
                 "metadata": {"name": d["metadata"]["name"], "namespace": d["metadata"]["namespace"],
                              "uid": f"svc-{d['metadata']['namespace']}-{d['metadata']['name']}"},
                 "spec": {"selector": {"app.kubernetes.io/name": d["metadata"]["name"]},
                          "ports": [{"port": 5432 if d["metadata"]["name"] == "orders-db" else 80,
                                     "targetPort": 5432 if d["metadata"]["name"] == "orders-db" else 8080}]}}
                for d in deployments if d["metadata"]["name"] != "coredns"]

    env_changed = variant == "c1"
    flags_changed = variant == "c2"
    configmaps = [
        {"apiVersion": "v1", "kind": "ConfigMap",
         "metadata": {"name": "shop-env", "namespace": "shop", "uid": "cm-shop-shop-env",
                      "annotations": {"meta.helm.sh/release-name": "platform-config", "meta.helm.sh/release-namespace": "shop"},
                      "managedFields": [{"manager": "helm", "operation": "Update",
                                         "time": ts(0 if env_changed else -40000)}]},
         "data": {"DB_HOST": BAD_HOST if env_changed else GOOD_HOST, "DB_PORT": "5432", "REGION": "eu-west",
                  "LOG_LEVEL": "info"}},
        {"apiVersion": "v1", "kind": "ConfigMap",
         "metadata": {"name": "feature-flags", "namespace": "shop", "uid": "cm-shop-feature-flags",
                      "annotations": {"meta.helm.sh/release-name": "feature-flags", "meta.helm.sh/release-namespace": "shop"},
                      "managedFields": [{"manager": "helm", "operation": "Update",
                                         "time": ts(0 if flags_changed else -40000)}]},
         "data": {"features.yaml": FLAGS_BAD if flags_changed else FLAGS_OK}},
        {"apiVersion": "v1", "kind": "ConfigMap",
         "metadata": {"name": "search-config", "namespace": "shop", "uid": "cm-shop-search-config",
                      "managedFields": [{"manager": "kubectl-client-side-apply", "operation": "Update", "time": ts(-120)}]},
         "data": {"search.yaml": "max_results: 50\nrequest_timeout_ms: 900\n"}}]

    if variant == "c1":
        events += [event("Node", "worker-node-02", "NodeNotSchedulable", "Node worker-node-02 status is now: NodeNotSchedulable",
                         20, ns="default", component="kubelet"),
                   event("Node", "worker-node-02", "NodeSchedulable", "Node worker-node-02 status is now: NodeSchedulable",
                         35, ns="default", component="kubelet")]
    else:
        events.append(event("Node", "worker-node-02", "NodeNotSchedulable", "Node worker-node-02 status is now: NodeNotSchedulable",
                            -9000, ns="default", component="kubelet"))
    events.append(event("Deployment", "web", "ScalingReplicaSet", "Scaled up replica set web-9c8d7b6f5 to 2", -60,
                        component="deployment-controller"))

    alerts = [alert("CPUThrottlingHigh", "info", {"namespace": "shop",
                                                  "pod": [p for p in pods if p["metadata"]["name"].startswith("cart-api-")][0]["metadata"]["name"],
                                                  "container": "cart-api"},
                    "Processes experience elevated CPU throttling.", "26.0% throttling of CPU in namespace shop.", -500)]
    alerts += [alert("KubePodCrashLooping", "warning", {"namespace": "shop", "pod": p["metadata"]["name"],
                                                        "container": p["spec"]["containers"][0]["name"]},
                     "Pod is crash looping.",
                     f"Pod shop/{p['metadata']['name']} is in waiting state (reason: \"CrashLoopBackOff\").", 40)
               for p in pods if p["status"]["containerStatuses"][0]["state"].get("waiting")]

    release = "feature-flags" if variant == "c2" else "platform-config"
    if release == "platform-config":
        history = [{"revision": 3, "updated": ts(-40000), "status": "superseded" if env_changed else "deployed",
                    "chart": "platform-config-1.4.0", "description": "Upgrade complete"}]
        if env_changed:
            history.append({"revision": 4, "updated": ts(0), "status": "deployed", "chart": "platform-config-1.5.0",
                            "description": "Upgrade complete"})
        applied = [configmaps[0]]
        values = {"dbHost": BAD_HOST if env_changed else GOOD_HOST, "region": "eu-west"}
    else:
        history = [{"revision": 9, "updated": ts(-40000), "status": "superseded", "chart": "feature-flags-2.1.0",
                    "description": "Upgrade complete"},
                   {"revision": 10, "updated": ts(0), "status": "deployed", "chart": "feature-flags-2.2.0",
                    "description": "Upgrade complete"}]
        applied = [configmaps[1]]
        values = {"flagsFile": "features.yaml"}
    applied = [{k: v for k, v in cm.items()} | {"metadata": {k: v for k, v in cm["metadata"].items() if k != "managedFields"}}
               for cm in applied]

    files = {"kube/namespaces.yaml": as_list("Namespace", namespaces),
             "kube/deployments.yaml": as_list("Deployment", deployments),
             "kube/replicasets.yaml": as_list("ReplicaSet", rss),
             "kube/pods.yaml": as_list("Pod", pods),
             "kube/services.yaml": as_list("Service", services),
             "kube/configmaps.yaml": as_list("ConfigMap", configmaps),
             "kube/events.yaml": as_list("Event", sorted(events, key=lambda e: e["firstTimestamp"])),
             "helm/values.yaml": values, "helm/release.json": values, "helm/history.json": history,
             "loki/streams.json": streams, "prometheus/alerts.json": alerts}
    write(CASE[variant], files, {**COMMON, "release": release, **EXPECT[variant]})
    (OUT / CASE[variant] / "helm" / "manifest.yaml").write_text(
        "---\n".join(yaml.safe_dump(d, sort_keys=False, width=100000) for d in applied))
    if variant == "c2":
        previous = dict(applied[0])
        previous["data"] = {"features.yaml": FLAGS_PREVIOUS}
        (OUT / CASE[variant] / "helm" / "manifest-previous.yaml").write_text(
            yaml.safe_dump(previous, sort_keys=False, width=100000))


CASE = {"c1": "c1_configmap_env_changed", "c2": "c2_configmap_invalid_file", "c0": "c0_configmap_no_incident"}
SOURCES = ["https://kubernetes.io/docs/concepts/configuration/configmap/",
           "prometheus-operator/runbooks@a685d14 KubePodCrashLooping"]
COMMON = {"set": "S2", "family": "configmap", "namespace": "shop", "sources": SOURCES,
          "spec": "docs/s2/family-configmap.md"}
EXPECT = {
    "c1": {"scenario": "pods of two releases crash on an unknown database host; a shared ConfigMap changed, read at start",
           "query": "why do cart-api and checkout crash in shop", "expected_outcome": "cause",
           "cause_object": "ConfigMap/shop/shop-env",
           "mechanism": "its DB_HOST was changed at T0 to orders-db-v2, which does not exist; pods read it when they start, "
                        "so only those recreated since T0 by a node drain fail, while older pods keep the old value",
           "pass_if": "names shop-env's DB_HOST change",
           "partial_if_only": ["the database host cannot be resolved, without the ConfigMap"],
           "chain": ["new pods of cart-api and checkout crash: lookup orders-db-v2... no such host",
                     "their containers take their environment from ConfigMap shop-env (envFrom)",
                     "shop-env's DB_HOST is orders-db-v2, set by the platform-config release at T0",
                     "only orders-db exists in data; the crashing pods started after T0, the others before"],
           "decoys": [{"object": "Node/worker-node-02 (drain)", "why_not": "recreated the pods, does not explain the crash"},
                      {"object": "Deployment/shop/orders-api", "why_not": "reads the same ConfigMap; its pods predate T0"},
                      {"object": "ConfigMap/shop/search-config", "why_not": "changed two hours before, its consumer runs"}],
           "affected_resources": ["Deployment/shop/cart-api", "Deployment/shop/checkout"],
           "root_cause_concepts": ["shop-env|configmap|config map", "db_host|orders-db-v2|host"]},
    "c2": {"scenario": "two services crash-loop on an invalid YAML file; the shared ConfigMap they mount was replaced",
           "query": "why are search-api and web crash-looping in shop", "expected_outcome": "cause",
           "cause_object": "ConfigMap/shop/feature-flags",
           "mechanism": "its features.yaml was replaced at T0 by invalid YAML (a tab on line 7); search-api and web, which "
                        "mount it, reload it, exit, and fail again on every start",
           "pass_if": "names the feature-flags ConfigMap's invalid content",
           "partial_if_only": ["a configuration file fails to parse, without the ConfigMap"],
           "chain": ["search-api and web crash-loop: failed to load /etc/flags/features.yaml, yaml: line 7",
                     "both mount ConfigMap feature-flags at /etc/flags; cart-api does not",
                     "feature-flags was updated at T0 by its release, revision 10",
                     "its features.yaml has a tab on line 7"],
           "decoys": [{"object": "Deployment/shop/web (redeployed)", "why_not": "an hour before T0; its pods ran until T0"},
                      {"object": "Pod/shop/cart-api (CPUThrottlingHigh)", "why_not": "unrelated"},
                      {"object": "feature-flags new-checkout rollout 10 to 25", "why_not": "a valid change in the same release"}],
           "affected_resources": ["Deployment/shop/search-api", "Deployment/shop/web"],
           "root_cause_concepts": ["feature-flags|configmap|config map", "yaml|invalid|line 7|tab|parse"]},
    "c0": {"scenario": "No incident: the shared ConfigMaps are valid, every pod runs",
           "query": "is something wrong in shop", "expected_outcome": "no_incident", "cause_object": None,
           "decoys": [{"object": "Node/worker-node-02 (old drain)", "why_not": "long ago"},
                      {"object": "ConfigMap/shop/search-config", "why_not": "an old, valid change"},
                      {"object": "Deployment/shop/web (redeployed)", "why_not": "rolled out, Ready"}],
           "affected_resources": [], "root_cause_contains": [],
           "notes": "Passes only on an explicit no-incident answer (protocol 002, A2)."},
}

if __name__ == "__main__":
    for v in ("c1", "c2", "c0"):
        build(v)
    print("written:", ", ".join(sorted(CASE.values())))
