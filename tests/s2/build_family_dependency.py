"""Builds S2's cross-namespace dependency family (docs/s2/family-dependency.md): d1, d2 and their negative d0.

Written 2026-11-06 from the family spec, before any arm ran on these cases. Frozen by hash before
the first run (docs/s2-correlation-cases.md). Nothing in a fixture says which object is the cause.

    python tests/s2/build_family_dependency.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_family_quota import OUT, alert, as_list, event, ns_epoch, ts, write  # noqa: E402

NODES = ["worker-node-01", "worker-node-02", "worker-node-03"]
# namespace, app, release, image tag, replicas, container port, service port
APPS = [("shop", "checkout-api", "checkout-api", "3.1.4", 3, 8080, 80),
        ("shop", "cart-api", "cart-api", "2.3.1", 2, 8080, 80),
        ("shop", "web", "web", "4.2.0", 2, 8080, 80),
        ("platform", "inventory", "inventory", "2.0.0", 2, 9090, 80),
        ("platform", "pricing", "pricing", "1.7.0", 3, 8080, 80),
        ("platform", "catalog", "catalog", "5.0.3", 2, 8080, 80),
        ("cache", "redis", "redis", "7.2.5", 1, 6379, 6379),
        ("monitoring", "prometheus-server", "prometheus", "2.53.0", 1, 9090, 9090)]
SVC_IP = {"checkout-api": "10.43.3.11", "cart-api": "10.43.3.17", "web": "10.43.3.20", "inventory": "10.43.40.12",
          "pricing": "10.43.40.15", "catalog": "10.43.40.18", "redis": "10.43.50.7", "prometheus-server": "10.43.60.2"}
RS = {"checkout-api": "6d9f7c8b5", "cart-api": "5d6f8c7b9", "web": "8c7b6d5f9", "inventory": "7b9c8d6f4",
      "pricing": "59d8c7b6f", "catalog": "6f7b8c9d5", "redis": "84c9d7b6f", "prometheus-server": "5b6c7d8e9"}


def container(app: str, tag: str, port: int, variant: str) -> dict:
    c = {"name": app, "image": f"ghcr.io/acme/{app}:{tag}" if app != "redis" else f"docker.io/library/redis:{tag}",
         "ports": [{"containerPort": port, "name": "http" if app != "redis" else "redis"}],
         "resources": {"requests": {"cpu": "200m", "memory": "256Mi"}, "limits": {"cpu": "1", "memory": "768Mi"}}}
    if app == "redis":
        c["args"] = ["--maxmemory", "512mb", "--maxmemory-policy", "noeviction", "--appendonly", "no"]
    else:
        c["readinessProbe"] = {"httpGet": {"path": "/ready", "port": port}, "periodSeconds": 10}
    return c


def port_of(app: str, variant: str) -> int:
    p = [a for a in APPS if a[1] == app][0][5]
    return p


def tag_of(app: str, variant: str) -> str:
    if app == "inventory" and variant == "d0":
        return "2.0.0"
    if app == "pricing" and variant == "d2":
        return "1.8.0"
    return [a for a in APPS if a[1] == app][0][3]


def build(variant: str) -> None:
    now = 200 if variant == "d2" else 30
    namespaces = [{"apiVersion": "v1", "kind": "Namespace",
                   "metadata": {"name": n, "uid": f"ns-{n}", "labels": {"kubernetes.io/metadata.name": n}}}
                  for n in ("shop", "platform", "cache", "monitoring")]
    deployments, pods, events = [], [], []
    for k, (ns, app, rel, _, n, _, _) in enumerate(APPS):
        if app == "pricing" and variant == "d2":
            n = 5
        tag, port = tag_of(app, variant), port_of(app, variant)
        deployments.append({
            "apiVersion": "apps/v1", "kind": "Deployment",
            "metadata": {"name": app, "namespace": ns, "uid": f"dep-{ns}-{app}",
                         "labels": {"app.kubernetes.io/name": app, "app.kubernetes.io/managed-by": "Helm"},
                         "annotations": {"meta.helm.sh/release-name": rel, "meta.helm.sh/release-namespace": ns}},
            "spec": {"replicas": n, "selector": {"matchLabels": {"app.kubernetes.io/name": app}},
                     "template": {"metadata": {"labels": {"app.kubernetes.io/name": app}},
                                  "spec": {"containers": [container(app, tag, port, variant)]}}},
            "status": {"replicas": n, "readyReplicas": n, "availableReplicas": n, "updatedReplicas": n,
                       "conditions": [{"type": "Available", "status": "True"}]}})
        for i in range(n):
            name = f"{app}-{RS[app]}-{(i * 7919 + len(app) * 104729) % 1048576:05x}"
            started = -4000
            if app == "inventory" and variant in ("d1", "d0"):
                started = 0 if variant == "d1" else -2000
            if app == "pricing" and variant == "d2":
                started = 0 if i < 3 else 190
            pods.append({
                "apiVersion": "v1", "kind": "Pod",
                "metadata": {"name": name, "namespace": ns, "uid": f"pod-{ns}-{name}",
                             "labels": {"app.kubernetes.io/name": app, "pod-template-hash": RS[app]},
                             "ownerReferences": [{"apiVersion": "apps/v1", "kind": "ReplicaSet",
                                                  "name": f"{app}-{RS[app]}", "controller": True}]},
                "spec": {"nodeName": NODES[(i + k) % 3], "containers": [container(app, tag, port, variant)]},
                "status": {"phase": "Running", "podIP": f"10.42.{(i + k) % 3 + 1}.{30 + 3 * k + i}",
                           "conditions": [{"type": "Ready", "status": "True"}],
                           "containerStatuses": [{"name": app, "image": container(app, tag, port, variant)["image"],
                                                  "ready": True, "restartCount": 0,
                                                  "state": {"running": {"startedAt": ts(started)}}, "lastState": {}}]}})

    def svc_target(app: str) -> int:
        if app == "inventory":
            return 8080 if variant == "d1" else 9090
        return port_of(app, variant)

    services, endpoints = [], []
    for ns, app, rel, _, _, _, sport in APPS:
        owner = "platform-services" if app == "inventory" else rel
        services.append({"apiVersion": "v1", "kind": "Service",
                         "metadata": {"name": app, "namespace": ns, "uid": f"svc-{ns}-{app}",
                                      "annotations": {"meta.helm.sh/release-name": owner,
                                                      "meta.helm.sh/release-namespace": ns}},
                         "spec": {"clusterIP": SVC_IP[app], "selector": {"app.kubernetes.io/name": app},
                                  "ports": [{"port": sport, "targetPort": svc_target(app), "protocol": "TCP"}]}})
        endpoints.append({"apiVersion": "v1", "kind": "Endpoints", "metadata": {"name": app, "namespace": ns},
                          "subsets": [{"addresses": [{"ip": p["status"]["podIP"],
                                                      "targetRef": {"kind": "Pod", "name": p["metadata"]["name"]}}
                                                     for p in pods if p["metadata"]["namespace"] == ns
                                                     and p["metadata"]["name"].startswith(app + "-")],
                                       "ports": [{"port": svc_target(app)}]}]})

    certificate = {"apiVersion": "cert-manager.io/v1", "kind": "Certificate",
                   "metadata": {"name": "shop-tls", "namespace": "shop", "uid": "cert-shop-tls"},
                   "spec": {"secretName": "shop-tls", "dnsNames": ["shop.acme.example"],
                            "issuerRef": {"name": "letsencrypt", "kind": "ClusterIssuer"}},
                   "status": {"notAfter": ts(now + 12 * 1440), "renewalTime": ts(now + 2 * 1440),
                              "conditions": [{"type": "Ready", "status": "True"}]}}

    inv = [p for p in pods if p["metadata"]["name"].startswith("inventory-")]
    blip = 0 if variant == "d1" else -2000
    events.append(event("Pod", inv[0]["metadata"]["name"], "Unhealthy",
                        "Readiness probe failed: Get \"http://%s:9090/ready\": dial tcp %s:9090: connect: connection refused"
                        % (inv[0]["status"]["podIP"], inv[0]["status"]["podIP"]),
                        blip, ns="platform", etype="Warning", component="kubelet"))
    if variant == "d1":
        events.append(event("Deployment", "inventory", "ScalingReplicaSet",
                            "Scaled up replica set inventory-7b9c8d6f4 to 2", 0, ns="platform",
                            component="deployment-controller"))
    if variant == "d2":
        events.append(event("Deployment", "pricing", "ScalingReplicaSet",
                            "Scaled up replica set pricing-59d8c7b6f to 3", 0, ns="platform",
                            component="deployment-controller"))
        events.append(event("Deployment", "pricing", "ScalingReplicaSet",
                            "Scaled up replica set pricing-59d8c7b6f to 5 from 3", 190, ns="platform",
                            component="deployment-controller"))

    checkout = [p for p in pods if p["metadata"]["name"].startswith("checkout-api-")]
    pricing = [p for p in pods if p["metadata"]["name"].startswith("pricing-")]
    streams, traces = [], []
    if variant == "d1":
        for p in checkout[:2]:
            streams.append({"stream": {"k8s_pod_name": p["metadata"]["name"], "k8s_namespace_name": "shop",
                                       "k8s_container_name": "checkout-api"},
                            "values": [[ns_epoch(1 + t), "level=error msg=\"reserve stock failed\" "
                                        "upstream=inventory.platform.svc.cluster.local:80 "
                                        f"err=\"dial tcp {SVC_IP['inventory']}:80: connect: connection refused\""]
                                       for t in (0, 6, 14)]})
    if variant == "d2":
        for p in checkout[:2]:
            streams.append({"stream": {"k8s_pod_name": p["metadata"]["name"], "k8s_namespace_name": "shop",
                                       "k8s_container_name": "checkout-api"},
                            "values": [[ns_epoch(181 + t), "level=error msg=\"price lookup failed\" "
                                        "upstream=pricing.platform.svc.cluster.local status=503"] for t in (0, 5, 12)]})
        for p in pricing[:2]:
            streams.append({"stream": {"k8s_pod_name": p["metadata"]["name"], "k8s_namespace_name": "platform",
                                       "k8s_container_name": "pricing"},
                            "values": [[ns_epoch(180 + t), "level=error msg=\"cache write failed\" "
                                        "addr=redis.cache.svc.cluster.local:6379 "
                                        "err=\"OOM command not allowed when used memory > 'maxmemory'.\""]
                                       for t in (1, 4, 9)]})
        for j in range(3):
            traces.append({"trace_id": f"{0x6a1f00 + j:032x}", "service_name": "checkout-api", "status": "ERROR",
                           "duration_ms": 212 + j, "span_count": 4, "root_span": "POST /checkout",
                           "error_message": "pricing returned 503", "started_at": ts(182 + 5 * j),
                           "error_spans": [{"name": "POST /checkout", "duration_ms": 212 + j, "status": "ERROR"},
                                           {"name": "GET pricing.platform/v1/price", "duration_ms": 31, "status": "ERROR",
                                            "attributes": {"http.status_code": 503,
                                                           "server.address": "pricing.platform.svc.cluster.local"}}]})
            traces.append({"trace_id": f"{0x7b2e00 + j:032x}", "service_name": "pricing", "status": "ERROR",
                           "duration_ms": 29 + j, "span_count": 3, "root_span": "GET /v1/price",
                           "error_message": "OOM command not allowed when used memory > 'maxmemory'.",
                           "started_at": ts(182 + 5 * j),
                           "error_spans": [{"name": "GET /v1/price", "duration_ms": 29 + j, "status": "ERROR"},
                                           {"name": "SET", "duration_ms": 2, "status": "ERROR",
                                            "attributes": {"db.system": "redis",
                                                           "server.address": "redis.cache.svc.cluster.local"}}]})

    alerts = [alert("CPUThrottlingHigh", "info", {"namespace": "shop", "pod": checkout[0]["metadata"]["name"],
                                                  "container": "checkout-api"},
                    "Processes experience elevated CPU throttling.",
                    "27.4% throttling of CPU in namespace shop for container checkout-api.", -600)]
    if variant == "d1":
        alerts.append(alert("HighErrorRate", "critical", {"namespace": "shop", "deployment": "checkout-api"},
                            "checkout-api error rate above 5%", "42% of POST /checkout/reserve requests fail.", 3))
    if variant == "d2":
        alerts += [alert("HighErrorRate", "critical", {"namespace": "shop", "deployment": "checkout-api"},
                         "checkout-api error rate above 5%", "38% of POST /checkout requests fail.", 183),
                   alert("HighLatencyP95", "warning", {"namespace": "shop", "deployment": "checkout-api"},
                         "checkout-api p95 latency above 300ms", "p95 latency is 410ms.", 184),
                   alert("HighErrorRate", "critical", {"namespace": "platform", "deployment": "pricing"},
                         "pricing error rate above 5%", "47% of GET /v1/price requests return 503.", 182)]

    release = "inventory" if variant in ("d1", "d0") else "pricing"
    rel_app = [a for a in APPS if a[1] == release][0]
    ns, app = rel_app[0], rel_app[1]
    values = {"image": {"repository": f"ghcr.io/acme/{app}", "tag": tag_of(app, variant)}, "replicaCount": rel_app[4],
              "containerPort": port_of(app, variant)}
    if app == "pricing":
        values["cache"] = {"enabled": True, "ttlSeconds": 0 if variant == "d2" else 300,
                           "address": "redis.cache.svc.cluster.local:6379"}
    applied = [d for d in deployments if d["metadata"]["name"] == app][0]
    applied = {k: v for k, v in applied.items() if k != "status"}
    if app == "pricing":
        applied = {**applied, "spec": {**applied["spec"], "replicas": 3}}
    history = [{"revision": 4, "updated": ts(-43200), "status": "superseded",
                "chart": f"{app}-{'1.9.0' if app == 'inventory' else '1.7.0'}", "description": "Upgrade complete"}]
    history[0]["status"] = "superseded"
    history.append({"revision": 5, "updated": ts(0 if variant != "d0" else -2000), "status": "deployed",
                    "chart": f"{app}-{tag_of(app, variant)}", "description": "Upgrade complete"})

    files = {"kube/namespaces.yaml": as_list("Namespace", namespaces),
             "kube/deployments.yaml": as_list("Deployment", deployments),
             "kube/pods.yaml": as_list("Pod", pods),
             "kube/services.yaml": as_list("Service", services),
             "kube/endpoints.yaml": as_list("Endpoints", endpoints),
             "kube/certificates.yaml": certificate,
             "kube/events.yaml": as_list("Event", sorted(events, key=lambda e: e["firstTimestamp"])),
             "helm/values.yaml": values, "helm/release.json": values, "helm/history.json": history,
             "loki/streams.json": streams, "prometheus/alerts.json": alerts}
    if traces:
        files["otel/traces.json"] = traces
    write(CASE[variant], files, {**COMMON, "release": release, **EXPECT[variant]})
    (OUT / CASE[variant] / "helm" / "manifest.yaml").write_text(yaml.safe_dump(applied, sort_keys=False, width=100000))


CASE = {"d1": "d1_dependency_service_wrong_port", "d2": "d2_dependency_redis_full",
        "d0": "d0_dependency_no_incident"}
SOURCES = ["prometheus-operator/runbooks@a685d14 KubePodNotReady",
           "prometheus-operator/runbooks@a685d14 KubeDeploymentReplicasMismatch",
           "https://kubernetes.io/docs/concepts/services-networking/service/",
           "https://redis.io/docs/latest/develop/reference/eviction/"]
COMMON = {"set": "S2", "family": "dependency", "namespace": "shop", "sources": SOURCES,
          "spec": "docs/s2/family-dependency.md"}
THROTTLING = {"object": "Pod/shop/checkout-api (CPUThrottlingHigh)", "why_not": "older than T0, unrelated to the errors"}
EXPECT = {
    "d1": {"scenario": "checkout-api's calls to inventory are refused; inventory is Ready, its Service targets the old port",
           "query": "why does checkout-api fail to reserve stock in shop", "expected_outcome": "cause",
           "cause_object": "Service/platform/inventory",
           "mechanism": "the inventory upgrade moved the container to port 9090, while Service platform/inventory, "
                        "owned by another release, still targets 8080: every connection is refused while the pods "
                        "report Ready",
           "pass_if": "names the port mismatch between the Service and the inventory pods, blaming either side",
           "partial_if_only": ["inventory unreachable or connection refused, without the port"],
           "chain": ["checkout-api's calls to inventory.platform:80 are refused",
                     "Service/platform/inventory has ready endpoints: the inventory pods are Ready",
                     "the Service sends traffic to targetPort 8080",
                     "the inventory upgrade at T0 moved the container and its probe to 9090"],
           "decoys": [THROTTLING,
                      {"object": "Deployment/platform/inventory (image 2.0.0)", "why_not": "expected with the upgrade"},
                      {"object": "Pod/platform/inventory (one readiness failure)", "why_not": "during the rollout, then Ready"}],
           "affected_resources": ["Deployment/shop/checkout-api"],
           "root_cause_concepts": ["port|targetport|9090|8080", "inventory"]},
    "d2": {"scenario": "checkout-api gets 503 from pricing; pricing's writes to Redis are refused, Redis is full",
           "query": "why does checkout-api return errors on pricing in shop", "expected_outcome": "cause",
           "cause_object": "Deployment/cache/redis", "origin_object": "Deployment/platform/pricing",
           "mechanism": "Redis reached its maxmemory (512mb) with the noeviction policy, so it refuses writes; pricing, "
                        "which since its T0 release caches every price without a TTL, fails on them and returns 503 to "
                        "checkout-api",
           "pass_if": "names Redis's memory limit or eviction policy as what refuses the writes",
           "partial_if_only": ["pricing as failing, without Redis's limit"],
           "chain": ["checkout-api logs and traces: pricing returned 503",
                     "pricing's logs and traces: Redis SET refused, OOM command not allowed when used memory > maxmemory",
                     "Deployment/cache/redis runs with --maxmemory 512mb --maxmemory-policy noeviction",
                     "pricing's T0 release caches every price with ttlSeconds 0"],
           "decoys": [THROTTLING,
                      {"object": "Certificate/shop/shop-tls", "why_not": "expires in 12 days, Ready; a warning, not a fault"},
                      {"object": "Deployment/platform/pricing (scaled 3 to 5)", "why_not": "a manual reaction at T0+3 h 10 min"}],
           "affected_resources": ["Deployment/shop/checkout-api"],
           "root_cause_concepts": ["redis", "maxmemory|memory|noeviction|eviction|full"]},
    "d0": {"scenario": "No incident: the same objects as d1 and d2, ports that match, Redis with room",
           "query": "is something wrong with checkout-api in shop", "expected_outcome": "no_incident",
           "cause_object": None,
           "decoys": [THROTTLING,
                      {"object": "Certificate/shop/shop-tls", "why_not": "expires in 12 days, Ready"},
                      {"object": "Pod/platform/inventory (old readiness failure)", "why_not": "during an old rollout"}],
           "affected_resources": [], "root_cause_contains": [],
           "notes": "Passes only on an explicit no-incident answer (protocol 002, A2)."},
}

if __name__ == "__main__":
    for v in ("d1", "d2", "d0"):
        build(v)
    print("written:", ", ".join(sorted(CASE.values())))
