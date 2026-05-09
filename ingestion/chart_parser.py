from __future__ import annotations
import copy
import logging
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Any

import yaml

from ontology.entities import ChartDependency, HelmChart

log = logging.getLogger(__name__)


class ChartParser:
    """
    Generic Helm chart parser.

    Handles:
    - Local chart directories
    - .tgz chart archives
    - Remote charts via `helm show` (no download needed)
    - Umbrella charts (Chart.yaml dependencies: + charts/ sub-directories)

    Returns HelmChart objects with full dependency tree and merged default values.
    """

    # ------------------------------------------------------------------
    # Entry points
    # ------------------------------------------------------------------

    def from_dir(self, path: Path) -> HelmChart | None:
        """Parse a chart from an unpacked directory."""
        chart_yaml = path / "Chart.yaml"
        values_yaml = path / "values.yaml"
        if not chart_yaml.exists():
            log.debug("No Chart.yaml at %s", path)
            return None
        return self._parse(
            chart_meta=self._load_yaml(chart_yaml) or {},
            default_values=self._load_yaml(values_yaml) or {},
            source_path=str(path),
            base_dir=path,
        )

    def from_tgz(self, path: Path) -> HelmChart | None:
        """Parse a chart from a .tgz archive."""
        with tempfile.TemporaryDirectory() as tmp:
            try:
                with tarfile.open(path, "r:gz") as tar:
                    tar.extractall(tmp)
            except (tarfile.TarError, OSError) as exc:
                log.warning("Cannot extract %s: %s", path, exc)
                return None
            # Chart is extracted into <tmp>/<chart-name>/
            extracted_dirs = [d for d in Path(tmp).iterdir() if d.is_dir()]
            if not extracted_dirs:
                return None
            return self.from_dir(extracted_dirs[0])

    def from_helm_show(
        self,
        chart_ref: str,
        version: str | None = None,
        repo: str | None = None,
    ) -> HelmChart | None:
        """
        Fetch chart metadata and default values via `helm show`
        without pulling the full chart archive.
        chart_ref: "repo/chart-name" or local path
        """
        chart_meta = self._helm_show("chart", chart_ref, version, repo)
        default_values = self._helm_show("values", chart_ref, version, repo)

        if not chart_meta:
            return None
        if not isinstance(chart_meta, dict):
            return None

        return self._parse(
            chart_meta=chart_meta,
            default_values=default_values if isinstance(default_values, dict) else {},
            source_path=chart_ref,
            base_dir=None,
        )

    # ------------------------------------------------------------------
    # Core parsing
    # ------------------------------------------------------------------

    def _parse(
        self,
        chart_meta: dict,
        default_values: dict,
        source_path: str,
        base_dir: Path | None,
    ) -> HelmChart:
        name = chart_meta.get("name", Path(source_path).name)
        version = chart_meta.get("version", "")
        uid = f"chart-{name}-{version}"

        raw_deps = chart_meta.get("dependencies") or []
        dependencies = [self._parse_dep(d) for d in raw_deps]

        is_umbrella = len(dependencies) > 0 or (
            base_dir is not None and (base_dir / "charts").is_dir()
            and any((base_dir / "charts").iterdir())
        )

        chart = HelmChart(
            uid=uid,
            name=name,
            namespace=None,
            labels={},
            annotations={},
            chart_version=version,
            chart_api_version=chart_meta.get("apiVersion", "v2"),
            description=chart_meta.get("description", ""),
            chart_type=chart_meta.get("type", "application"),
            is_umbrella=is_umbrella,
            dependencies=dependencies,
            default_values=default_values,
            source_path=source_path,
        )

        # Recurse into sub-charts (umbrella pattern)
        if base_dir is not None:
            chart._sub_charts = self._parse_sub_charts(base_dir, dependencies, default_values)
        else:
            chart._sub_charts = []

        return chart

    @staticmethod
    def _parse_dep(raw: dict) -> ChartDependency:
        return ChartDependency(
            name=raw.get("name", ""),
            version=raw.get("version", ""),
            repository=raw.get("repository", ""),
            alias=raw.get("alias", ""),
            condition=raw.get("condition", ""),
            tags=list(raw.get("tags") or []),
        )

    def _parse_sub_charts(
        self,
        base_dir: Path,
        dependencies: list[ChartDependency],
        parent_values: dict,
    ) -> list[HelmChart]:
        """
        Parses charts/ sub-directory for umbrella charts.
        Each sub-chart gets the values scoped to its key in the parent values.
        """
        charts_dir = base_dir / "charts"
        if not charts_dir.exists():
            return []

        sub_charts: list[HelmChart] = []
        dep_by_name = {d.name: d for d in dependencies}

        for entry in sorted(charts_dir.iterdir()):
            sub: HelmChart | None = None
            if entry.is_dir():
                sub = self.from_dir(entry)
            elif entry.suffix in (".tgz", ".tar"):
                sub = self.from_tgz(entry)

            if sub is None:
                continue

            # Override sub-chart defaults with parent-level scoped values
            dep = dep_by_name.get(sub.name)
            values_key = dep.values_key if dep else sub.name
            parent_overrides = parent_values.get(values_key) or {}
            if isinstance(parent_overrides, dict):
                merged = copy.deepcopy(sub.default_values)
                _deep_merge(merged, parent_overrides)
                sub.default_values = merged

            sub_charts.append(sub)

        return sub_charts

    # ------------------------------------------------------------------
    # Helm CLI helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _helm_show(
        subcommand: str,
        chart_ref: str,
        version: str | None,
        repo: str | None,
    ) -> Any:
        cmd = ["helm", "show", subcommand, chart_ref]
        if version:
            cmd += ["--version", version]
        if repo:
            cmd += ["--repo", repo]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return yaml.safe_load(result.stdout)
        except subprocess.CalledProcessError as exc:
            log.warning("helm show %s %s failed: %s", subcommand, chart_ref, exc.stderr.strip())
            return None
        except yaml.YAMLError as exc:
            log.warning("helm show %s YAML parse error: %s", subcommand, exc)
            return None

    @staticmethod
    def _load_yaml(path: Path) -> dict | None:
        try:
            return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (yaml.YAMLError, OSError) as exc:
            log.warning("Cannot read %s: %s", path, exc)
            return None


# ---------------------------------------------------------------------------
# Values helpers
# ---------------------------------------------------------------------------

def merge_values_hierarchy(*layers: dict) -> dict:
    """
    Merge N value dicts in order (last wins for scalar conflicts).
    Layers: [chart_defaults, sub_chart_defaults, helmfile_env, release_values, set_overrides]
    """
    result: dict[str, Any] = {}
    for layer in layers:
        if isinstance(layer, dict):
            _deep_merge(result, layer)
    return result


def flatten_values(values: dict, prefix: str = "", max_depth: int = 4) -> dict[str, str]:
    """
    Flatten nested values dict into dot-notation keys for indexing / comparison.
    Stops at max_depth to avoid explosion on deeply nested sub-chart values.
    """
    result: dict[str, str] = {}
    if max_depth == 0:
        return result
    for k, v in values.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            result.update(flatten_values(v, key, max_depth - 1))
        elif isinstance(v, list):
            result[key] = f"[{len(v)} items]"
        elif v is None:
            result[key] = "null"
        else:
            result[key] = str(v)
    return result


def _deep_merge(base: dict, override: dict) -> None:
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = copy.deepcopy(v)
