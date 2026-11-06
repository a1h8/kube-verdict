"""Builds S2's ResourceQuota family (docs/s2/family-quota.md): q1, q2 and their negative q0.

Written 2026-11-06 from the family spec, before any arm ran on these cases. Run it to rewrite the
fixtures; the cases are frozen by their hash before the first run (docs/s2-correlation-cases.md),
so a change here after that makes new cases.

    python tests/s2/build_family_quota.py

Nothing in a fixture says which object is the cause or a decoy: the raw arm reads every file of a
case except ``expect.json``.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

OUT = Path(__file__).resolve().parent / "cases"
T0 = datetime(2026, 10, 20, 8, 0, tzinfo=timezone.utc)
GI = 1024


def ts(minutes: float) -> str:
    return (T0 + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def ns_epoch(minutes: float) -> str:
    return str(int((T0 + timedelta(minutes=minutes)).timestamp() * 1e9))


# name: image tag (applied), cpu request (applied), memory request in Mi, replicas at rest
APPS = {
    "catalog-api":    ("3.2.0", "500m", 1024, 4),
    "cart-api":       ("2.3.1", "250m", 1024, 2),
    "billing-worker": ("1.8.0", "500m", 2048, 2),
    "search-api":     ("4.0.2", "500m", 1024, 3),
    "checkout-web":   ("5.1.0", "200m", 512, 2),
    "inventory-api":  ("2.0.4", "200m", 512, 1),
    "notify-worker":  ("1.1.0", "100m", 512, 1),
}
HASH = {"catalog-api": "7c9d8b6f5", "cart-api": "5d6f8c7b9", "billing-worker": "84b7c6d5f",
        "search-api": "6f9c8d7b4", "checkout-web": "9b8c7d6f5", "inventory-api": "7d6c5b8f9",
        "notify-worker": "5c8b9d7f6"}
NODES = ["worker-node-01", "worker-node-02", "worker-node-03"]
QUOTA_HARD = {"requests.cpu": "16", "requests.memory": "32Gi", "pods": "40"}


def mem(mi: int) -> str:
    return f"{mi // GI}Gi" if mi % GI == 0 else f"{mi}Mi"


def millicores(cpu: str) -> int:
    return int(cpu[:-1]) if cpu.endswith("m") else int(float(cpu) * 1000)


def labels(app: str, instance: str = "shop") -> dict:
    return {"app.kubernetes.io/name": app, "app.kubernetes.io/instance": instance,
            "app.kubernetes.io/managed-by": "Helm"}


def container(app: str, tag: str, cpu: str, mem_mi: int, ns: str = "shop", limit_cpu: str | None = None) -> dict:
    return {"name": app, "image": f"ghcr.io/acme/{app}:{tag}",
            "ports": [{"containerPort": 8080, "name": "http"}],
            "resources": {"requests": {"cpu": cpu, "memory": mem(mem_mi)},
                          "limits": {"cpu": limit_cpu or f"{2 * millicores(cpu)}m", "memory": mem(mem_mi)}},
            "readinessProbe": {"httpGet": {"path": "/ready", "port": 8080}, "periodSeconds": 10}}


def deployment(app: str, tag: str, cpu: str, mem_mi: int, replicas: int, ready: int,
               ns: str = "shop", instance: str = "shop", conditions: list | None = None,
               limit_cpu: str | None = None) -> dict:
    return {
        "apiVersion": "apps/v1", "kind": "Deployment",
        "metadata": {"name": app, "namespace": ns, "uid": f"dep-{ns}-{app}", "labels": labels(app, instance),
                     "annotations": {"meta.helm.sh/release-name": instance,
                                     "meta.helm.sh/release-namespace": ns}},
        "spec": {"replicas": replicas,
                 "selector": {"matchLabels": {"app.kubernetes.io/name": app}},
                 "strategy": {"type": "RollingUpdate",
                              "rollingUpdate": {"maxSurge": 1, "maxUnavailable": 0}},
                 "template": {"metadata": {"labels": labels(app, instance)},
                              "spec": {"containers": [container(app, tag, cpu, mem_mi, ns, limit_cpu)]}}},
        "status": {"replicas": replicas, "readyReplicas": ready, "availableReplicas": ready,
                   "updatedReplicas": ready,
                   "conditions": conditions or [{"type": "Available", "status": "True"},
                                                {"type": "Progressing", "status": "True",
                                                 "reason": "NewReplicaSetAvailable"}]}}


def pod(app: str, rs_hash: str, i: int, tag: str, cpu: str, mem_mi: int, started: float,
        ns: str = "shop", instance: str = "shop", ready: bool = True, restarts: int = 0,
        last_state: dict | None = None, owner: tuple[str, str] | None = None,
        phase: str = "Running", limit_cpu: str | None = None) -> dict:
    suffix = f"{(i * 7919 + len(app) * 104729) % 46656:05x}"[-5:]
    name = f"{app}-{rs_hash}-{suffix}" if owner is None else f"{owner[1]}-{suffix}"
    okind, oname = owner or ("ReplicaSet", f"{app}-{rs_hash}")
    status = {"phase": phase,
              "conditions": [{"type": "Ready", "status": "True" if ready else "False"}],
              "containerStatuses": [{"name": app, "image": f"ghcr.io/acme/{app}:{tag}", "ready": ready,
                                     "restartCount": restarts,
                                     "state": {"running": {"startedAt": ts(started)}},
                                     "lastState": last_state or {}}]}
    if phase == "Succeeded":
        status["containerStatuses"][0]["state"] = {"terminated": {"reason": "Completed", "exitCode": 0,
                                                                   "finishedAt": ts(started + 6)}}
        status["conditions"] = [{"type": "Ready", "status": "False", "reason": "PodCompleted"}]
    return {
        "apiVersion": "v1", "kind": "Pod",
        "metadata": {"name": name, "namespace": ns, "uid": f"pod-{ns}-{name}",
                     "labels": {**labels(app, instance), "pod-template-hash": rs_hash},
                     "ownerReferences": [{"apiVersion": "apps/v1" if okind == "ReplicaSet" else "batch/v1",
                                          "kind": okind, "name": oname, "controller": True}]},
        "spec": {"nodeName": NODES[i % 3], "containers": [container(app, tag, cpu, mem_mi, ns, limit_cpu)]},
        "status": status}


def event(kind: str, name: str, reason: str, message: str, first: float, last: float | None = None,
          count: int = 1, ns: str = "shop", etype: str = "Normal", component: str = "") -> dict:
    return {"apiVersion": "v1", "kind": "Event",
            "metadata": {"name": f"{name}.{reason.lower()}.{int(first * 60):x}", "namespace": ns,
                         "uid": f"evt-{ns}-{name}-{reason.lower()}-{int(first * 60):x}"},
            "type": etype, "reason": reason, "message": message,
            "involvedObject": {"kind": kind, "name": name, "namespace": ns},
            "count": count, "firstTimestamp": ts(first), "lastTimestamp": ts(last if last is not None else first),
            "reportingComponent": component}


def alert(name: str, severity: str, labels_: dict, summary: str, description: str, active: float) -> dict:
    return {"labels": {"alertname": name, "severity": severity, **labels_},
            "annotations": {"summary": summary, "description": description},
            "state": "firing", "activeAt": ts(active), "value": "1"}


def write(case: str, files: dict[str, object], expect: dict) -> None:
    d = OUT / case
    for rel, content in files.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if rel.endswith(".json"):
            p.write_text(json.dumps(content, indent=2) + "\n")
        else:
            p.write_text(yaml.safe_dump(content, sort_keys=False, width=120))
    (d / "expect.json").write_text(json.dumps(expect, indent=2) + "\n")


def as_list(kind: str, items: list) -> dict:
    return {"apiVersion": "v1", "kind": f"{kind}List", "metadata": {}, "items": items}


def build(variant: str) -> None:
    """variant: q1 (request drift fills the memory quota), q2 (Jobs fill the pod quota), q0 (healthy)."""
    now = 280 if variant == "q2" else 90
    replicas = {a: r for a, (_, _, _, r) in APPS.items()}
    live_cpu = {a: c for a, (_, c, _, _) in APPS.items()}
    live_tag = {a: t for a, (t, _, _, _) in APPS.items()}
    live_tag["cart-api"] = "2.3.2"                       # all variants: a newer patch than applied
    if variant == "q1":
        live_cpu["catalog-api"] = "100m"
        replicas["catalog-api"] = 20
    hashes = dict(HASH)
    if variant == "q1":
        hashes["catalog-api"] = "5f6b7d9c8"

    deployments, pods, events = [], [], []
    for app, (_, _, mem_mi, _) in APPS.items():
        r = replicas[app]
        conditions = None
        if variant == "q1" and app == "billing-worker":
            conditions = [{"type": "Available", "status": "True"},
                          {"type": "Progressing", "status": "True", "reason": "ReplicaSetUpdated"},
                          {"type": "ReplicaFailure", "status": "True", "reason": "FailedCreate",
                           "message": "pods \"billing-worker-6b8c4d7f9-\" is forbidden: exceeded quota: "
                                      "shop-quota, requested: requests.memory=2Gi, used: "
                                      "requests.memory=31Gi, limited: requests.memory=32Gi"}]
        if variant == "q2" and app == "search-api":
            conditions = [{"type": "Available", "status": "True"},
                          {"type": "Progressing", "status": "True", "reason": "ReplicaSetUpdated"},
                          {"type": "ReplicaFailure", "status": "True", "reason": "FailedCreate",
                           "message": "pods \"search-api-6f9c8d7b4-\" is forbidden: exceeded quota: "
                                      "shop-quota, requested: pods=1, used: pods=40, limited: pods=40"}]
        want = 6 if (variant == "q2" and app == "search-api") else r
        applied_limit = f"{2 * millicores(APPS[app][1])}m"      # `kubectl set resources --requests` keeps it
        deployments.append(deployment(app, live_tag[app], live_cpu[app], mem_mi, want, r, conditions=conditions,
                                      limit_cpu=applied_limit))
        for i in range(r):
            started = -2880 - 37 * i
            if variant == "q1" and app == "catalog-api":
                started = 0 if i < 4 else 10 + (i - 4) * 1.0
            restarts, last = 0, None
            if app == "billing-worker" and i == 0:
                restarts = 1
                last = {"terminated": {"reason": "OOMKilled", "exitCode": 137,
                                       "startedAt": ts(-2880), "finishedAt": ts(now - 300)}}
            pods.append(pod(app, hashes[app], i, live_tag[app], live_cpu[app], mem_mi, started,
                            restarts=restarts, last_state=last, limit_cpu=applied_limit))

    # the CronJob and its Jobs
    cron_policy = "Allow" if variant == "q2" else "Forbid"
    jobs, job_pods = [], []
    if variant == "q2":
        for k in range(25):
            jname = f"report-export-{29342400 + k * 10}"
            start = k * 10
            jobs.append({"apiVersion": "batch/v1", "kind": "Job",
                         "metadata": {"name": jname, "namespace": "shop", "uid": f"job-shop-{jname}",
                                      "ownerReferences": [{"apiVersion": "batch/v1", "kind": "CronJob",
                                                           "name": "report-export", "controller": True}]},
                         "spec": {"backoffLimit": 2},
                         "status": {"active": 1, "startTime": ts(start)}})
            job_pods.append(pod("report-export", "job", k, "1.3.0", "100m", 256, start,
                                owner=("Job", jname)))
            events.append(event("CronJob", "report-export", "SuccessfulCreate", f"Created job {jname}",
                                start, component="cronjob-controller"))
    else:
        for k, start in enumerate((now - 25, now - 15)):
            jname = f"report-export-{29342400 + k * 10}"
            jobs.append({"apiVersion": "batch/v1", "kind": "Job",
                         "metadata": {"name": jname, "namespace": "shop", "uid": f"job-shop-{jname}",
                                      "ownerReferences": [{"apiVersion": "batch/v1", "kind": "CronJob",
                                                           "name": "report-export", "controller": True}]},
                         "spec": {"backoffLimit": 2},
                         "status": {"succeeded": 1, "startTime": ts(start), "completionTime": ts(start + 6)}})
            job_pods.append(pod("report-export", "job", k, "1.3.0", "100m", 256, start,
                                owner=("Job", jname), phase="Succeeded", ready=False))
            events.append(event("CronJob", "report-export", "SuccessfulCreate", f"Created job {jname}",
                                start, component="cronjob-controller"))
            events.append(event("CronJob", "report-export", "SawCompletedJob",
                                f"Saw completed job: {jname}, status: Complete", start + 6,
                                component="cronjob-controller"))
    cronjob = {"apiVersion": "batch/v1", "kind": "CronJob",
               "metadata": {"name": "report-export", "namespace": "shop", "uid": "cj-shop-report-export",
                            "labels": labels("report-export"),
                            "annotations": {"meta.helm.sh/release-name": "shop",
                                            "meta.helm.sh/release-namespace": "shop"}},
               "spec": {"schedule": "*/10 * * * *", "concurrencyPolicy": cron_policy,
                        "successfulJobsHistoryLimit": 3, "failedJobsHistoryLimit": 1,
                        "jobTemplate": {"spec": {"backoffLimit": 2, "template": {"spec": {
                            "restartPolicy": "OnFailure",
                            "containers": [{**container("report-export", "1.3.0", "100m", 256),
                                            "args": ["--db", "reports-db.data.svc.cluster.local:5432"]}]}}}}},
               "status": {"active": [{"kind": "Job", "name": j["metadata"]["name"]} for j in jobs
                                     if j["status"].get("active")],
                          "lastScheduleTime": ts(now - 10 if variant == "q2" else now - 15)}}

    # the quota, as the API reports it
    running = [p for p in pods + job_pods if p["status"]["phase"] == "Running"]
    used_mem = sum(int(p["spec"]["containers"][0]["resources"]["requests"]["memory"][:-2]) * (GI if p["spec"]["containers"][0]["resources"]["requests"]["memory"].endswith("Gi") else 1) for p in running)
    used_cpu = sum(millicores(p["spec"]["containers"][0]["resources"]["requests"]["cpu"]) for p in running)
    quota = {"apiVersion": "v1", "kind": "ResourceQuota",
             "metadata": {"name": "shop-quota", "namespace": "shop", "uid": "rq-shop-quota"},
             "spec": {"hard": QUOTA_HARD},
             "status": {"hard": QUOTA_HARD,
                        "used": {"requests.cpu": f"{used_cpu}m", "requests.memory": mem(used_mem),
                                 "pods": str(len(running))}}}
    limitrange = {"apiVersion": "v1", "kind": "LimitRange",
                  "metadata": {"name": "shop-limits", "namespace": "shop", "uid": "lr-shop-limits"},
                  "spec": {"limits": [{"type": "Container",
                                       "defaultRequest": {"cpu": "100m", "memory": "128Mi"},
                                       "default": {"cpu": "200m", "memory": "256Mi"},
                                       "max": {"cpu": "2", "memory": "4Gi"}}]}}

    # the HPAs
    cat_cur = replicas["catalog-api"]
    hpas = [
        {"apiVersion": "autoscaling/v2", "kind": "HorizontalPodAutoscaler",
         "metadata": {"name": "catalog-api", "namespace": "shop", "uid": "hpa-shop-catalog-api"},
         "spec": {"scaleTargetRef": {"apiVersion": "apps/v1", "kind": "Deployment", "name": "catalog-api"},
                  "minReplicas": 4, "maxReplicas": 20,
                  "metrics": [{"type": "Resource", "resource": {"name": "cpu", "target": {
                      "type": "Utilization", "averageUtilization": 70}}}]},
         "status": {"currentReplicas": cat_cur, "desiredReplicas": cat_cur,
                    "currentMetrics": [{"type": "Resource", "resource": {"name": "cpu", "current": {
                        "averageUtilization": 70 if variant == "q1" else 68}}}],
                    "conditions": ([{"type": "ScalingLimited", "status": "True", "reason": "TooManyReplicas",
                                     "message": "the desired replica count is more than the maximum replica count"}]
                                   if variant == "q1" else
                                   [{"type": "ScalingLimited", "status": "False", "reason": "DesiredWithinRange"}])}},
        {"apiVersion": "autoscaling/v2", "kind": "HorizontalPodAutoscaler",
         "metadata": {"name": "search-api", "namespace": "shop", "uid": "hpa-shop-search-api"},
         "spec": {"scaleTargetRef": {"apiVersion": "apps/v1", "kind": "Deployment", "name": "search-api"},
                  "minReplicas": 3, "maxReplicas": 8,
                  "metrics": [{"type": "Resource", "resource": {"name": "cpu", "target": {
                      "type": "Utilization", "averageUtilization": 70}}}]},
         "status": {"currentReplicas": 3, "desiredReplicas": 6 if variant == "q2" else 3,
                    "currentMetrics": [{"type": "Resource", "resource": {"name": "cpu", "current": {
                        "averageUtilization": 138 if variant == "q2" else 52}}}]}}]

    # events of the story
    events.append(event("Deployment", "cart-api", "ScalingReplicaSet",
                        "Scaled up replica set cart-api-5d6f8c7b9 to 2", -2900, component="deployment-controller"))
    if variant == "q1":
        events += [
            event("Deployment", "catalog-api", "ScalingReplicaSet",
                  "Scaled up replica set catalog-api-5f6b7d9c8 to 4", 0, component="deployment-controller"),
            event("Deployment", "catalog-api", "ScalingReplicaSet",
                  "Scaled down replica set catalog-api-7c9d8b6f5 to 0 from 4", 2, component="deployment-controller"),
            event("HorizontalPodAutoscaler", "catalog-api", "SuccessfulRescale",
                  "New size: 8; reason: cpu resource utilization (percentage of request) above target", 10,
                  component="horizontal-pod-autoscaler"),
            event("HorizontalPodAutoscaler", "catalog-api", "SuccessfulRescale",
                  "New size: 14; reason: cpu resource utilization (percentage of request) above target", 16,
                  component="horizontal-pod-autoscaler"),
            event("HorizontalPodAutoscaler", "catalog-api", "SuccessfulRescale",
                  "New size: 20; reason: cpu resource utilization (percentage of request) above target", 24,
                  component="horizontal-pod-autoscaler"),
            event("Deployment", "billing-worker", "ScalingReplicaSet",
                  "Scaled up replica set billing-worker-6b8c4d7f9 to 1", 40, component="deployment-controller"),
            event("ReplicaSet", "billing-worker-6b8c4d7f9", "FailedCreate",
                  "Error creating: pods \"billing-worker-6b8c4d7f9-\" is forbidden: exceeded quota: shop-quota, "
                  "requested: requests.memory=2Gi, used: requests.memory=31Gi, limited: requests.memory=32Gi",
                  40, 88, count=11, etype="Warning", component="replicaset-controller")]
    if variant == "q2":
        events += [
            event("Pod", "reports-db-6c7d8b9f4-x2k9p", "Unhealthy",
                  "Readiness probe failed: psql: error: connection to server on socket "
                  "\"/var/run/postgresql/.s.PGSQL.5432\" failed: FATAL:  the database system is in recovery mode",
                  0, now, count=1680, ns="data", etype="Warning", component="kubelet"),
            event("Deployment", "reports-db-proxy", "ScalingReplicaSet",
                  "Scaled up replica set reports-db-proxy-8d9c7b6f5 to 1", 120, ns="data",
                  component="deployment-controller"),
            event("HorizontalPodAutoscaler", "search-api", "SuccessfulRescale",
                  "New size: 6; reason: cpu resource utilization (percentage of request) above target", 255,
                  component="horizontal-pod-autoscaler"),
            event("ReplicaSet", "search-api-6f9c8d7b4", "FailedCreate",
                  "Error creating: pods \"search-api-6f9c8d7b4-\" is forbidden: exceeded quota: shop-quota, "
                  "requested: pods=1, used: pods=40, limited: pods=40",
                  255, now - 2, count=14, etype="Warning", component="replicaset-controller")]
    if variant == "q2":
        events.append(event("Deployment", "notify-worker", "ScalingReplicaSet",
                            "Scaled up replica set notify-worker-5c8b9d7f6 to 1", 150, component="deployment-controller"))
    if variant == "q0":
        events.append(event("HorizontalPodAutoscaler", "catalog-api", "SuccessfulRescale",
                            "New size: 4; reason: All metrics below target", -600, component="horizontal-pod-autoscaler"))

    # data and monitoring namespaces
    db_ready = variant != "q2"
    others = [deployment("reports-db", "15.4", "1", 4096, 1, 1 if db_ready else 0, ns="data", instance="reports"),
              deployment("reports-db-proxy", "1.21.0", "100m", 256, 1, 1, ns="data", instance="reports"),
              deployment("prometheus-server", "2.53.0", "500m", 2048, 1, 1, ns="monitoring", instance="prometheus")]
    other_pods = [pod("reports-db", "6c7d8b9f4", 0, "15.4", "1", 4096, -9000, ns="data", instance="reports",
                      ready=db_ready),
                  pod("reports-db-proxy", "8d9c7b6f5" if variant == "q2" else "7b8c9d6f5", 1, "1.21.0", "100m",
                      256, 120 if variant == "q2" else -9000, ns="data", instance="reports"),
                  pod("prometheus-server", "5b6c7d8e9", 2, "2.53.0", "500m", 2048, -12000, ns="monitoring",
                      instance="prometheus")]
    if not db_ready:
        other_pods[0]["metadata"]["name"] = "reports-db-6c7d8b9f4-x2k9p"
        other_pods[0]["metadata"]["uid"] = "pod-data-reports-db-6c7d8b9f4-x2k9p"

    services = [{"apiVersion": "v1", "kind": "Service",
                 "metadata": {"name": a, "namespace": "shop", "uid": f"svc-shop-{a}"},
                 "spec": {"selector": {"app.kubernetes.io/name": a}, "ports": [{"port": 80, "targetPort": 8080}]}}
                for a in APPS] + [
                {"apiVersion": "v1", "kind": "Service",
                 "metadata": {"name": "reports-db", "namespace": "data", "uid": "svc-data-reports-db"},
                 "spec": {"selector": {"app.kubernetes.io/name": "reports-db"}, "ports": [{"port": 5432}]}}]
    endpoints = {"apiVersion": "v1", "kind": "Endpoints",
                 "metadata": {"name": "reports-db", "namespace": "data"},
                 "subsets": [{"notReadyAddresses" if not db_ready else "addresses": [{"ip": "10.42.3.17"}],
                              "ports": [{"port": 5432}]}]}

    search_cfg = "max_results: 50\nrequest_timeout_ms: 800\n"
    configmaps = [{"apiVersion": "v1", "kind": "ConfigMap",
                   "metadata": {"name": "search-config", "namespace": "shop", "uid": "cm-shop-search-config",
                                "annotations": {"meta.helm.sh/release-name": "shop",
                                                "meta.helm.sh/release-namespace": "shop"}},
                   "data": {"search.yaml": search_cfg}},
                  {"apiVersion": "v1", "kind": "ConfigMap",
                   "metadata": {"name": "shop-env", "namespace": "shop", "uid": "cm-shop-env",
                                "annotations": {"meta.helm.sh/release-name": "shop",
                                                "meta.helm.sh/release-namespace": "shop"}},
                   "data": {"REGION": "eu-west", "LOG_LEVEL": "info"}}]

    # the release: what Git declares and what Helm applied are the same; live differs where it differs
    values = {a: {"image": {"tag": t}, "resources": {"requests": {"cpu": c, "memory": mem(m)}},
                  "replicaCount": r} for a, (t, c, m, r) in APPS.items()}
    values["reportExport"] = {"schedule": "*/10 * * * *", "concurrencyPolicy": cron_policy}
    applied = [deployment(a, t, c, m, r, r) for a, (t, c, m, r) in APPS.items()]
    for d in applied:
        d.pop("status")
        if d["metadata"]["name"] in ("catalog-api", "search-api"):     # the HPA owns the replica count
            d["spec"].pop("replicas")

    alerts = [alert("CPUThrottlingHigh", "info",
                    {"namespace": "monitoring", "pod": other_pods[2]["metadata"]["name"], "container": "prometheus-server"},
                    "Processes experience elevated CPU throttling.",
                    "31.2% throttling of CPU in namespace monitoring for container prometheus-server.", -300)]
    if variant == "q1":
        alerts += [alert("KubeHpaMaxedOut", "warning", {"namespace": "shop", "horizontalpodautoscaler": "catalog-api"},
                         "HPA is running at max replicas",
                         "HPA shop/catalog-api has been running at max replicas for longer than 15 minutes.", 39),
                   alert("KubeQuotaFullyUsed", "info", {"namespace": "shop", "resource": "requests.memory",
                                                         "resourcequota": "shop-quota"},
                         "Namespace quota is fully used.",
                         "Namespace shop is using 97% of its requests.memory quota.", 40)]
    if variant == "q2":
        alerts += [alert("KubeHpaReplicasMismatch", "warning", {"namespace": "shop", "horizontalpodautoscaler": "search-api"},
                         "HPA has not matched desired number of replicas.",
                         "HPA shop/search-api has not matched the desired number of replicas for longer than 15 minutes.", 270),
                   alert("KubeQuotaFullyUsed", "info", {"namespace": "shop", "resource": "pods",
                                                         "resourcequota": "shop-quota"},
                         "Namespace quota is fully used.", "Namespace shop is using 100% of its pods quota.", 240),
                   alert("KubePodNotReady", "warning", {"namespace": "data", "pod": "reports-db-6c7d8b9f4-x2k9p"},
                         "Pod has been in a non-ready state for more than 15 minutes.",
                         "Pod data/reports-db-6c7d8b9f4-x2k9p has been in a non-ready state for longer than 15 minutes.", 15)]

    files = {
        "kube/deployments.yaml": as_list("Deployment", deployments + others),
        "kube/pods.yaml": as_list("Pod", pods + job_pods + other_pods),
        "kube/events.yaml": as_list("Event", sorted(events, key=lambda e: e["firstTimestamp"])),
        "kube/quota.yaml": quota,
        "kube/limitrange.yaml": limitrange,
        "kube/hpa.yaml": as_list("HorizontalPodAutoscaler", hpas),
        "kube/cronjob.yaml": cronjob,
        "kube/jobs.yaml": as_list("Job", jobs),
        "kube/services.yaml": as_list("Service", services),
        "kube/endpoints.yaml": endpoints,
        "kube/configmaps.yaml": as_list("ConfigMap", configmaps),
        "helm/values.yaml": values,
        "helm/release.json": values,
        "prometheus/alerts.json": alerts,
    }
    if variant == "q2":
        files["loki/streams.json"] = [
            {"stream": {"k8s_pod_name": p["metadata"]["name"], "k8s_namespace_name": "shop",
                        "k8s_container_name": "report-export"},
             "values": [[ns_epoch(k * 10 + t),
                         f"level=warn msg=\"waiting for database\" target=reports-db.data.svc.cluster.local:5432 "
                         f"err=\"connection refused\" attempt={n}"]
                        for n, t in enumerate(range(1, 31, 10), 1)]}
            for k, p in enumerate(job_pods[:3])]
    manifest = "---\n".join(yaml.safe_dump(d, sort_keys=False, width=120) for d in applied)

    expect = EXPECT[variant]
    write(CASE[variant], files, expect)
    (OUT / CASE[variant] / "helm" / "manifest.yaml").write_text(manifest)


CASE = {"q1": "q1_quota_filled_by_request_drift", "q2": "q2_quota_filled_by_stuck_jobs",
        "q0": "q0_quota_no_incident"}

SOURCES = ["prometheus-operator/runbooks@a685d14 KubeQuotaFullyUsed",
           "prometheus-operator/runbooks@a685d14 KubeHpaMaxedOut",
           "prometheus-operator/runbooks@a685d14 KubePodNotReady",
           "https://kubernetes.io/docs/concepts/policy/resource-quotas/",
           "https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/",
           "https://kubernetes.io/docs/concepts/workloads/controllers/cron-jobs/"]
COMMON = {"set": "S2", "family": "quota", "namespace": "shop", "release": "shop", "sources": SOURCES,
          "spec": "docs/s2/family-quota.md"}

EXPECT = {
    "q1": {**COMMON,
           "scenario": "A namespace memory quota refuses billing-worker's new pod; another workload filled it",
           "query": "why can't billing-worker roll out in shop",
           "expected_outcome": "cause",
           "cause_object": "Deployment/shop/catalog-api",
           "mechanism": "catalog-api's live CPU request (100m) is below its applied 500m, so its HPA reads five "
                        "times the utilization, scales it to its maximum of 20, and those replicas' 1Gi memory "
                        "requests fill the namespace's requests.memory quota",
           "chain": ["billing-worker's new pod is refused: exceeded quota on requests.memory",
                     "ResourceQuota/shop/shop-quota is at its requests.memory limit",
                     "20 replicas of Deployment/shop/catalog-api use most of it",
                     "HorizontalPodAutoscaler/shop/catalog-api scaled it from 4 to its max of 20",
                     "catalog-api's live CPU request is 100m against 500m applied"],
           "decoys": [{"object": "Deployment/shop/cart-api", "why_not": "runs a newer image patch than applied; every pod is Ready"},
                      {"object": "Pod/shop/billing-worker (old OOMKilled restart)", "why_not": "resolved hours before the rollout"},
                      {"object": "Pod/monitoring/prometheus-server (CPUThrottlingHigh)", "why_not": "another namespace, no quota involved"},
                      {"object": "LimitRange/shop/shop-limits", "why_not": "every container declares its own requests"}],
           "affected_resources": ["Deployment/shop/billing-worker"],
           "root_cause_concepts": ["catalog-api", "request", "hpa|autoscal|scaled|replicas"],
           "notes": "Secondary keyword grading only; primary grading is on cause_object and mechanism (protocol 002, A1)."},
    "q2": {**COMMON,
           "scenario": "A namespace pod quota refuses search-api's scale-up; Jobs that never finish filled it",
           "query": "why does search-api not scale up in shop",
           "expected_outcome": "cause",
           "cause_object": "CronJob/shop/report-export",
           "origin_object": "Service/data/reports-db",
           "mechanism": "report-export starts a Job every 10 minutes with concurrencyPolicy Allow; each waits on "
                        "reports-db, which has had no ready endpoint since T0, so none finishes and 25 Jobs fill "
                        "the namespace's pods quota",
           "partial_if_only": ["Service/data/reports-db"],
           "chain": ["search-api's new pods are refused: exceeded quota on pods",
                     "ResourceQuota/shop/shop-quota is at its pods limit",
                     "25 of those pods are Jobs of CronJob/shop/report-export, all Running",
                     "the CronJob runs every 10 minutes with concurrencyPolicy Allow",
                     "each Job waits on Service/data/reports-db, which has no ready endpoint"],
           "decoys": [{"object": "Deployment/shop/search-api (CPU above target)", "why_not": "the load the HPA reacts to"},
                      {"object": "Deployment/data/reports-db-proxy", "why_not": "rolled out two hours after the hang began"},
                      {"object": "Deployment/shop/notify-worker", "why_not": "rolled out at T0+2 h 30 min, healthy, unrelated to the quota"},
                      {"object": "Pod/monitoring/prometheus-server (CPUThrottlingHigh)", "why_not": "another namespace"}],
           "affected_resources": ["Deployment/shop/search-api"],
           "root_cause_concepts": ["report-export|cronjob|cron job", "concurrency|allow|accumulat|pile|never finish|keep running|still running|not finish"],
           "notes": "Naming reports-db alone is PARTIAL (decided 2026-11-06)."},
    "q0": {**COMMON,
           "scenario": "No incident: the same objects as q1 and q2, healthy, with the same harmless drifts",
           "query": "is something wrong in shop",
           "expected_outcome": "no_incident",
           "cause_object": None,
           "decoys": [{"object": "Deployment/shop/cart-api", "why_not": "newer image patch than applied; every pod is Ready"},
                      {"object": "Pod/shop/billing-worker (old OOMKilled restart)", "why_not": "resolved"},
                      {"object": "Pod/monitoring/prometheus-server (CPUThrottlingHigh)", "why_not": "another namespace, informational"},
                      {"object": "LimitRange/shop/shop-limits", "why_not": "every container declares its own requests"}],
           "affected_resources": [],
           "root_cause_contains": [],
           "notes": "Passes only on an explicit no-incident answer (protocol 002, A2)."},
}


if __name__ == "__main__":
    for v in ("q1", "q2", "q0"):
        build(v)
    print("written:", ", ".join(sorted(CASE.values())))
