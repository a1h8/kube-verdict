from __future__ import annotations
import json
import logging
import subprocess
from typing import Any

from ontology.entities import HelmRelease, HelmChart
from ontology.graph import OntologyGraph
from ontology.relationships import Edge, RelationshipType
from ingestion.chart_parser import ChartParser

log = logging.getLogger(__name__)


class HelmCollector:
    """
    Collects Helm release state from a live cluster via the `helm` CLI.
    Captures: release metadata, user-supplied values, chart version, status,
    and notes (which often contain error context useful for RCA).
    """

    def __init__(self, kubeconfig: str | None = None, kube_context: str | None = None) -> None:
        self._env_flags: list[str] = []
        if kubeconfig:
            self._env_flags += ["--kubeconfig", kubeconfig]
        if kube_context:
            self._env_flags += ["--kube-context", kube_context]

    def collect(self, graph: OntologyGraph, namespaces: list[str] | None = None) -> None:
        releases = self._list_releases(namespaces)
        log.info("Found %d Helm release(s) in cluster", len(releases))
        parser = ChartParser()

        for rel in releases:
            namespace = rel.get("namespace", "")
            release_name = rel.get("name", "")

            user_values = self._get_values(release_name, namespace, include_defaults=False)
            notes = self._get_notes(release_name, namespace)

            chart_str = rel.get("chart", "")
            chart_name = self._parse_chart_name(chart_str)
            chart_version = self._parse_chart_version(chart_str)
            uid = f"helm-{namespace}-{release_name}"

            entity = HelmRelease(
                uid=uid,
                name=release_name,
                namespace=namespace,
                labels={"app.kubernetes.io/managed-by": "Helm"},
                annotations={"helm.sh/notes": notes} if notes else {},
                chart=chart_name,
                chart_version=chart_version,
                app_version=rel.get("app_version", ""),
                status=rel.get("status", ""),
                values=user_values,   # user-supplied only (cleaner for diff)
                source="helm",
                raw=rel,
            )
            graph.add_entity(entity)
            self._link_managed_resources(graph, entity, namespace)

            # Parse chart structure and add HelmChart node
            chart_entity = parser.from_helm_show(
                chart_ref=f"{chart_name}",
                version=chart_version,
            )
            if chart_entity:
                graph.add_entity(chart_entity)
                graph.add_edge(Edge(uid, chart_entity.uid, RelationshipType.DEPLOYED_FROM))
                # Wire umbrella sub-chart dependency edges
                self._wire_chart_deps(graph, chart_entity)

        log.info("Helm collection done")

    # ------------------------------------------------------------------
    # CLI wrappers
    # ------------------------------------------------------------------

    def _list_releases(self, namespaces: list[str] | None) -> list[dict[str, Any]]:
        if namespaces:
            results: list[dict] = []
            for ns in namespaces:
                results.extend(self._helm_list(namespace=ns))
            return results
        return self._helm_list(all_namespaces=True)

    def _helm_list(
        self, namespace: str | None = None, all_namespaces: bool = False
    ) -> list[dict[str, Any]]:
        cmd = ["helm", "list", "--output", "json"] + self._env_flags
        if all_namespaces:
            cmd.append("--all-namespaces")
        elif namespace:
            cmd += ["--namespace", namespace]
        return self._run_json(cmd, "helm list")

    def _get_values(
        self, release_name: str, namespace: str, include_defaults: bool = False
    ) -> dict[str, Any]:
        cmd = [
            "helm", "get", "values", release_name,
            "--namespace", namespace,
            "--output", "json",
        ] + self._env_flags
        if include_defaults:
            cmd.append("--all")   # includes chart defaults + computed values
        result = self._run_json(cmd, f"helm get values {release_name}")
        return result if isinstance(result, dict) else {}

    def _get_notes(self, release_name: str, namespace: str) -> str:
        cmd = [
            "helm", "get", "notes", release_name,
            "--namespace", namespace,
        ] + self._env_flags
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return out.stdout.strip()
        except subprocess.CalledProcessError:
            return ""

    def _run_json(self, cmd: list[str], label: str) -> Any:
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return json.loads(out.stdout) or {}
        except subprocess.CalledProcessError as exc:
            log.warning("%s failed: %s", label, exc.stderr.strip())
            return {}
        except json.JSONDecodeError:
            log.warning("%s returned invalid JSON", label)
            return {}

    # ------------------------------------------------------------------
    # Graph linking
    # ------------------------------------------------------------------

    def _link_managed_resources(
        self, graph: OntologyGraph, helm_entity: HelmRelease, namespace: str
    ) -> None:
        for entity in graph.entities():
            if entity.namespace != namespace:
                continue
            release_ann = entity.annotations.get("meta.helm.sh/release-name", "")
            managed_by = entity.labels.get("app.kubernetes.io/managed-by", "")
            if release_ann == helm_entity.name or managed_by == "Helm":
                graph.add_edge(
                    Edge(entity.uid, helm_entity.uid, RelationshipType.MANAGED_BY_HELM)
                )

    @staticmethod
    def _wire_chart_deps(graph: OntologyGraph, chart: HelmChart) -> None:
        sub_charts = getattr(chart, "_sub_charts", [])
        for sub in sub_charts:
            graph.add_entity(sub)
            graph.add_edge(Edge(chart.uid, sub.uid, RelationshipType.CHART_DEPENDENCY))
            # Recurse for nested umbrella charts
            HelmCollector._wire_chart_deps(graph, sub)

    @staticmethod
    def _parse_chart_name(chart_string: str) -> str:
        parts = chart_string.rsplit("-", 1)
        return parts[0] if len(parts) == 2 else chart_string

    @staticmethod
    def _parse_chart_version(chart_string: str) -> str:
        parts = chart_string.rsplit("-", 1)
        return parts[-1] if len(parts) == 2 else ""
