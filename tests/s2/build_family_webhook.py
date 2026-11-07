"""Builds S2's mutating webhook family (docs/s2/family-webhook.md): w1, w2 and their negative w0.

Written 2026-11-07 from the family spec, before any arm ran on these cases. Frozen by hash before
the first run (docs/s2-correlation-cases.md). Nothing in a fixture says which object is the cause.

    python tests/s2/build_family_webhook.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_family_quota import OUT, alert, as_list, event, ns_epoch, ts, write  # noqa: E402

NODES = ["worker-node-01", "worker-node-02", "worker-node-03"]
# name: (release, tag, replicas)
SHOP = {"cart-api": ("cart-api", "2.3.1", 2), "web": ("web", "4.2.0", 2), "orders-api": ("orders-api", "3.0.0", 2),
        "checkout": ("checkout", "1.9.2", 2)}
RS = {"cart-api": "5d6f8c7b9", "web": "8c7b6d5f9", "orders-api": "7f8c9d6b4", "checkout": "6d9f7c8b5",
      "mesh-injector": "84b7c6d5f", "coredns": "7db6d8ff4"}
NEW_RS = {"cart-api": "9c7d8b6f5", "checkout": "7b8c9d6f4"}
WEBHOOK_URL = "https://mesh-injector.mesh-system.svc:443/inject?timeout=10s"


def proxy_tag(variant: str, created: float) -> str:
    if variant == "w1" and created >= 0:
        return "1.21.0-rc"
    if variant == "w0":
        return "1.21.1"
    return "1.20.3"


def app_container(app: str, tag: str) -> dict:
    return {"name": app, "image": f"ghcr.io/acme/{app}:{tag}", "ports": [{"containerPort": 8080}],
            "resources": {"requests": {"cpu": "200m", "memory": "256Mi"}, "limits": {"memory": "512Mi"}}}


def proxy_container(tag: str) -> dict:
    return {"name": "mesh-proxy", "image": f"ghcr.io/acme/mesh-proxy:{tag}", "args": ["proxy", "sidecar"],
            "resources": {"requests": {"cpu": "50m", "memory": "64Mi"}, "limits": {"memory": "128Mi"}}}


def shop_pod(app: str, rs: str, i: int, tag: str, created: float, variant: str) -> dict:
    name = f"{app}-{rs}-{(i * 7919 + len(app) * 104729 + len(rs)) % 1048576:05x}"
    ptag = proxy_tag(variant, created)
    pulling = ptag == "1.21.0-rc"
    app_status = {"name": app, "image": f"ghcr.io/acme/{app}:{tag}", "ready": not pulling, "restartCount": 0,
                  "state": {"running": {"startedAt": ts(created + 0.5)}}, "lastState": {}}
    proxy_status = {"name": "mesh-proxy", "image": f"ghcr.io/acme/mesh-proxy:{ptag}", "ready": not pulling,
                    "restartCount": 0, "lastState": {},
                    "state": ({"waiting": {"reason": "ImagePullBackOff",
                                           "message": f"Back-off pulling image \"ghcr.io/acme/mesh-proxy:{ptag}\""}}
                              if pulling else {"running": {"startedAt": ts(created + 0.5)}})}
    return {"apiVersion": "v1", "kind": "Pod",
            "metadata": {"name": name, "namespace": "shop", "uid": f"pod-shop-{name}",
                         "creationTimestamp": ts(created),
                         "annotations": {"mesh.acme.io/injected": "true", "mesh.acme.io/proxy-version": ptag},
                         "labels": {"app.kubernetes.io/name": app, "pod-template-hash": rs},
                         "ownerReferences": [{"apiVersion": "apps/v1", "kind": "ReplicaSet", "name": f"{app}-{rs}",
                                              "controller": True}]},
            "spec": {"nodeName": NODES[i % 3], "containers": [app_container(app, tag), proxy_container(ptag)]},
            "status": {"phase": "Pending" if pulling else "Running",
                       "conditions": [{"type": "Ready", "status": "False" if pulling else "True"}],
                       "containerStatuses": [app_status, proxy_status]}}


def build(variant: str) -> None:
    namespaces = [{"apiVersion": "v1", "kind": "Namespace",
                   "metadata": {"name": n, "uid": f"ns-{n}", "labels": {"kubernetes.io/metadata.name": n, **lbl}}}
                  for n, lbl in (("shop", {"mesh-injection": "enabled"}), ("mesh-system", {}), ("kube-system", {}))]
    injector_chart = {"w1": "1.21.0", "w2": "1.20.3", "w0": "1.21.1"}[variant]
    injector_updated = {"w1": 0, "w2": -57600, "w0": -2000}[variant]
    webhook = {"apiVersion": "admissionregistration.k8s.io/v1", "kind": "MutatingWebhookConfiguration",
               "metadata": {"name": "mesh-injector", "uid": "mwc-mesh-injector",
                            "annotations": {"meta.helm.sh/release-name": "mesh-injector",
                                            "meta.helm.sh/release-namespace": "mesh-system"}},
               "webhooks": [{"name": "inject.mesh.acme.io", "admissionReviewVersions": ["v1"], "sideEffects": "None",
                             "failurePolicy": "Fail", "timeoutSeconds": 10,
                             "clientConfig": {"service": {"namespace": "mesh-system", "name": "mesh-injector",
                                                          "path": "/inject", "port": 443}},
                             "namespaceSelector": {"matchLabels": {"mesh-injection": "enabled"}},
                             "rules": [{"operations": ["CREATE"], "apiGroups": [""], "apiVersions": ["v1"],
                                        "resources": ["pods"]}]}]}

    deployments, pods, rss, events = [], [], [], []
    for app, (rel, tag, n) in SHOP.items():
        new_tag = {"cart-api": "2.4.0", "checkout": "2.0.0"}.get(app)
        rolling = (app == "cart-api" and variant in ("w1", "w0")) or (app == "checkout" and variant == "w2")
        dtag = new_tag if rolling else tag
        conditions = [{"type": "Available", "status": "True"}]
        if rolling and variant == "w2":
            conditions += [{"type": "ReplicaFailure", "status": "True", "reason": "FailedCreate",
                            "message": "Internal error occurred: failed calling webhook \"inject.mesh.acme.io\""}]
        deployments.append({
            "apiVersion": "apps/v1", "kind": "Deployment",
            "metadata": {"name": app, "namespace": "shop", "uid": f"dep-shop-{app}",
                         "labels": {"app.kubernetes.io/name": app, "app.kubernetes.io/managed-by": "Helm"},
                         "annotations": {"meta.helm.sh/release-name": rel, "meta.helm.sh/release-namespace": "shop"}},
            "spec": {"replicas": n, "selector": {"matchLabels": {"app.kubernetes.io/name": app}},
                     "template": {"metadata": {"labels": {"app.kubernetes.io/name": app}},
                                  "spec": {"containers": [app_container(app, dtag)]}}},
            "status": {"replicas": n, "readyReplicas": n, "availableReplicas": n,
                       "updatedReplicas": 0 if rolling and variant != "w0" else n, "conditions": conditions}})
        old_created = -6000
        for i in range(n):
            created = old_created
            if app == "web" and variant == "w1" and i == 1:
                created = 25                                   # replacement of an evicted pod
            if rolling and variant == "w0":
                pods.append(shop_pod(app, NEW_RS[app], i, dtag, 20 + i, variant))
                continue
            pods.append(shop_pod(app, RS[app], i, tag, created, variant))
        rss.append({"apiVersion": "apps/v1", "kind": "ReplicaSet",
                    "metadata": {"name": f"{app}-{RS[app]}", "namespace": "shop",
                                 "ownerReferences": [{"kind": "Deployment", "name": app, "controller": True}]},
                    "spec": {"replicas": 0 if (rolling and variant == "w0") else n},
                    "status": {"replicas": 0 if (rolling and variant == "w0") else n}})
        if rolling:
            rss.append({"apiVersion": "apps/v1", "kind": "ReplicaSet",
                        "metadata": {"name": f"{app}-{NEW_RS[app]}", "namespace": "shop",
                                     "ownerReferences": [{"kind": "Deployment", "name": app, "controller": True}]},
                        "spec": {"replicas": 1 if variant != "w0" else n},
                        "status": {"replicas": 0 if variant == "w2" else (1 if variant == "w1" else n)}})
            if variant == "w1":
                pods.append(shop_pod(app, NEW_RS[app], 0, dtag, 20, variant))
            events.append(event("Deployment", app, "ScalingReplicaSet",
                                f"Scaled up replica set {app}-{NEW_RS[app]} to 1", 20 if variant != "w2" else 15,
                                component="deployment-controller"))

    # the injector
    inj_tag = injector_chart
    pods.append({"apiVersion": "v1", "kind": "Pod",
                 "metadata": {"name": "mesh-injector-84b7c6d5f-q7k2m", "namespace": "mesh-system",
                              "uid": "pod-mesh-system-mesh-injector",
                              "labels": {"app.kubernetes.io/name": "mesh-injector", "pod-template-hash": RS["mesh-injector"]},
                              "ownerReferences": [{"apiVersion": "apps/v1", "kind": "ReplicaSet",
                                                   "name": f"mesh-injector-{RS['mesh-injector']}", "controller": True}]},
                 "spec": {"containers": [{"name": "injector", "image": f"ghcr.io/acme/mesh-injector:{inj_tag}",
                                          "args": ["--tls-cert=/etc/tls/tls.crt", "--tls-key=/etc/tls/tls.key"],
                                          "volumeMounts": [{"name": "tls", "mountPath": "/etc/tls"}]}],
                          "volumes": [{"name": "tls", "secret": {"secretName": "mesh-injector-tls"}}]},
                 "status": {"phase": "Running", "podIP": "10.42.2.61",
                            "conditions": [{"type": "Ready", "status": "True"}],
                            "containerStatuses": [{"name": "injector", "image": f"ghcr.io/acme/mesh-injector:{inj_tag}",
                                                   "ready": True, "restartCount": 0,
                                                   "state": {"running": {"startedAt": ts(injector_updated)}},
                                                   "lastState": {}}]}})
    proxy_default = {"w1": "1.21.0-rc", "w2": "1.20.3", "w0": "1.21.1"}[variant]
    injector_dep = {"apiVersion": "apps/v1", "kind": "Deployment",
                    "metadata": {"name": "mesh-injector", "namespace": "mesh-system", "uid": "dep-mesh-system-mesh-injector",
                                 "labels": {"app.kubernetes.io/name": "mesh-injector", "app.kubernetes.io/managed-by": "Helm"},
                                 "annotations": {"meta.helm.sh/release-name": "mesh-injector",
                                                 "meta.helm.sh/release-namespace": "mesh-system"}},
                    "spec": {"replicas": 1, "selector": {"matchLabels": {"app.kubernetes.io/name": "mesh-injector"}},
                             "template": {"metadata": {"labels": {"app.kubernetes.io/name": "mesh-injector"}},
                                          "spec": {"containers": [{"name": "injector",
                                                                   "image": f"ghcr.io/acme/mesh-injector:{inj_tag}",
                                                                   "args": ["--tls-cert=/etc/tls/tls.crt",
                                                                            "--tls-key=/etc/tls/tls.key",
                                                                            f"--proxy-image=ghcr.io/acme/mesh-proxy:{proxy_default}"]}]}}},
                    "status": {"replicas": 1, "readyReplicas": 1, "availableReplicas": 1, "updatedReplicas": 1,
                               "conditions": [{"type": "Available", "status": "True"}]}}
    deployments.append(injector_dep)
    deployments.append({"apiVersion": "apps/v1", "kind": "Deployment",
                        "metadata": {"name": "coredns", "namespace": "kube-system", "uid": "dep-kube-system-coredns"},
                        "spec": {"replicas": 2, "selector": {"matchLabels": {"app.kubernetes.io/name": "coredns"}},
                                 "template": {"metadata": {"labels": {"app.kubernetes.io/name": "coredns"}},
                                              "spec": {"containers": [{"name": "coredns", "image": "registry.k8s.io/coredns/coredns:v1.11.3"}]}}},
                        "status": {"replicas": 2, "readyReplicas": 2, "availableReplicas": 2,
                                   "conditions": [{"type": "Available", "status": "True"}]}})
    for i in range(2):
        pods.append({"apiVersion": "v1", "kind": "Pod",
                     "metadata": {"name": f"coredns-7db6d8ff4-{'ab12c' if i == 0 else 'de34f'}", "namespace": "kube-system",
                                  "uid": f"pod-kube-system-coredns-{i}", "labels": {"app.kubernetes.io/name": "coredns"},
                                  "ownerReferences": [{"apiVersion": "apps/v1", "kind": "ReplicaSet",
                                                       "name": "coredns-7db6d8ff4", "controller": True}]},
                     "spec": {"containers": [{"name": "coredns", "image": "registry.k8s.io/coredns/coredns:v1.11.3"}]},
                     "status": {"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}],
                                "containerStatuses": [{"name": "coredns", "image": "registry.k8s.io/coredns/coredns:v1.11.3",
                                                       "ready": True, "restartCount": 0,
                                                       "state": {"running": {"startedAt": ts(-30000)}}, "lastState": {}}]}})

    services = [{"apiVersion": "v1", "kind": "Service",
                 "metadata": {"name": a, "namespace": "shop", "uid": f"svc-shop-{a}"},
                 "spec": {"selector": {"app.kubernetes.io/name": a}, "ports": [{"port": 80, "targetPort": 8080}]}}
                for a in SHOP] + [
                {"apiVersion": "v1", "kind": "Service",
                 "metadata": {"name": "mesh-injector", "namespace": "mesh-system", "uid": "svc-mesh-system-mesh-injector"},
                 "spec": {"selector": {"app.kubernetes.io/name": "mesh-injector"},
                          "ports": [{"name": "https", "port": 443, "targetPort": 8443}]}}]
    endpoints = [{"apiVersion": "v1", "kind": "Endpoints", "metadata": {"name": "mesh-injector", "namespace": "mesh-system"},
                  "subsets": [{"addresses": [{"ip": "10.42.2.61", "targetRef": {"kind": "Pod",
                                                                               "name": "mesh-injector-84b7c6d5f-q7k2m"}}],
                               "ports": [{"name": "https", "port": 8443}]}]}]

    expired = variant == "w2"
    secrets = [{"apiVersion": "v1", "kind": "Secret", "type": "kubernetes.io/tls",
                "metadata": {"name": "mesh-injector-tls", "namespace": "mesh-system", "uid": "secret-mesh-injector-tls",
                             "annotations": {"cert-manager.io/certificate-name": "mesh-injector-tls",
                                             "cert-manager.io/issuer-name": "mesh-ca"}}}]
    certificates = [{"apiVersion": "cert-manager.io/v1", "kind": "Certificate",
                     "metadata": {"name": "mesh-injector-tls", "namespace": "mesh-system", "uid": "cert-mesh-injector-tls"},
                     "spec": {"secretName": "mesh-injector-tls", "dnsNames": ["mesh-injector.mesh-system.svc"],
                              "issuerRef": {"name": "mesh-ca", "kind": "Issuer"}},
                     "status": {"notAfter": ts(0 if expired else 40000), "renewalTime": ts(-1440 if expired else 30000),
                                "conditions": [{"type": "Ready", "status": "False" if expired else "True",
                                                "reason": "Expired" if expired else "Ready",
                                                "message": ("Certificate expired on " + ts(0)) if expired
                                                else "Certificate is up to date and has not expired"}]}},
                    {"apiVersion": "cert-manager.io/v1", "kind": "Certificate",
                     "metadata": {"name": "shop-tls", "namespace": "shop", "uid": "cert-shop-tls"},
                     "spec": {"secretName": "shop-tls", "dnsNames": ["shop.acme.example"],
                              "issuerRef": {"name": "letsencrypt", "kind": "ClusterIssuer"}},
                     "status": {"notAfter": ts(5 + 90 * 1440), "conditions": [{"type": "Ready", "status": "True",
                                                                              "reason": "Ready"}]}}]
    issuers = [] if expired else [{"apiVersion": "cert-manager.io/v1", "kind": "Issuer",
                                   "metadata": {"name": "mesh-ca", "namespace": "mesh-system"},
                                   "spec": {"ca": {"secretName": "mesh-ca-root"}},
                                   "status": {"conditions": [{"type": "Ready", "status": "True"}]}}]

    events.append(event("Certificate", "shop-tls", "Issuing", "The certificate has been successfully issued", 5,
                        component="cert-manager-certificates-issuing"))
    if variant == "w1":
        for p in pods:
            if p["status"]["phase"] == "Pending":
                events += [event("Pod", p["metadata"]["name"], "Failed",
                                 "Failed to pull image \"ghcr.io/acme/mesh-proxy:1.21.0-rc\": rpc error: code = NotFound "
                                 "desc = failed to pull and unpack image \"ghcr.io/acme/mesh-proxy:1.21.0-rc\": not found",
                                 21, 58, count=9, etype="Warning", component="kubelet"),
                           event("Pod", p["metadata"]["name"], "BackOff",
                                 "Back-off pulling image \"ghcr.io/acme/mesh-proxy:1.21.0-rc\"", 22, 59, count=30,
                                 etype="Warning", component="kubelet")]
        events.append(event("Pod", "web-8c7b6d5f9-evicted", "Evicted",
                            "The node was low on resource: ephemeral-storage.", 25, etype="Warning", component="kubelet"))
        events.append(event("Deployment", "mesh-injector", "ScalingReplicaSet",
                            "Scaled up replica set mesh-injector-84b7c6d5f to 1", 0, ns="mesh-system",
                            component="deployment-controller"))
    if variant == "w2":
        msg = (f"Error creating: Internal error occurred: failed calling webhook \"inject.mesh.acme.io\": failed to call "
               f"webhook: Post \"{WEBHOOK_URL}\": tls: failed to verify certificate: x509: certificate has expired or "
               f"is not yet valid: current time {ts(15)} is after {ts(0)}")
        events += [event("ReplicaSet", f"checkout-{NEW_RS['checkout']}", "FailedCreate", msg, 15, 58, count=17,
                         etype="Warning", component="replicaset-controller"),
                   event("Job", "report-export-29342410", "FailedCreate", msg.replace(ts(15), ts(30)), 30, 58, count=6,
                         etype="Warning", component="job-controller"),
                   event("Certificate", "mesh-injector-tls", "Issuing",
                         "Issuing certificate as Secret will expire in 24h: issuer mesh-ca not found", -1440, -60, count=24,
                         ns="mesh-system", etype="Warning", component="cert-manager-certificates-trigger")]
    quota = {"apiVersion": "v1", "kind": "ResourceQuota",
             "metadata": {"name": "shop-quota", "namespace": "shop", "uid": "rq-shop-quota"},
             "spec": {"hard": {"pods": "40"}}, "status": {"hard": {"pods": "40"}, "used": {"pods": "20"}}}

    streams = [{"stream": {"k8s_pod_name": "mesh-injector-84b7c6d5f-q7k2m", "k8s_namespace_name": "mesh-system",
                           "k8s_container_name": "injector"},
                "values": ([[ns_epoch(16 + t), "http: TLS handshake error from 10.42.0.1:51234: remote error: tls: bad certificate"]
                            for t in (0, 9, 20)] if expired else
                           [[ns_epoch(21 + t), f"level=info msg=\"injected sidecar\" proxy=ghcr.io/acme/mesh-proxy:{proxy_default}"]
                            for t in (0, 5)])}]

    alerts = []
    if variant == "w1":
        alerts = [alert("KubeContainerWaiting", "warning", {"namespace": "shop", "pod": p["metadata"]["name"],
                                                            "container": "mesh-proxy"},
                        "Pod container waiting longer than 1 hour",
                        f"pod/{p['metadata']['name']} in namespace shop on container mesh-proxy has been in waiting state.", 50)
                  for p in pods if p["status"]["phase"] == "Pending"]
    if variant == "w2":
        alerts = [alert("KubeDeploymentReplicasMismatch", "warning", {"namespace": "shop", "deployment": "checkout"},
                        "Deployment has not matched the expected number of replicas.",
                        "Deployment shop/checkout has not matched the expected number of replicas for longer than 15 minutes.", 32)]

    history = [{"revision": 7, "updated": ts(-57600), "status": "superseded" if variant != "w2" else "deployed",
                "chart": "mesh-injector-1.20.3", "description": "Upgrade complete"}]
    if variant != "w2":
        history.append({"revision": 8, "updated": ts(injector_updated), "status": "deployed",
                        "chart": f"mesh-injector-{injector_chart}", "description": "Upgrade complete"})
    values = {"image": {"tag": injector_chart}, "tls": {"secretName": "mesh-injector-tls", "issuer": "mesh-ca"}}
    applied = {k: v for k, v in injector_dep.items() if k != "status"}
    manifest = yaml.safe_dump(applied, sort_keys=False, width=100000) + "---\n" + yaml.safe_dump(webhook, sort_keys=False, width=100000)

    files = {"kube/namespaces.yaml": as_list("Namespace", namespaces),
             "kube/mutatingwebhookconfigurations.yaml": as_list("MutatingWebhookConfiguration", [webhook]),
             "kube/deployments.yaml": as_list("Deployment", deployments),
             "kube/replicasets.yaml": as_list("ReplicaSet", rss),
             "kube/pods.yaml": as_list("Pod", pods),
             "kube/services.yaml": as_list("Service", services),
             "kube/endpoints.yaml": as_list("Endpoints", endpoints),
             "kube/secrets.yaml": as_list("Secret", secrets),
             "kube/certificates.yaml": as_list("Certificate", certificates),
             "kube/issuers.yaml": as_list("Issuer", issuers),
             "kube/quota.yaml": quota,
             "kube/events.yaml": as_list("Event", sorted(events, key=lambda e: e["firstTimestamp"])),
             "helm/values.yaml": values, "helm/release.json": values, "helm/history.json": history,
             "loki/streams.json": streams, "prometheus/alerts.json": alerts}
    write(CASE[variant], files, {**COMMON, **EXPECT[variant]})
    (OUT / CASE[variant] / "helm" / "manifest.yaml").write_text(manifest)


CASE = {"w1": "w1_webhook_injects_missing_image", "w2": "w2_webhook_fails_closed_expired_cert",
        "w0": "w0_webhook_no_incident"}
SOURCES = ["https://kubernetes.io/docs/reference/access-authn-authz/extensible-admission-controllers/",
           "prometheus-operator/runbooks@a685d14 KubeContainerWaiting",
           "prometheus-operator/runbooks@a685d14 KubeDeploymentReplicasMismatch"]
COMMON = {"set": "S2", "family": "webhook", "namespace": "shop", "release": "mesh-injector", "sources": SOURCES,
          "spec": "docs/s2/family-webhook.md"}
EXPECT = {
    "w1": {"scenario": "new pods of two releases cannot pull an injected sidecar image; the injector's upgrade set it",
           "query": "why are new pods of cart-api and web stuck in shop", "expected_outcome": "cause",
           "cause_object": "Deployment/mesh-system/mesh-injector",
           "mechanism": "since its T0 upgrade (chart 1.21.0) the injector injects ghcr.io/acme/mesh-proxy:1.21.0-rc, a tag "
                        "that does not exist, into every new pod of the namespaces its webhook selects",
           "pass_if": "names the injector or its webhook as the source of the mesh-proxy container with the bad image",
           "partial_if_only": ["an image cannot be pulled, without where the container comes from"],
           "chain": ["new pods of cart-api and web are ImagePullBackOff on mesh-proxy:1.21.0-rc",
                     "mesh-proxy is in no chart; the pods are annotated mesh.acme.io/injected",
                     "MutatingWebhookConfiguration mesh-injector selects mesh-injection=enabled, which shop is",
                     "the injector, upgraded at T0, sets --proxy-image=...:1.21.0-rc"],
           "decoys": [{"object": "Deployment/shop/cart-api (redeployed)", "why_not": "created the new pod; its own tag exists"},
                      {"object": "Certificate/shop/shop-tls", "why_not": "renewed and Ready"},
                      {"object": "Deployment/shop/orders-api", "why_not": "its pods predate T0 and run"}],
           "affected_resources": ["Deployment/shop/cart-api", "Deployment/shop/web"],
           "root_cause_concepts": ["inject|webhook|mesh-injector|sidecar", "1.21.0-rc|proxy image|mesh-proxy"]},
    "w2": {"scenario": "pod creations are refused; the webhook fails closed because its certificate expired",
           "query": "why does the checkout rollout not start in shop", "expected_outcome": "cause",
           "cause_object": "Secret/mesh-system/mesh-injector-tls",
           "mechanism": "the injector's serving certificate expired at T0 and was not renewed (its issuer mesh-ca is gone), "
                        "so the API server's calls to the webhook fail TLS verification, and the webhook, set to "
                        "failurePolicy Fail, rejects every pod creation in shop",
           "pass_if": "names the expired certificate, or the webhook failing closed because of it",
           "partial_if_only": ["a webhook rejects the pods, without why"],
           "chain": ["checkout's new ReplicaSet and a Job fail to create pods: failed calling webhook, certificate expired",
                     "MutatingWebhookConfiguration mesh-injector has failurePolicy Fail",
                     "the injector runs and is reachable; its TLS handshakes fail",
                     "Certificate mesh-injector-tls expired at T0; its issuer mesh-ca was not found"],
           "decoys": [{"object": "Deployment/shop/checkout 2.0.0", "why_not": "the version the rollout ships"},
                      {"object": "ResourceQuota/shop/shop-quota", "why_not": "at 50 %"},
                      {"object": "Deployment/mesh-system/mesh-injector", "why_not": "last upgraded 40 days ago, running"}],
           "affected_resources": ["Deployment/shop/checkout"],
           "root_cause_concepts": ["certificate|cert|tls|x509", "expired|expir"]},
    "w0": {"scenario": "No incident: the injector injects an image that exists, its certificate is valid",
           "query": "is something wrong in shop", "expected_outcome": "no_incident", "cause_object": None,
           "decoys": [{"object": "Deployment/shop/cart-api (redeployed)", "why_not": "rolled out, Ready"},
                      {"object": "Certificate/shop/shop-tls", "why_not": "renewed"},
                      {"object": "ResourceQuota/shop/shop-quota", "why_not": "at 50 %"}],
           "affected_resources": [], "root_cause_contains": [],
           "notes": "Passes only on an explicit no-incident answer (protocol 002, A2)."},
}

if __name__ == "__main__":
    for v in ("w1", "w2", "w0"):
        build(v)
    print("written:", ", ".join(sorted(CASE.values())))
