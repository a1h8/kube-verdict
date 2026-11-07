"""Builds S2's HPA family (docs/s2/family-hpa.md): a1, a2 and their negative a0.

Written 2026-11-07 from the family spec and its evidence tables, before any arm ran on these cases.
Frozen by hash before the first run (docs/s2-correlation-cases.md). Nothing in a fixture says which
object is the cause.

    python tests/s2/build_family_hpa.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_family_quota import OUT, alert, as_list, event, ts, write  # noqa: E402

NODES = ["worker-node-01", "worker-node-02", "worker-node-03"]
# namespace, app, release, tag, replicas
WORKLOADS = [("shop", "api", "api", "3.2.0", 4), ("shop", "web", "web", "4.2.0", 2), ("shop", "worker", "worker", "1.4.0", 2),
             ("monitoring", "prometheus-server", "prometheus", "2.53.0", 1),
             ("observability", "prometheus-adapter", "prometheus-adapter", "0.12.0", 1),
             ("kube-system", "metrics-server", "metrics-server", "0.7.1", 1),
             ("kube-system", "coredns", "coredns", "1.11.3", 2)]
RS = {"api": "6d8f9c7b5", "web": "8c7b6d5f9", "worker": "5f6c7d8b9", "prometheus-server": "5b6c7d8e9",
      "prometheus-adapter": "7c8d9b6f4", "metrics-server": "6b7c8d9f5", "coredns": "7db6d8ff4"}
METRIC = "http_requests_per_second"


def deployment(ns, app, rel, tag, n):
    return {"apiVersion": "apps/v1", "kind": "Deployment",
            "metadata": {"name": app, "namespace": ns, "uid": f"dep-{ns}-{app}",
                         "labels": {"app.kubernetes.io/name": app, "app.kubernetes.io/managed-by": "Helm"},
                         "annotations": {"meta.helm.sh/release-name": rel, "meta.helm.sh/release-namespace": ns}},
            "spec": {"replicas": n, "selector": {"matchLabels": {"app.kubernetes.io/name": app}},
                     "template": {"metadata": {"labels": {"app.kubernetes.io/name": app}},
                                  "spec": {"containers": [{"name": app, "image": f"ghcr.io/acme/{app}:{tag}",
                                                           "ports": [{"containerPort": 8080 if ns == "shop" else 6443}],
                                                           "resources": {"requests": {"cpu": "500m", "memory": "512Mi"},
                                                                         "limits": {"memory": "1Gi"}}}]}}},
            "status": {"replicas": n, "readyReplicas": n, "availableReplicas": n, "updatedReplicas": n,
                       "conditions": [{"type": "Available", "status": "True"}]}}


def pod(ns, app, i, tag, started):
    name = f"{app}-{RS[app]}-{(i * 7919 + len(app) * 104729) % 1048576:05x}"
    return {"apiVersion": "v1", "kind": "Pod",
            "metadata": {"name": name, "namespace": ns, "uid": f"pod-{ns}-{name}",
                         "labels": {"app.kubernetes.io/name": app, "pod-template-hash": RS[app]},
                         "ownerReferences": [{"apiVersion": "apps/v1", "kind": "ReplicaSet", "name": f"{app}-{RS[app]}",
                                              "controller": True}]},
            "spec": {"nodeName": NODES[i % 3],
                     "containers": [{"name": app, "image": f"ghcr.io/acme/{app}:{tag}",
                                     "resources": {"requests": {"cpu": "500m", "memory": "512Mi"}, "limits": {"memory": "1Gi"}}}]},
            "status": {"phase": "Running", "podIP": f"10.42.{i % 3 + 1}.{40 + len(app) + i}",
                       "conditions": [{"type": "Ready", "status": "True"}],
                       "containerStatuses": [{"name": app, "image": f"ghcr.io/acme/{app}:{tag}", "ready": True,
                                              "restartCount": 0, "state": {"running": {"startedAt": ts(started)}},
                                              "lastState": {}}]}}


def hpa(variant: str, current: int, desired: int) -> dict:
    spec = {"scaleTargetRef": {"apiVersion": "apps/v1", "kind": "Deployment", "name": "api"},
            "minReplicas": 4, "maxReplicas": 20,
            "metrics": [{"type": "Pods", "pods": {"metric": {"name": METRIC},
                                                  "target": {"type": "AverageValue", "averageValue": "50"}}}]}
    if variant == "a2":
        spec["behavior"] = {"scaleUp": {"stabilizationWindowSeconds": 600,
                                        "policies": [{"type": "Pods", "value": 1, "periodSeconds": 600}]}}
    conditions = [{"type": "AbleToScale", "status": "True", "reason": "ReadyForNewScale"}]
    if variant == "a1":
        conditions.append({"type": "ScalingActive", "status": "False", "reason": "FailedGetPodsMetric",
                           "message": f"the HPA was unable to compute the replica count: unable to get metric {METRIC}: "
                                      "unable to fetch metrics from custom metrics API: the server is currently unable "
                                      "to handle the request (get pods.custom.metrics.k8s.io *)"})
    else:
        conditions.append({"type": "ScalingActive", "status": "True", "reason": "ValidMetricFound"})
    if variant == "a2":
        conditions.append({"type": "ScalingLimited", "status": "True", "reason": "ScaleUpLimit",
                           "message": "the desired replica count is increasing faster than the maximum scale rate"})
    return {"apiVersion": "autoscaling/v2", "kind": "HorizontalPodAutoscaler",
            "metadata": {"name": "api", "namespace": "shop", "uid": "hpa-shop-api",
                         "annotations": {"meta.helm.sh/release-name": "api", "meta.helm.sh/release-namespace": "shop"}},
            "spec": spec,
            "status": {"currentReplicas": current, "desiredReplicas": desired, "conditions": conditions}}


def build(variant: str) -> None:
    now = 3215 if variant == "a2" else 70
    adapter_ns_for_api = "monitoring" if variant == "a1" else "observability"
    api_replicas = 7 if variant == "a2" else 4
    namespaces = [{"apiVersion": "v1", "kind": "Namespace",
                   "metadata": {"name": n, "uid": f"ns-{n}", "labels": {"kubernetes.io/metadata.name": n}}}
                  for n in ("shop", "monitoring", "observability", "kube-system")]
    deployments, pods = [], []
    for ns, app, rel, tag, n in WORKLOADS:
        if app == "api":
            n = api_replicas
            tag = "3.2.0" if variant == "a2" else "3.1.4"
        deployments.append(deployment(ns, app, rel, tag, n))
        for i in range(n):
            started = -9000
            if app == "prometheus-adapter":
                started = 1 if variant == "a1" else -9000
            if app == "api" and variant == "a2":
                started = 0 if i < 4 else 3190 + 10 * (i - 4)
            pods.append(pod(ns, app, i, tag, started))

    services = [{"apiVersion": "v1", "kind": "Service",
                 "metadata": {"name": app, "namespace": ns, "uid": f"svc-{ns}-{app}"},
                 "spec": {"selector": {"app.kubernetes.io/name": app},
                          "ports": [{"name": "https" if ns != "shop" else "http",
                                     "port": 443 if app in ("prometheus-adapter", "metrics-server") else 80,
                                     "targetPort": 6443 if ns != "shop" else 8080}]}}
                for ns, app, _, _, _ in WORKLOADS]
    if variant == "a1":
        services.append({"apiVersion": "v1", "kind": "Service",
                         "metadata": {"name": "prometheus-adapter", "namespace": "monitoring",
                                      "uid": "svc-monitoring-prometheus-adapter"},
                         "spec": {"selector": {"app.kubernetes.io/name": "prometheus-adapter"},
                                  "ports": [{"name": "https", "port": 443, "targetPort": 6443}]}})
    endpoints = []
    for s in services:
        ns, name = s["metadata"]["namespace"], s["metadata"]["name"]
        addrs = [{"ip": p["status"]["podIP"], "targetRef": {"kind": "Pod", "name": p["metadata"]["name"]}}
                 for p in pods if p["metadata"]["namespace"] == ns and p["metadata"]["name"].startswith(name + "-")]
        endpoints.append({"apiVersion": "v1", "kind": "Endpoints", "metadata": {"name": name, "namespace": ns},
                          "subsets": [{"addresses": addrs, "ports": [{"port": s["spec"]["ports"][0]["targetPort"]}]}]
                          if addrs else []})

    custom_ok = variant != "a1"
    apiservices = [
        {"apiVersion": "apiregistration.k8s.io/v1", "kind": "APIService", "metadata": {"name": "v1beta1.metrics.k8s.io"},
         "spec": {"group": "metrics.k8s.io", "version": "v1beta1",
                  "service": {"namespace": "kube-system", "name": "metrics-server", "port": 443}},
         "status": {"conditions": [{"type": "Available", "status": "True", "reason": "Passed",
                                    "message": "all checks passed"}]}},
        {"apiVersion": "apiregistration.k8s.io/v1", "kind": "APIService",
         "metadata": {"name": "v1beta1.custom.metrics.k8s.io"},
         "spec": {"group": "custom.metrics.k8s.io", "version": "v1beta1",
                  "service": {"namespace": adapter_ns_for_api, "name": "prometheus-adapter", "port": 443}},
         "status": {"conditions": [{"type": "Available", "status": "True" if custom_ok else "False",
                                    "reason": "Passed" if custom_ok else "MissingEndpoints",
                                    "message": "all checks passed" if custom_ok else
                                    "endpoints for service/prometheus-adapter in \"monitoring\" have no addresses with port name \"https\"",
                                    "lastTransitionTime": ts(0 if not custom_ok else -9000)}]}}]

    events = []
    if variant == "a1":
        events.append(event("HorizontalPodAutoscaler", "api", "FailedGetPodsMetric",
                            f"unable to get metric {METRIC}: unable to fetch metrics from custom metrics API: the server "
                            "is currently unable to handle the request (get pods.custom.metrics.k8s.io *)",
                            2, now, count=136, etype="Warning", component="horizontal-pod-autoscaler"))
        events.append(event("Deployment", "prometheus-adapter", "ScalingReplicaSet",
                            "Scaled up replica set prometheus-adapter-7c8d9b6f4 to 1", 1, ns="observability",
                            component="deployment-controller"))
    peaks_before = [-2580, -1140]
    peaks_after = [300, 1740, 3180]
    if variant == "a2":
        for p0 in peaks_before:
            events += [event("HorizontalPodAutoscaler", "api", "SuccessfulRescale",
                             f"New size: 8; reason: pods metric {METRIC} above target", p0,
                             component="horizontal-pod-autoscaler"),
                       event("HorizontalPodAutoscaler", "api", "SuccessfulRescale",
                             f"New size: 12; reason: pods metric {METRIC} above target", p0 + 0.5,
                             component="horizontal-pod-autoscaler"),
                       event("HorizontalPodAutoscaler", "api", "SuccessfulRescale",
                             "New size: 4; reason: All metrics below target", p0 + 120,
                             component="horizontal-pod-autoscaler")]
        for p0 in peaks_after:
            last = 8 if p0 != 3180 else 7
            for k, size in enumerate(range(5, last + 1)):
                events.append(event("HorizontalPodAutoscaler", "api", "SuccessfulRescale",
                                    f"New size: {size}; reason: pods metric {METRIC} above target", p0 + 10 + 10 * k,
                                    component="horizontal-pod-autoscaler"))
            if p0 != 3180:
                events.append(event("HorizontalPodAutoscaler", "api", "SuccessfulRescale",
                                    "New size: 4; reason: All metrics below target", p0 + 150,
                                    component="horizontal-pod-autoscaler"))
        events.append(event("Deployment", "api", "ScalingReplicaSet", "Scaled up replica set api-6d8f9c7b5 to 4", 0,
                            component="deployment-controller"))
    if variant == "a0":
        for p0 in peaks_before:
            events.append(event("HorizontalPodAutoscaler", "api", "SuccessfulRescale",
                                f"New size: 9; reason: pods metric {METRIC} above target", p0 + 3000,
                                component="horizontal-pod-autoscaler"))

    alerts = []
    if variant == "a1":
        alerts = [alert("HighLatencyP99", "warning", {"namespace": "shop", "deployment": "api"},
                        "api p99 latency above 500ms", "p99 latency is 1.9s while traffic rises.", 55),
                  alert("KubeAggregatedAPIErrors", "warning", {"name": "v1beta1.custom.metrics.k8s.io"},
                        "Kubernetes aggregated API has reported errors.",
                        "Kubernetes aggregated API v1beta1.custom.metrics.k8s.io has reported errors.", 5)]
    if variant == "a2":
        alerts = [alert("LatencySLOBreach", "critical", {"namespace": "shop", "deployment": "api", "slo": "api-latency"},
                        "api breaches its latency SLO",
                        "p99 1.4s against 400ms; the SLO burned at each of the last three daily peaks.", 3190)]

    if variant == "a2":
        history = [{"revision": 11, "updated": ts(-43200), "status": "superseded", "chart": "api-3.1.4",
                    "app_version": "3.1.4", "description": "Upgrade complete"},
                   {"revision": 12, "updated": ts(0), "status": "deployed", "chart": "api-3.2.0",
                    "app_version": "3.2.0", "description": "Upgrade complete"}]
    else:
        history = [{"revision": 11, "updated": ts(-43200), "status": "deployed", "chart": "api-3.1.4",
                    "app_version": "3.1.4", "description": "Upgrade complete"}]
    releases = [{"name": "api", "namespace": "shop", "revision": history[-1]["revision"], "status": "deployed",
                 "chart": history[-1]["chart"], "updated": history[-1]["updated"]},
                {"name": "prometheus-adapter", "namespace": "observability", "revision": 1, "status": "deployed",
                 "chart": "prometheus-adapter-4.10.0", "updated": ts(0 if variant == "a1" else -60000)}]
    if variant == "a1":
        releases.append({"name": "prometheus-adapter", "namespace": "monitoring", "revision": 9, "status": "uninstalled",
                         "chart": "prometheus-adapter-4.9.1", "updated": ts(0)})

    api_dep = {k: v for k, v in [d for d in deployments if d["metadata"]["name"] == "api"][0].items() if k != "status"}
    api_dep["spec"] = {k: v for k, v in api_dep["spec"].items() if k != "replicas"}
    hpa_now = hpa(variant, 7 if variant == "a2" else 4, 12 if variant == "a2" else 4)
    hpa_applied = {k: v for k, v in hpa_now.items() if k != "status"}
    manifest = yaml.safe_dump(api_dep, sort_keys=False, width=100000) + "---\n" + \
        yaml.safe_dump(hpa_applied, sort_keys=False, width=100000)
    values = {"image": {"tag": "3.2.0" if variant == "a2" else "3.1.4"},
              "autoscaling": {"minReplicas": 4, "maxReplicas": 20, "metric": METRIC, "targetAverageValue": 50}}
    if variant == "a2":
        values["autoscaling"]["behavior"] = hpa_now["spec"]["behavior"]

    files = {"kube/namespaces.yaml": as_list("Namespace", namespaces),
             "kube/deployments.yaml": as_list("Deployment", deployments),
             "kube/pods.yaml": as_list("Pod", pods),
             "kube/services.yaml": as_list("Service", services),
             "kube/endpoints.yaml": as_list("Endpoints", endpoints),
             "kube/apiservices.yaml": as_list("APIService", apiservices),
             "kube/hpa.yaml": hpa_now,
             "kube/events.yaml": as_list("Event", sorted(events, key=lambda e: e["firstTimestamp"])),
             "helm/values.yaml": values, "helm/release.json": values, "helm/history.json": history,
             "helm/releases.json": releases, "prometheus/alerts.json": alerts}
    write(CASE[variant], files, {**COMMON, **EXPECT[variant]})
    (OUT / CASE[variant] / "helm" / "manifest.yaml").write_text(manifest)
    if variant == "a2":
        previous = {k: v for k, v in hpa_applied.items()}
        previous["spec"] = {k: v for k, v in hpa_applied["spec"].items() if k != "behavior"}
        (OUT / CASE[variant] / "helm" / "manifest-previous.yaml").write_text(
            yaml.safe_dump(api_dep, sort_keys=False, width=100000).replace("api:3.2.0", "api:3.1.4") + "---\n"
            + yaml.safe_dump(previous, sort_keys=False, width=100000))


CASE = {"a1": "a1_hpa_custom_metrics_api_points_away", "a2": "a2_hpa_scale_up_policy_slowed",
        "a0": "a0_hpa_no_incident"}
SOURCES = ["prometheus-operator/runbooks@a685d14 KubeAggregatedAPIDown",
           "prometheus-operator/runbooks@a685d14 KubeAggregatedAPIErrors",
           "prometheus-operator/runbooks@a685d14 KubeHpaReplicasMismatch",
           "https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/"]
COMMON = {"set": "S2", "family": "hpa", "namespace": "shop", "release": "api", "sources": SOURCES,
          "spec": "docs/s2/family-hpa.md"}
EXPECT = {
    "a1": {"scenario": "api does not scale under load; the custom metrics API points to the namespace its adapter left",
           "query": "why doesn't api scale up under load in shop", "expected_outcome": "cause",
           "cause_object": "APIService/v1beta1.custom.metrics.k8s.io",
           "mechanism": "the APIService still points to monitoring/prometheus-adapter, which has had no endpoints since "
                        "the adapter moved to observability at T0, so the custom metrics API is unavailable and the HPA "
                        "cannot read http_requests_per_second",
           "pass_if": "names the APIService, or the custom metrics API, pointing to the adapter's old location",
           "partial_if_only": ["the HPA cannot read its metric, or the metrics API is unavailable, without where it points"],
           "chain": ["api saturates; its HPA stays at 4", "the HPA: FailedGetPodsMetric on http_requests_per_second",
                     "APIService v1beta1.custom.metrics.k8s.io Available=False, MissingEndpoints, points to monitoring",
                     "Service monitoring/prometheus-adapter has no endpoints",
                     "the adapter runs in observability since T0"],
           "decoys": [{"object": "Deployment/observability/prometheus-adapter", "why_not": "healthy, at its new address"},
                      {"object": "Deployment/kube-system/metrics-server", "why_not": "healthy; resource metrics work"},
                      {"object": "Deployment/shop/api (busy pods)", "why_not": "the load, not a fault"}],
           "affected_resources": ["Deployment/shop/api"],
           "root_cause_concepts": ["apiservice|custom.metrics|custom metrics api", "monitoring|observability|adapter"]},
    "a2": {"scenario": "api absorbs peaks too slowly; a release gave its HPA a one-pod-per-10-minutes scale-up policy",
           "query": "why does api take so long to absorb traffic peaks in shop", "expected_outcome": "cause",
           "cause_object": "HorizontalPodAutoscaler/shop/api",
           "mechanism": "the T0 release (chart 3.2.0) set behavior.scaleUp to one pod per 600 s with a 600 s "
                        "stabilization window, so the HPA adds replicas too slowly and api saturates at each peak",
           "pass_if": "names the HPA's scale-up policy or rate limit",
           "partial_if_only": ["the HPA scales too slowly, without the policy", "the release, without the policy"],
           "chain": ["api breaches its latency SLO at each peak", "the HPA adds one pod every 10 minutes, ScaleUpLimit",
                     "its behavior.scaleUp: 1 pod per 600 s, 600 s window",
                     "before T0 it went to 12 within a minute", "the T0 release, chart api-3.2.0, added that behavior"],
           "decoys": [{"object": "the daily traffic peaks", "why_not": "the same before T0, absorbed then"},
                      {"object": "HorizontalPodAutoscaler/shop/api maxReplicas 20", "why_not": "never reached"},
                      {"object": "the nodes", "why_not": "no pod Pending: capacity is not the limit"}],
           "affected_resources": ["Deployment/shop/api"],
           "root_cause_concepts": ["behavior|scaleup|scale-up|policy|stabilization|rate", "hpa|autoscal"]},
    "a0": {"scenario": "No incident: the APIService points to the adapter, the HPA follows the load",
           "query": "is something wrong with api in shop", "expected_outcome": "no_incident", "cause_object": None,
           "decoys": [{"object": "Deployment/kube-system/metrics-server", "why_not": "healthy"},
                      {"object": "the daily peaks", "why_not": "absorbed"}],
           "affected_resources": [], "root_cause_contains": [],
           "notes": "Passes only on an explicit no-incident answer (protocol 002, A2)."},
}

if __name__ == "__main__":
    for v in ("a1", "a2", "a0"):
        build(v)
    print("written:", ", ".join(sorted(CASE.values())))
