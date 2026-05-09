from __future__ import annotations
import copy
import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Any

import yaml

import config as cfg
from ontology.entities import HelmRelease
from ontology.graph import OntologyGraph
from ontology.relationships import Edge, RelationshipType

log = logging.getLogger(__name__)

# Helmfile Go-template expressions that we cannot evaluate — strip them safely
_TEMPLATE_RE = re.compile(r"\{\{.*?\}\}", re.DOTALL)


class HelmfileCollector:
    """
    Reads Helmfile declarative configs (helmfile.yaml or helmfile.d/*.yaml)
    and populates the ontology graph with HelmRelease entities.

    Two modes:
    - Pure YAML parsing (default): no helmfile binary needed, works offline.
      Go-template expressions in values are stripped rather than evaluated.
    - CLI rendering (use_cli=True): runs `helmfile build` to get a fully
      rendered spec including template expressions and environment merges.

    The `needs:` graph is wired as DEPENDS_ON edges so the BFS traversal
    can follow release dependencies during RCA.
    """

    def __init__(
        self,
        helmfile_path: str | Path | None = None,
        environment: str | None = None,
        use_cli: bool = False,
    ) -> None:
        raw_path = helmfile_path or cfg.HELMFILE_PATH
        self.path: Path | None = Path(raw_path) if raw_path else None
        self.environment = environment or cfg.HELMFILE_ENVIRONMENT or "default"
        self.use_cli = use_cli

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def collect(self, graph: OntologyGraph) -> None:
        if self.path is None:
            log.debug("HELMFILE_PATH not set — skipping Helmfile collection")
            return
        if not self.path.exists():
            log.warning("Helmfile path not found: %s", self.path)
            return

        specs = self._load_specs()
        if not specs:
            return

        merged = self._merge_specs(specs)
        env_values = self._resolve_env_values(merged)
        releases = merged.get("releases") or []

        log.info(
            "Helmfile: %d release(s) found  environment=%s  path=%s",
            len(releases), self.environment, self.path,
        )

        uid_map: dict[str, str] = {}  # "namespace/name" → entity uid

        for rel in releases:
            entity = self._build_entity(rel, env_values)
            graph.add_entity(entity)
            uid_map[f"{entity.namespace}/{entity.name}"] = entity.uid
            uid_map[entity.name] = entity.uid  # allow bare-name references too
            self._link_managed_resources(graph, entity)

        # Wire DEPENDS_ON edges from needs:
        for rel in releases:
            self._wire_needs(graph, rel, uid_map)

        log.info("Helmfile collection done (%d releases)", len(releases))

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load_specs(self) -> list[dict]:
        assert self.path is not None

        if self.use_cli and self._helmfile_available():
            return self._load_via_cli()

        # Directory mode: helmfile.d/*.yaml
        if self.path.is_dir():
            specs = []
            for f in sorted(self.path.glob("*.yaml")):
                spec = self._parse_yaml_file(f)
                if spec:
                    specs.append(spec)
            return specs

        spec = self._parse_yaml_file(self.path)
        return [spec] if spec else []

    def _parse_yaml_file(self, path: Path) -> dict | None:
        try:
            text = path.read_text(encoding="utf-8")
            # Strip Go-template expressions before parsing
            text = _TEMPLATE_RE.sub('""', text)
            return yaml.safe_load(text) or {}
        except yaml.YAMLError as exc:
            log.warning("YAML parse error in %s: %s", path, exc)
            return None
        except OSError as exc:
            log.warning("Cannot read %s: %s", path, exc)
            return None

    def _load_via_cli(self) -> list[dict]:
        """Uses `helmfile build` to get a fully rendered spec."""
        cmd = [
            "helmfile", "--file", str(self.path),
            "--environment", self.environment,
            "build", "--output", "json",
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            data = json.loads(result.stdout)
            return [data] if isinstance(data, dict) else data
        except subprocess.CalledProcessError as exc:
            log.warning("helmfile build failed: %s — falling back to YAML parse", exc.stderr.strip())
            return []
        except json.JSONDecodeError:
            log.warning("helmfile build returned invalid JSON — falling back to YAML parse")
            return []

    @staticmethod
    def _helmfile_available() -> bool:
        try:
            subprocess.run(["helmfile", "version"], capture_output=True, check=True)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    # ------------------------------------------------------------------
    # Spec merging (helmfile.d fragments → single spec)
    # ------------------------------------------------------------------

    @staticmethod
    def _merge_specs(specs: list[dict]) -> dict:
        merged: dict[str, Any] = {
            "repositories": [],
            "environments": {},
            "helmDefaults": {},
            "releases": [],
        }
        for spec in specs:
            merged["repositories"].extend(spec.get("repositories") or [])
            merged["releases"].extend(spec.get("releases") or [])
            _deep_merge(merged["environments"], spec.get("environments") or {})
            _deep_merge(merged["helmDefaults"], spec.get("helmDefaults") or {})
        return merged

    # ------------------------------------------------------------------
    # Environment value resolution
    # ------------------------------------------------------------------

    def _resolve_env_values(self, spec: dict) -> dict[str, Any]:
        """
        Reads and merges environment-specific value files declared under
        environments.<name>.values in the helmfile spec.
        """
        environments: dict = spec.get("environments") or {}
        env_spec = environments.get(self.environment) or {}
        value_files: list = env_spec.get("values") or []

        base_dir = self.path.parent if self.path and not self.path.is_dir() else (self.path or Path("."))
        merged: dict[str, Any] = {}
        for vf in value_files:
            if not isinstance(vf, str):
                continue
            vf_path = base_dir / vf
            data = self._read_value_file(vf_path)
            _deep_merge(merged, data)
        return merged

    # ------------------------------------------------------------------
    # Per-release entity construction
    # ------------------------------------------------------------------

    def _build_entity(self, rel: dict, env_values: dict) -> HelmRelease:
        name = rel.get("name", "")
        namespace = rel.get("namespace", "default")
        chart = rel.get("chart", "")
        version = rel.get("version", "")
        installed = rel.get("installed", True)
        labels: dict = rel.get("labels") or {}

        # Merge values: env-level → release value files → inline set values
        base_dir = (
            self.path.parent
            if self.path and not self.path.is_dir()
            else (self.path or Path("."))
        )
        merged_values = copy.deepcopy(env_values)

        value_file_paths: list[str] = []
        for vf in (rel.get("values") or []):
            if isinstance(vf, str):
                vf_path = base_dir / vf
                value_file_paths.append(str(vf_path))
                data = self._read_value_file(vf_path)
                _deep_merge(merged_values, data)
            elif isinstance(vf, dict):
                # Inline values dict inside the release
                _deep_merge(merged_values, vf)

        # Apply set: overrides (key=value scalars only)
        for setter in (rel.get("set") or []):
            key: str = setter.get("name", "")
            val = setter.get("value")
            if key and val is not None and not isinstance(val, str | int | float | bool):
                continue  # skip templated values
            if key:
                _set_nested(merged_values, key, val)

        uid = f"helmfile-{namespace}-{name}"
        return HelmRelease(
            uid=uid,
            name=name,
            namespace=namespace,
            labels={**labels, "app.kubernetes.io/managed-by": "Helm"},
            annotations={
                "helmfile.io/environment": self.environment,
                "helmfile.io/installed": str(installed).lower(),
            },
            chart=_chart_name(chart),
            chart_version=version or _chart_version_from_string(chart),
            app_version="",
            status="declared" if installed else "disabled",
            values=merged_values,
            source="helmfile",
            environment=self.environment,
            value_files=value_file_paths,
            needs=[str(n) for n in (rel.get("needs") or [])],
            raw=rel,
        )

    # ------------------------------------------------------------------
    # Graph wiring
    # ------------------------------------------------------------------

    def _link_managed_resources(self, graph: OntologyGraph, entity: HelmRelease) -> None:
        for e in graph.entities():
            if e.namespace != entity.namespace:
                continue
            release_ann = e.annotations.get("meta.helm.sh/release-name", "")
            managed_by = e.labels.get("app.kubernetes.io/managed-by", "")
            if release_ann == entity.name or managed_by == "Helm":
                graph.add_edge(Edge(e.uid, entity.uid, RelationshipType.MANAGED_BY_HELM))

    @staticmethod
    def _wire_needs(
        graph: OntologyGraph, rel: dict, uid_map: dict[str, str]
    ) -> None:
        src_uid = uid_map.get(
            f"{rel.get('namespace', 'default')}/{rel.get('name', '')}"
        ) or uid_map.get(rel.get("name", ""))
        if not src_uid:
            return
        for need in (rel.get("needs") or []):
            target_uid = uid_map.get(str(need))
            if target_uid:
                graph.add_edge(Edge(src_uid, target_uid, RelationshipType.DEPENDS_ON))
            else:
                log.debug("helmfile needs: reference %r not found in graph", need)

    # ------------------------------------------------------------------
    # Value file helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _read_value_file(path: Path) -> dict[str, Any]:
        if not path.exists():
            log.debug("Value file not found: %s", path)
            return {}
        try:
            text = path.read_text(encoding="utf-8")
            text = _TEMPLATE_RE.sub('""', text)
            data = yaml.safe_load(text)
            return data if isinstance(data, dict) else {}
        except (yaml.YAMLError, OSError) as exc:
            log.warning("Cannot read value file %s: %s", path, exc)
            return {}


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _deep_merge(base: dict, override: dict) -> None:
    """Mutates base by recursively merging override into it."""
    for key, val in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(val, dict):
            _deep_merge(base[key], val)
        else:
            base[key] = copy.deepcopy(val)


def _set_nested(d: dict, dotted_key: str, value: Any) -> None:
    """Sets d[a][b][c] from dotted key "a.b.c"."""
    keys = dotted_key.split(".")
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    d[keys[-1]] = value


def _chart_name(chart: str) -> str:
    # "repo/chart-name" → "chart-name"; "./charts/my-chart" → "my-chart"
    return Path(chart.split("/")[-1]).name if chart else ""


def _chart_version_from_string(chart: str) -> str:
    # "chart-name-1.2.3" → "1.2.3"
    parts = chart.rsplit("-", 1)
    return parts[-1] if len(parts) == 2 else ""
