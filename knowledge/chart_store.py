"""
Enterprise chart / expected-state store.

Versioned "expected state" sources are pushed into KubeVerdict and persisted
under ./data/charts/<name>/<version>/ so the expected manifests can be
reconstructed at the *exact* version that is (or should be) deployed.

The chart version is part of the RCA evidence, not metadata: a different
version renders a different expected manifest, so a version-less source would
diff against the wrong baseline and the verdict would be wrong.

Compatible with all deployment modes. A source has a ``render_type``:
  - ``helm``      — a Helm chart directory (Chart.yaml)            → helm template
  - ``helmfile``  — a Helmfile bundle (helmfile.yaml + charts)     → per-release render
  - ``kustomize`` — a Kustomize overlay (kustomization.yaml)       → kustomize build
  - ``manifests`` — already-rendered / raw YAML — the universal    → used as-is
                    catch-all for any other tool's output
                    (Jsonnet/Tanka, CDK8s, ArgoCD/Flux-rendered…)
"""
from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

_DEFAULT_DIR = Path("./data/charts")

RenderType = str  # "helm" | "helmfile" | "manifests"

_META_FILE = ".kv-meta.json"


@dataclass
class EnterpriseChart:
    name: str = ""
    version: str = ""
    render_type: RenderType = "helm"
    source: str = "upload"          # upload | git | repo
    tags: list[str] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def id(self) -> str:
        return f"{self.name}@{self.version}"


# Required marker file(s) per render_type — push() rejects sources that lack them.
# A tuple means "any one of these"; None means "any *.yaml accepted".
_REQUIRED_MARKER: dict[str, str | tuple[str, ...] | None] = {
    "helm":      "Chart.yaml",
    "helmfile":  "helmfile.yaml",
    "kustomize": ("kustomization.yaml", "kustomization.yml", "Kustomization"),
    "manifests": None,              # any *.yaml accepted
}


class ChartStore:
    """File-backed store for versioned enterprise expected-state sources."""

    def __init__(self, data_dir: Path = _DEFAULT_DIR) -> None:
        self._dir = Path(data_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _chart_dir(self, name: str, version: str) -> Path:
        return self._dir / name / version

    # ── Push ──────────────────────────────────────────────────────────────────

    def push(
        self,
        name: str,
        version: str,
        chart_src: Path | str,
        render_type: RenderType = "helm",
        source: str = "upload",
        tags: list[str] | None = None,
    ) -> EnterpriseChart:
        """
        Copy an expected-state source directory into the store under
        ``<name>/<version>/``. Version is mandatory — a source without a version
        is rejected, because the rendered baseline would be ambiguous.
        """
        if not name or not version:
            raise ValueError("name and version are both required (version is evidence)")
        if render_type not in _REQUIRED_MARKER:
            raise ValueError(f"unknown render_type {render_type!r}")

        src = Path(chart_src)
        if not src.is_dir():
            raise ValueError(f"{src} is not a directory")

        marker = _REQUIRED_MARKER[render_type]
        if marker is None:  # manifests — require at least one YAML doc
            if not any(src.rglob("*.yaml")) and not any(src.rglob("*.yml")):
                raise ValueError(f"{src} contains no manifests (*.yaml)")
        else:
            markers = (marker,) if isinstance(marker, str) else marker
            if not any((src / m).exists() for m in markers):
                raise ValueError(
                    f"{src} is not a {render_type} source (no {' / '.join(markers)})"
                )

        dest = self._chart_dir(name, version)
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)

        meta = EnterpriseChart(
            name=name, version=version, render_type=render_type,
            source=source, tags=tags or [],
        )
        (dest / _META_FILE).write_text(json.dumps(asdict(meta), indent=2))
        return meta

    # ── Read ──────────────────────────────────────────────────────────────────

    def path(self, name: str, version: str) -> Path | None:
        d = self._chart_dir(name, version)
        return d if (d / _META_FILE).exists() else None

    def get(self, name: str, version: str) -> EnterpriseChart | None:
        meta = self._chart_dir(name, version) / _META_FILE
        if not meta.exists():
            return None
        try:
            return EnterpriseChart(**json.loads(meta.read_text()))
        except Exception:
            return None

    def versions(self, name: str) -> list[str]:
        base = self._dir / name
        if not base.is_dir():
            return []
        return sorted(p.name for p in base.iterdir() if (p / _META_FILE).exists())

    def list(self) -> list[EnterpriseChart]:
        out: list[EnterpriseChart] = []
        if not self._dir.is_dir():
            return out
        for name_dir in sorted(self._dir.iterdir()):
            if not name_dir.is_dir():
                continue
            for ver_dir in sorted(name_dir.iterdir()):
                meta = ver_dir / _META_FILE
                if not meta.exists():
                    continue
                try:
                    out.append(EnterpriseChart(**json.loads(meta.read_text())))
                except Exception:
                    pass
        return out

    def delete(self, name: str, version: str) -> bool:
        d = self._chart_dir(name, version)
        if d.exists():
            shutil.rmtree(d)
            return True
        return False
