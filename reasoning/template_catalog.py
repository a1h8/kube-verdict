"""
Community runbook template catalog.

Lightweight cosine-similarity matcher over a YAML or built-in template library.
Each template describes a known Kubernetes failure pattern with a validated
fix recipe.  Seeds: h001–h006 scenarios from the integration test bank.

Usage
─────
    from reasoning.template_catalog import TemplateCatalog

    catalog = TemplateCatalog()           # built-in seeds
    matches = catalog.match(query, top_k=3)
    for m in matches:
        print(m.score, m.template.fix_commands)

Custom templates can be loaded from a directory of YAML files:
    catalog = TemplateCatalog(catalog_dir=Path("knowledge/runbooks"))
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RunbookTemplate:
    id:                  str
    title:               str
    symptoms:            list[str]
    root_cause_pattern:  str
    fix_commands:        list[str]
    tags:                list[str] = field(default_factory=list)


@dataclass
class TemplateMatch:
    template: RunbookTemplate
    score:    float          # cosine similarity 0–1


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> dict[str, int]:
    tokens = re.findall(r'\b\w+\b', text.lower())
    freq: dict[str, int] = {}
    for t in tokens:
        freq[t] = freq.get(t, 0) + 1
    return freq


def _cosine(a: dict[str, int], b: dict[str, int]) -> float:
    dot    = sum(a.get(k, 0) * v for k, v in b.items())
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))
    if not norm_a or not norm_b:
        return 0.0
    return dot / (norm_a * norm_b)


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

class TemplateCatalog:

    def __init__(self, catalog_dir: Path | None = None) -> None:
        self._templates: list[RunbookTemplate] = []
        if catalog_dir and catalog_dir.is_dir():
            self._load_dir(catalog_dir)
        if not self._templates:
            self._templates = _builtin_templates()

    def _load_dir(self, directory: Path) -> None:
        try:
            import yaml
        except ImportError:
            return
        for f in sorted(directory.glob("*.yaml")):
            try:
                docs = yaml.safe_load(f.read_text())
                entries = docs if isinstance(docs, list) else [docs]
                for d in entries:
                    if isinstance(d, dict):
                        self._templates.append(RunbookTemplate(**d))
            except Exception:
                pass

    def match(self, query: str, top_k: int = 3) -> list[TemplateMatch]:
        """Return up to top_k templates ranked by cosine similarity to query."""
        q_vec = _tokenize(query)
        scored: list[TemplateMatch] = []
        for t in self._templates:
            combined = " ".join([t.title] + t.symptoms + [t.root_cause_pattern])
            t_vec    = _tokenize(combined)
            score    = _cosine(q_vec, t_vec)
            scored.append(TemplateMatch(template=t, score=round(score, 4)))
        scored.sort(key=lambda m: m.score, reverse=True)
        return scored[:top_k]

    def get(self, template_id: str) -> RunbookTemplate | None:
        return next((t for t in self._templates if t.id == template_id), None)

    def __len__(self) -> int:
        return len(self._templates)


# ---------------------------------------------------------------------------
# Seed templates (h001–h006)
# ---------------------------------------------------------------------------

def _builtin_templates() -> list[RunbookTemplate]:
    return [
        RunbookTemplate(
            id="h001", title="CrashLoopBackOff — OOMKilled",
            symptoms=["CrashLoopBackOff", "OOMKilled", "exit code 137", "memory limit"],
            root_cause_pattern="container memory limit too low; OOM killer terminates pod",
            fix_commands=[
                "helm upgrade {release} -n {ns} --set resources.limits.memory={limit}",
                "kubectl top pod {pod} -n {ns}",
            ],
            tags=["oom", "memory", "crashloop"],
        ),
        RunbookTemplate(
            id="h002", title="ImagePullBackOff — wrong tag or missing registry secret",
            symptoms=["ImagePullBackOff", "ErrImagePull", "401", "403", "image pull failed"],
            root_cause_pattern="image tag does not exist or imagePullSecret missing from namespace",
            fix_commands=[
                "kubectl create secret docker-registry {secret} -n {ns} --docker-server=REGISTRY --docker-username=USER --docker-password=TOKEN",
                "helm upgrade {release} -n {ns} --set image.tag={tag}",
            ],
            tags=["image", "registry", "secret", "pull"],
        ),
        RunbookTemplate(
            id="h003", title="Pending pod — Insufficient node resources",
            symptoms=["Pending", "Insufficient cpu", "Insufficient memory", "FailedScheduling", "no nodes available"],
            root_cause_pattern="no node has enough CPU or memory to schedule the pod; resource requests too high",
            fix_commands=[
                "kubectl describe nodes | grep -A5 'Allocated resources'",
                "helm upgrade {release} -n {ns} --set resources.requests.cpu={cpu} --set resources.requests.memory={mem}",
            ],
            tags=["scheduling", "resources", "pending", "cpu", "memory"],
        ),
        RunbookTemplate(
            id="h004", title="Pod stuck — missing Secret or ConfigMap dependency",
            symptoms=["CreateContainerConfigError", "secret not found", "configmap not found", "no such key"],
            root_cause_pattern="pod spec references a Secret or ConfigMap that does not exist in the namespace",
            fix_commands=[
                "kubectl create secret generic {secret} -n {ns} --from-literal=KEY=VALUE",
                "kubectl create configmap {cm} -n {ns} --from-literal=KEY=VALUE",
            ],
            tags=["secret", "configmap", "missing", "dependency"],
        ),
        RunbookTemplate(
            id="h005", title="Service unreachable — selector mismatch or no endpoints",
            symptoms=["connection refused", "no endpoints", "503", "timeout", "selector mismatch"],
            root_cause_pattern="Service selector does not match any pod labels; endpoints list is empty",
            fix_commands=[
                "kubectl get endpoints {svc} -n {ns}",
                "kubectl patch service {svc} -n {ns} --type=merge --patch '{\"spec\":{\"selector\":{\"app\":\"{app}\"}}}'",
            ],
            tags=["service", "selector", "endpoints", "networking"],
        ),
        RunbookTemplate(
            id="h006", title="PVC stuck Pending — StorageClass missing or no provisioner",
            symptoms=["Pending", "FailedMount", "no persistent volumes available", "storageclass not found", "provisioner"],
            root_cause_pattern="PersistentVolumeClaim references a StorageClass that does not exist or has no provisioner",
            fix_commands=[
                "kubectl get storageclass",
                "kubectl patch pvc {pvc} -n {ns} --type=merge --patch '{\"spec\":{\"storageClassName\":\"{sc}\"}}'",
            ],
            tags=["pvc", "storage", "storageclass", "provisioner"],
        ),
    ]
