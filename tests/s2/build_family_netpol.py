"""Builds S2's NetworkPolicy family (docs/s2/family-netpol.md): n1, n2 and their negative n0.

Written 2026-11-06 from the family spec, before any arm ran on these cases. Frozen by hash before
the first run (docs/s2-correlation-cases.md). Nothing in a fixture says which object is the cause.

    python tests/s2/build_family_netpol.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_family_quota import OUT, alert, as_list, event, ns_epoch, ts, write  # noqa: E402

NODES = ["worker-node-01", "worker-node-02", "worker-node-03"]
SVC_IP = {"payments": "10.43.12.9", "orders-db": "10.43.20.5", "checkout-api": "10.43.3.11",
          "orders-api": "10.43.3.14", "cart-api": "10.43.3.17", "web": "10.43.3.20", "coredns": "10.43.0.10"}

# namespace, app, release, chart version, image tag, replicas, port
APPS = [("shop", "checkout-api", "checkout-api", "1.9.2", "1.9.2", 3, 8080),
        ("shop", "orders-api", "orders-api", "3.0.0", "3.0.0", 2, 8080),
        ("shop", "cart-api", "cart-api", "2.3.1", "2.3.1", 2, 8080),
        ("shop", "web", "web", "4.2.0", "4.2.0", 2, 8080),
        ("pay", "payments", "payments", "5.4.1", "5.4.1", 3, 8443),
        ("data", "orders-db", "orders-db", "15.4.0", "15.4", 1, 5432),
        ("kube-system", "coredns", "coredns", "1.11.3", "1.11.3", 2, 53)]


def pod_labels(app: str, variant: str) -> dict:
    std = {"app.kubernetes.io/name": app, "app.kubernetes.io/instance": app}
    if app == "orders-api":
        if variant == "n2":
            return std                                  # chart 3.0: the standard labels only
        return {**std, "app": "orders-api"}             # n0: kept for compatibility; n1: the 2.x labels too
    return std


def deployment(ns: str, app: str, release: str, tag: str, replicas: int, ready: int, port: int,
               variant: str, conditions: list | None = None) -> dict:
    lbl = pod_labels(app, variant)
    return {"apiVersion": "apps/v1", "kind": "Deployment",
            "metadata": {"name": app, "namespace": ns, "uid": f"dep-{ns}-{app}",
                         "labels": {"app.kubernetes.io/name": app, "app.kubernetes.io/managed-by": "Helm"},
                         "annotations": {"meta.helm.sh/release-name": release, "meta.helm.sh/release-namespace": ns}},
            "spec": {"replicas": replicas, "selector": {"matchLabels": {"app.kubernetes.io/name": app}},
                     "template": {"metadata": {"labels": lbl},
                                  "spec": {"containers": [{"name": app, "image": f"ghcr.io/acme/{app}:{tag}",
                                                           "ports": [{"containerPort": port}],
                                                           "resources": {"requests": {"cpu": "200m", "memory": "256Mi"},
                                                                         "limits": {"cpu": "1", "memory": "512Mi"}}}]}}},
            "status": {"replicas": replicas, "readyReplicas": ready, "availableReplicas": ready, "updatedReplicas": replicas,
                       "conditions": conditions or [{"type": "Available", "status": "True" if ready else "False"}]}}


def pod(ns: str, app: str, rs: str, i: int, tag: str, variant: str, ready: bool = True, crash: bool = False,
        started: float = -4000, restarts: int = 0, last: dict | None = None) -> dict:
    name = f"{app}-{rs}-{(i * 7919 + len(app) * 104729) % 1048576:05x}"
    state = {"running": {"startedAt": ts(started)}}
    if crash:
        state = {"waiting": {"reason": "CrashLoopBackOff",
                             "message": f"back-off 2m40s restarting failed container={app} pod={name}"}}
        last = {"terminated": {"reason": "Error", "exitCode": 1, "startedAt": ts(25), "finishedAt": ts(26)}}
    return {"apiVersion": "v1", "kind": "Pod",
            "metadata": {"name": name, "namespace": ns, "uid": f"pod-{ns}-{name}",
                         "labels": {**pod_labels(app, variant), "pod-template-hash": rs},
                         "ownerReferences": [{"apiVersion": "apps/v1", "kind": "ReplicaSet", "name": f"{app}-{rs}",
                                              "controller": True}]},
            "spec": {"nodeName": NODES[i % 3],
                     "containers": [{"name": app, "image": f"ghcr.io/acme/{app}:{tag}",
                                     "resources": {"requests": {"cpu": "200m", "memory": "256Mi"},
                                                   "limits": {"cpu": "1", "memory": "512Mi"}}}]},
            "status": {"phase": "Running", "podIP": f"10.42.{i + 1}.{20 + len(app)}",
                       "conditions": [{"type": "Ready", "status": "True" if ready and not crash else "False"}],
                       "containerStatuses": [{"name": app, "image": f"ghcr.io/acme/{app}:{tag}",
                                              "ready": ready and not crash, "restartCount": 6 if crash else restarts,
                                              "state": state, "lastState": last or {}}]}}


def netpol(ns: str, name: str, spec: dict) -> dict:
    return {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
            "metadata": {"name": name, "namespace": ns, "uid": f"np-{ns}-{name}"}, "spec": spec}


def build(variant: str) -> None:
    shop_team = "shop" if variant == "n1" else "storefront"
    namespaces = [{"apiVersion": "v1", "kind": "Namespace",
                   "metadata": {"name": n, "uid": f"ns-{n}",
                                "labels": {"kubernetes.io/metadata.name": n, **lbl}}}
                  for n, lbl in (("shop", {"team": shop_team}), ("pay", {"team": "payments"}),
                                 ("data", {"team": "data"}), ("kube-system", {}))]
    if variant == "n1":
        namespaces[0]["metadata"]["annotations"] = {"platform.acme.io/owner-updated": ts(0)}

    policies = [
        netpol("pay", "default-deny-ingress", {"podSelector": {}, "policyTypes": ["Ingress"]}),
        netpol("pay", "allow-from-storefront", {
            "podSelector": {"matchLabels": {"app.kubernetes.io/name": "payments"}}, "policyTypes": ["Ingress"],
            "ingress": [{"from": [{"namespaceSelector": {"matchLabels": {"team": "storefront"}}}],
                         "ports": [{"protocol": "TCP", "port": 8443}]}]}),
        netpol("data", "default-deny-ingress", {"podSelector": {}, "policyTypes": ["Ingress"]}),
        netpol("data", "allow-orders-to-db", {
            "podSelector": {"matchLabels": {"app.kubernetes.io/name": "orders-db"}}, "policyTypes": ["Ingress"],
            "ingress": [{"from": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "shop"}},
                                   "podSelector": {"matchLabels": {"app": "orders-api"}}}],
                         "ports": [{"protocol": "TCP", "port": 5432}]}]}),
        netpol("shop", "allow-egress-all", {"podSelector": {}, "policyTypes": ["Egress"], "egress": [{}]}),
    ]

    deployments, pods, events = [], [], []
    rs = {"checkout-api": "6d9f7c8b5", "orders-api": "7f8c9d6b4" if variant == "n2" else "5b7d8c9f6",
          "cart-api": "5d6f8c7b9", "web": "8c7b6d5f9", "payments": "9d8c7f6b5", "orders-db": "6c7d8b9f4",
          "coredns": "7db6d8ff4"}
    for k, (ns, app, rel, chart, tag, n, port) in enumerate(APPS):
        if app == "orders-api" and variant != "n2":
            tag = "2.4.1"
        broken = (variant == "n1" and app == "checkout-api") or (variant == "n2" and app == "orders-api")
        conditions = None
        if broken:
            conditions = [{"type": "Available", "status": "False", "reason": "MinimumReplicasUnavailable"}]
        deployments.append(deployment(ns, app, rel, tag, n, 0 if broken else n, port, variant, conditions))
        for i in range(n):
            restarts, last = 0, None
            if app == "coredns" and i == 0:
                restarts, last = 1, {"terminated": {"reason": "Error", "exitCode": 1,
                                                     "startedAt": ts(-9000), "finishedAt": ts(-1560)}}
            started = -4000
            if app == "payments" and variant != "n0":
                started = 5 + i
            if app == "orders-api":
                started = 0 if variant == "n2" else -40000
            pods.append(pod(ns, app, rs[app], i, tag, variant, ready=not (variant == "n1" and app == "checkout-api"),
                            crash=variant == "n2" and app == "orders-api", started=started,
                            restarts=restarts, last=last))

    services = [{"apiVersion": "v1", "kind": "Service",
                 "metadata": {"name": app, "namespace": ns, "uid": f"svc-{ns}-{app}"},
                 "spec": {"clusterIP": SVC_IP[app], "selector": {"app.kubernetes.io/name": app},
                          "ports": [{"port": port, "targetPort": port}]}}
                for ns, app, _, _, _, _, port in APPS]
    endpoints = [{"apiVersion": "v1", "kind": "Endpoints", "metadata": {"name": app, "namespace": ns},
                  "subsets": [{("notReadyAddresses" if not p["status"]["conditions"][0]["status"] == "True" else "addresses"):
                               [{"ip": p["status"]["podIP"], "targetRef": {"kind": "Pod", "name": p["metadata"]["name"]}}],
                               "ports": [{"port": port}]}
                              for p in pods if p["metadata"]["namespace"] == ns and p["metadata"]["name"].startswith(app + "-")]}
                 for ns, app, _, _, _, _, port in APPS]

    # events
    events.append(event("Pod", pods[-2]["metadata"]["name"], "Started", "Started container coredns", -1560,
                        ns="kube-system", component="kubelet"))
    if variant != "n0":
        events.append(event("Deployment", "payments", "ScalingReplicaSet",
                            "Scaled up replica set payments-9d8c7f6b5 to 3", 5, ns="pay", component="deployment-controller"))
    if variant == "n0":
        events.append(event("Deployment", "payments", "ScalingReplicaSet",
                            "Scaled up replica set payments-9d8c7f6b5 to 3", -20, ns="pay", component="deployment-controller"))
    if variant == "n1":
        for p in pods:
            if p["metadata"]["name"].startswith("checkout-api-"):
                events.append(event("Pod", p["metadata"]["name"], "Unhealthy",
                                    "Readiness probe failed: HTTP probe failed with statuscode: 503", 1, 30,
                                    count=180, etype="Warning", component="kubelet"))
    if variant == "n2":
        events.append(event("Deployment", "orders-api", "ScalingReplicaSet",
                            "Scaled up replica set orders-api-7f8c9d6b4 to 2", 0, component="deployment-controller"))
        for p in pods:
            if p["metadata"]["name"].startswith("orders-api-"):
                events.append(event("Pod", p["metadata"]["name"], "BackOff", "Back-off restarting failed container "
                                    "orders-api in pod " + p["metadata"]["name"], 2, 30, count=24, etype="Warning",
                                    component="kubelet"))

    # logs
    streams = []
    if variant == "n1":
        for p in [q for q in pods if q["metadata"]["name"].startswith("checkout-api-")][:2]:
            streams.append({"stream": {"k8s_pod_name": p["metadata"]["name"], "k8s_namespace_name": "shop",
                                       "k8s_container_name": "checkout-api"},
                            "values": [[ns_epoch(1 + t), f"level=error msg=\"payment authorization failed\" "
                                        f"upstream=payments.pay.svc.cluster.local:8443 err=\"dial tcp "
                                        f"{SVC_IP['payments']}:8443: i/o timeout\""] for t in (0, 4, 9)]})
    if variant == "n2":
        for p in [q for q in pods if q["metadata"]["name"].startswith("orders-api-")]:
            streams.append({"stream": {"k8s_pod_name": p["metadata"]["name"], "k8s_namespace_name": "shop",
                                       "k8s_container_name": "orders-api"},
                            "values": [[ns_epoch(2 + t), f"level=fatal msg=\"cannot connect to database\" "
                                        f"host=orders-db.data.svc.cluster.local err=\"dial tcp {SVC_IP['orders-db']}:5432: "
                                        f"i/o timeout\""] for t in (0, 3, 8)]})
    db = [q for q in pods if q["metadata"]["name"].startswith("orders-db-")][0]
    streams.append({"stream": {"k8s_pod_name": db["metadata"]["name"], "k8s_namespace_name": "data",
                               "k8s_container_name": "orders-db"},
                    "values": [[ns_epoch(-185 + t), "FATAL:  sorry, too many clients already"] for t in (0, 1, 3)]})

    alerts = []
    if variant == "n1":
        alerts = [alert("KubeDeploymentReplicasMismatch", "warning", {"namespace": "shop", "deployment": "checkout-api"},
                        "Deployment has not matched the expected number of replicas.",
                        "Deployment shop/checkout-api has not matched the expected number of replicas for longer than 15 minutes.", 16)]
    if variant == "n2":
        alerts = [alert("KubePodCrashLooping", "warning", {"namespace": "shop", "pod": p["metadata"]["name"], "container": "orders-api"},
                        "Pod is crash looping.", f"Pod shop/{p['metadata']['name']} (orders-api) is in waiting state (reason: \"CrashLoopBackOff\").", 17)
                  for p in pods if p["metadata"]["name"].startswith("orders-api-")]

    release = "checkout-api" if variant == "n1" else "orders-api"
    rel_app = [a for a in APPS if a[1] == release][0]
    ns, app, _, _, tag, n, port = rel_app
    if app == "orders-api" and variant != "n2":
        tag = "2.4.1"
    values = {"image": {"repository": f"ghcr.io/acme/{app}", "tag": tag}, "replicaCount": n,
              "resources": {"requests": {"cpu": "200m", "memory": "256Mi"}, "limits": {"cpu": "1", "memory": "512Mi"}}}
    applied = deployment(ns, app, app, tag, n, n, port, variant)
    applied.pop("status")
    history = [{"revision": 7, "updated": ts(-43200), "status": "superseded", "chart": f"{app}-2.4.1",
                "app_version": "2.4.1", "description": "Upgrade complete"}]
    if variant == "n2":
        history.append({"revision": 8, "updated": ts(0), "status": "deployed", "chart": f"{app}-3.0.0",
                        "app_version": "3.0.0", "description": "Upgrade complete"})
    else:
        history[0]["status"] = "deployed"

    files = {"kube/namespaces.yaml": as_list("Namespace", namespaces),
             "kube/networkpolicies.yaml": as_list("NetworkPolicy", policies),
             "kube/deployments.yaml": as_list("Deployment", deployments),
             "kube/pods.yaml": as_list("Pod", pods),
             "kube/services.yaml": as_list("Service", services),
             "kube/endpoints.yaml": as_list("Endpoints", endpoints),
             "kube/events.yaml": as_list("Event", sorted(events, key=lambda e: e["firstTimestamp"])),
             "helm/values.yaml": values, "helm/release.json": values, "helm/history.json": history,
             "loki/streams.json": streams, "prometheus/alerts.json": alerts}
    write(CASE[variant], files, {**COMMON, "release": release, **EXPECT[variant]})
    (OUT / CASE[variant] / "helm" / "manifest.yaml").write_text(yaml.safe_dump(applied, sort_keys=False, width=120))


CASE = {"n1": "n1_netpol_namespace_relabel", "n2": "n2_netpol_pod_labels_changed", "n0": "n0_netpol_no_incident"}
SOURCES = ["https://kubernetes.io/docs/concepts/services-networking/network-policies/",
           "prometheus-operator/runbooks@a685d14 KubePodNotReady",
           "prometheus-operator/runbooks@a685d14 KubePodCrashLooping"]
COMMON = {"set": "S2", "family": "netpol", "namespace": "shop", "sources": SOURCES, "spec": "docs/s2/family-netpol.md"}
DECOYS = [{"object": "Pod/kube-system/coredns (old restart)", "why_not": "resolved 26 h before; DNS resolves in the logs"},
          {"object": "NetworkPolicy/shop/allow-egress-all", "why_not": "allows every egress, so it blocks nothing"}]
EXPECT = {
    "n1": {"scenario": "checkout-api's calls to payments time out; a namespace relabel stopped matching an allow rule",
           "query": "why are checkout-api's payments failing in shop", "expected_outcome": "cause",
           "cause_object": "Namespace/shop",
           "mechanism": "Namespace shop's team label changed from storefront to shop, so NetworkPolicy "
                        "pay/allow-from-storefront no longer matches it and pay/default-deny-ingress drops "
                        "checkout-api's calls to payments",
           "pass_if": "names the label mismatch, through the Namespace or the policy",
           "partial_if_only": ["a NetworkPolicy without the label mismatch"],
           "chain": ["checkout-api's calls to payments.pay time out; its readiness fails",
                     "Service/pay/payments has ready endpoints: payments is healthy",
                     "NetworkPolicy/pay/default-deny-ingress isolates pay; allow-from-storefront allows team=storefront",
                     "Namespace/shop was relabelled team=shop at T0"],
           "decoys": [{"object": "Deployment/pay/payments", "why_not": "rolled out at T0+5 min, after the first timeouts; healthy"},
                      *DECOYS],
           "affected_resources": ["Deployment/shop/checkout-api"],
           "root_cause_concepts": ["label|team", "networkpolic|network policy|allow-from-storefront"]},
    "n2": {"scenario": "orders-api cannot reach its database; a chart upgrade changed the pod labels a policy selects",
           "query": "why does orders-api keep restarting in shop", "expected_outcome": "cause",
           "cause_object": "Deployment/shop/orders-api",
           "mechanism": "the orders-api chart upgrade to 3.0.0 dropped the app=orders-api pod label, so NetworkPolicy "
                        "data/allow-orders-to-db no longer selects its pods and data/default-deny-ingress drops their "
                        "connections to orders-db",
           "pass_if": "names the pod label change against the policy's podSelector",
           "partial_if_only": ["the upgrade without the label", "the policy without the label"],
           "chain": ["orders-api crash-loops: dial tcp to orders-db:5432 times out",
                     "Service/data/orders-db has ready endpoints: the database is up",
                     "NetworkPolicy/data/default-deny-ingress isolates data; allow-orders-to-db allows pods app=orders-api",
                     "the chart upgrade at T0 left orders-api's pods without app=orders-api"],
           "decoys": [{"object": "Pod/data/orders-db (too many clients)", "why_not": "logged 3 h before the upgrade, then stopped"},
                      {"object": "Deployment/shop/orders-api (new image tag)", "why_not": "expected with the chart upgrade"},
                      *DECOYS],
           "affected_resources": ["Deployment/shop/orders-api"],
           "root_cause_concepts": ["label", "networkpolic|network policy|allow-orders-to-db|podselector"]},
    "n0": {"scenario": "No incident: the same objects as n1 and n2, labels that match",
           "query": "is something wrong in shop", "expected_outcome": "no_incident", "cause_object": None,
           "decoys": [{"object": "Deployment/pay/payments", "why_not": "rolled out 20 min ago, healthy"},
                      {"object": "Pod/data/orders-db (too many clients)", "why_not": "old, resolved"}, *DECOYS],
           "affected_resources": [], "root_cause_contains": [],
           "notes": "Passes only on an explicit no-incident answer (protocol 002, A2)."},
}

if __name__ == "__main__":
    for v in ("n1", "n2", "n0"):
        build(v)
    print("written:", ", ".join(sorted(CASE.values())))
