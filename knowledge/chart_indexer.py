"""
Enterprise chart / expected-state indexer.

Reconstructs the expected manifests from a stored source **at its pinned
version** and indexes each rendered resource as a duck-typed anchor entity in
the FAISS store, so the expected state is retrievable as RCA evidence next to
live cluster state.

Render backends — compatible with all deployment modes:
  - ``helm``      → ManifestRenderer (`helm template`, version-pinned)
  - ``helmfile``  → render each release's chart (helmfile.yaml parsed directly)
  - ``kustomize`` → `kustomize build` (or `kubectl kustomize` fallback)
  - ``manifests`` → already-rendered / raw YAML, used as-is — the universal
                    catch-all for any other tool (Jsonnet/Tanka, CDK8s,
                    ArgoCD/Flux-rendered output); no binary required

The chart version is baked into every anchor — render output is
version-specific, so the version is part of the evidence, not metadata.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

import yaml

from ingestion.manifest_renderer import ManifestRenderer
from knowledge.chart_store import ChartStore, EnterpriseChart

log = logging.getLogger(__name__)


# ── Duck-typed entity FAISSStore.add_entity() accepts ─────────────────────────

class _ChartAnchorChunk:
    """Minimal entity interface for FAISS indexing — no K8sEntity inheritance."""

    def __init__(
        self, uid: str, name: str, chart: str, version: str,
        resource_kind: str, resource_name: str, fields: str,
    ) -> None:
        self.uid       = uid
        self.name      = name
        self.namespace = "enterprise-charts"
        self.kind      = "EnterpriseChart"   # plain string — to_text() handles it
        self._chart    = chart
        self._version  = version
        self._rkind    = resource_kind
        self._rname    = resource_name
        self._fields   = fields

    def to_text(self) -> str:
        return (
            f"kind=EnterpriseChart chart={self._chart} version={self._version} "
            f"resource={self._rkind}/{self._rname} {self._fields}"
        ).strip()


# ── Field summary (the declared fields that matter for drift) ─────────────────

def _summarize_fields(resource: dict) -> str:
    spec = resource.get("spec", {}) or {}
    parts: list[str] = []
    if "replicas" in spec:
        parts.append(f"spec.replicas={spec['replicas']}")
    containers = (
        (spec.get("template", {}) or {}).get("spec", {}).get("containers", [])
        or spec.get("containers", [])
    )
    for c in containers or []:
        if c.get("image"):
            parts.append(f"image={c['image']}")
        res = c.get("resources", {}) or {}
        for scope in ("limits", "requests"):
            for k, v in (res.get(scope, {}) or {}).items():
                parts.append(f"{scope}.{k}={v}")
    return " ".join(parts)


# ── Render backends ───────────────────────────────────────────────────────────

def _render_manifests(path: Path) -> list[dict]:
    """Raw / customised YAML used as-is (Kustomize output, committed manifests)."""
    docs: list[dict] = []
    for f in sorted(list(path.rglob("*.yaml")) + list(path.rglob("*.yml"))):
        try:
            for doc in yaml.safe_load_all(f.read_text()):
                if isinstance(doc, dict) and doc.get("kind"):
                    docs.append(doc)
        except yaml.YAMLError:
            log.warning("chart_indexer: skipping unparseable %s", f)
    return docs


def _parse_multidoc(text: str) -> list[dict]:
    return [d for d in yaml.safe_load_all(text) if isinstance(d, dict) and d.get("kind")]


def _render_kustomize(path: Path) -> list[dict]:
    """`kustomize build` (or `kubectl kustomize` fallback) for Kustomize overlays."""
    if shutil.which("kustomize"):
        cmd = ["kustomize", "build", str(path)]
    elif shutil.which("kubectl"):
        cmd = ["kubectl", "kustomize", str(path)]
    else:
        log.warning("chart_indexer: neither kustomize nor kubectl available")
        return []
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, check=True, timeout=60,
        ).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        stderr = getattr(exc, "stderr", "") or ""
        log.warning("chart_indexer: kustomize build failed: %s", stderr.strip()[:200])
        return []
    return _parse_multidoc(out)


def _render_helmfile(path: Path, namespace: str, renderer: ManifestRenderer) -> list[dict]:
    """Render every release declared in helmfile.yaml via its chart."""
    hf = path / "helmfile.yaml"
    try:
        spec = yaml.safe_load(hf.read_text()) or {}
    except yaml.YAMLError:
        log.warning("chart_indexer: unparseable helmfile %s", hf)
        return []

    docs: list[dict] = []
    for rel in spec.get("releases", []) or []:
        chart_ref = rel.get("chart", "")
        chart_dir = (path / chart_ref) if chart_ref else None
        if chart_dir is None or not (chart_dir / "Chart.yaml").exists():
            log.warning("chart_indexer: helmfile release %s chart not found (%s)",
                        rel.get("name"), chart_ref)
            continue
        value_files = [str(path / vf) for vf in rel.get("values", []) if isinstance(vf, str)]
        docs.extend(renderer.render(
            str(chart_dir),
            release_name=rel.get("name", chart_dir.name),
            namespace=rel.get("namespace", namespace),
            value_files=value_files or None,
        ))
    return docs


# ── Indexer ───────────────────────────────────────────────────────────────────

class ChartIndexer:
    """Render + index versioned enterprise expected-state sources into a FAISSStore."""

    def __init__(self, store, renderer: ManifestRenderer | None = None) -> None:
        self._store = store
        self._renderer = renderer or ManifestRenderer()

    def render(self, chart_store: ChartStore, chart: EnterpriseChart,
               namespace: str = "default") -> list[dict]:
        """Reconstruct the expected manifests for a stored source at its version."""
        path = chart_store.path(chart.name, chart.version)
        if path is None:
            log.warning("chart_indexer: %s not in store", chart.id)
            return []

        if chart.render_type == "manifests":
            return _render_manifests(path)
        if chart.render_type == "kustomize":
            return _render_kustomize(path)
        if chart.render_type == "helmfile":
            return _render_helmfile(path, namespace, self._renderer)
        # default: helm — version-pinned render
        return self._renderer.render(
            str(path), release_name=chart.name, namespace=namespace,
            chart_version=chart.version,
        )

    def index_chart(self, chart_store: ChartStore, chart: EnterpriseChart,
                    namespace: str = "default") -> int:
        rendered = self.render(chart_store, chart, namespace)
        n = 0
        for i, res in enumerate(rendered):
            rkind = res.get("kind", "")
            rname = (res.get("metadata", {}) or {}).get("name", "")
            entity = _ChartAnchorChunk(
                uid=f"chart-{chart.name}-{chart.version}-{i}",
                name=f"{chart.name}@{chart.version} {rkind}/{rname}",
                chart=chart.name, version=chart.version,
                resource_kind=rkind, resource_name=rname,
                fields=_summarize_fields(res),
            )
            self._store.add_entity(entity)
            n += 1
        log.info("chart_indexer: %s → %d resource anchors", chart.id, n)
        return n

    def index_all(self, chart_store: ChartStore, namespace: str = "default") -> int:
        total = 0
        for chart in chart_store.list():
            total += self.index_chart(chart_store, chart, namespace)
        return total