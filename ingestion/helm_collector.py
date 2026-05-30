from __future__ import annotations
import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Any

from ontology.entities import HelmRelease, HelmChart, HelmRepository
from ontology.graph import OntologyGraph
from ontology.relationships import Edge, RelationshipType
from ingestion.chart_parser import ChartParser

log = logging.getLogger(__name__)

# K8s name validation: RFC 1123 label (lowercase alphanumeric / hyphens / dots)
_SAFE_K8S_NAME = re.compile(r'^[a-z0-9][a-z0-9\-\.]{0,252}$')


def _safe_name(value: str, field: str) -> str:
    """Return value if it is a valid K8s resource name, raise ValueError otherwise."""
    if not value or not _SAFE_K8S_NAME.match(value):
        raise ValueError(f"unsafe {field}: {value!r}")
    return value


class HelmCollector:
    """
    Collects Helm release state from a live cluster via the `helm` CLI.
    Captures: release metadata, user-supplied values, chart version, status,
    and notes (which often contain error context useful for RCA).
    """

    def __init__(self, kubeconfig: str | None = None, kube_context: str | None = None) -> None:
        self._env_flags: list[str] = []
        if kubeconfig:
            if not Path(kubeconfig).is_file():
                raise ValueError(f"kubeconfig not found: {kubeconfig!r}")
            self._env_flags += ["--kubeconfig", str(Path(kubeconfig).resolve())]
        if kube_context:
            self._env_flags += ["--kube-context", _safe_name(kube_context, "kube_context")]

    def collect(self, graph: OntologyGraph, namespaces: list[str] | None = None) -> None:
        releases = self._list_releases(namespaces)
        log.info("Found %d Helm release(s) in cluster", len(releases))
        parser = ChartParser()

        # Index known repos so chart entities can link to them
        repo_uid_map = self._index_repos(graph)

        for rel in releases:
            try:
                namespace = _safe_name(rel.get("namespace", ""), "namespace")
                release_name = _safe_name(rel.get("name", ""), "release_name")
            except ValueError as exc:
                log.warning("helm collect: skipping release — %s", exc)
                continue

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
                values=user_values,
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
                self._wire_chart_deps(graph, chart_entity)

                # Wire HOSTED_BY: chart → repo (repo/chart-version prefix in chart_str)
                if "/" in chart_str:
                    repo_name = chart_str.split("/")[0]
                    repo_uid = repo_uid_map.get(repo_name)
                    if repo_uid:
                        graph.add_edge(Edge(
                            chart_entity.uid, repo_uid, RelationshipType.HOSTED_BY,
                        ))

        log.info("Helm collection done")

    # ------------------------------------------------------------------
    # Repository indexing
    # ------------------------------------------------------------------

    def _index_repos(self, graph: OntologyGraph) -> dict[str, str]:
        """
        Calls `helm repo list` to enumerate configured repos and creates
        HelmRepository nodes. Returns a name→uid map for edge wiring.
        Fails silently — repo info is enrichment, not critical.
        """
        uid_map: dict[str, str] = {}
        repos = self._run_json(["helm", "repo", "list", "--output", "json"], "helm repo list")
        if not isinstance(repos, list):
            return uid_map
        for repo in repos:
            name = repo.get("name", "")
            url = repo.get("url", "")
            if not name:
                continue
            uid = f"helmrepo-{name}"
            if not graph.get(uid):
                repo_type = "oci" if url.startswith("oci://") else "http"
                graph.add_entity(HelmRepository(uid=uid, name=name, url=url, repo_type=repo_type))
            uid_map[name] = uid
        return uid_map

    # ------------------------------------------------------------------
    # CLI wrappers
    # ------------------------------------------------------------------

    def _list_releases(self, namespaces: list[str] | None) -> list[dict[str, Any]]:
        if namespaces:
            results: list[dict] = []
            for ns in namespaces:
                try:
                    safe_ns = _safe_name(ns, "namespace")
                except ValueError:
                    log.warning("Skipping unsafe namespace: %r", ns)
                    continue
                results.extend(self._helm_list(namespace=safe_ns))
            return results
        return self._helm_list(all_namespaces=True)

    def _helm_list(
        self, namespace: str | None = None, all_namespaces: bool = False
    ) -> list[dict[str, Any]]:
        cmd = ["helm", "list", "--output", "json"] + self._env_flags
        if all_namespaces:
            cmd.append("--all-namespaces")
        elif namespace:
            safe_ns = _safe_name(namespace, "namespace")
            cmd += ["--namespace", safe_ns]
        return self._run_json(cmd, "helm list")

    def _get_values(
        self, release_name: str, namespace: str, include_defaults: bool = False
    ) -> dict[str, Any]:
        safe_release_name = _safe_name(release_name, "release_name")
        safe_namespace = _safe_name(namespace, "namespace")
        cmd = [
            "helm", "get", "values", safe_release_name,
            "--namespace", safe_namespace,
            "--output", "json",
        ] + self._env_flags
        if include_defaults:
            cmd.append("--all")   # includes chart defaults + computed values
        result = self._run_json(cmd, f"helm get values {safe_release_name}")
        return result if isinstance(result, dict) else {}

    def _get_notes(self, release_name: str, namespace: str) -> str:
        safe_release_name = _safe_name(release_name, "release_name")
        safe_namespace = _safe_name(namespace, "namespace")
        cmd = [
            "helm", "get", "notes", safe_release_name,
            "--namespace", safe_namespace,
        ] + self._env_flags
        try:
            self._validate_helm_cmd(cmd)
            out = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return out.stdout.strip()
        except (subprocess.CalledProcessError, ValueError):
            return ""

    @staticmethod
    def _validate_helm_cmd(cmd: list[str]) -> None:
        if not cmd or cmd[0] != "helm":
            raise ValueError("unsafe command executable")
        if len(cmd) < 2:
            raise ValueError("incomplete helm command")

        allowed_flags = {"--namespace", "--kubeconfig", "--kube-context", "--output", "--all-namespaces", "--all"}
        i = 1
        release_seen = False

        # Validate command shape
        if cmd[1:3] == ["repo", "list"]:
            i = 3
        elif cmd[1] == "list":
            i = 2
        elif cmd[1:3] == ["get", "values"] or cmd[1:3] == ["get", "notes"]:
            if len(cmd) < 4:
                raise ValueError("missing release name")
            _safe_name(cmd[3], "release_name")
            release_seen = True
            i = 4
        else:
            raise ValueError("unsupported helm subcommand")

        while i < len(cmd):
            token = cmd[i]
            if token not in allowed_flags:
                raise ValueError(f"unsupported helm flag: {token}")

            if token in {"--namespace", "--kube-context", "--kubeconfig", "--output"}:
                if i + 1 >= len(cmd):
                    raise ValueError(f"missing value for {token}")
                value = cmd[i + 1]
                if token in {"--namespace", "--kube-context"}:
                    _safe_name(value, token.lstrip("-"))
                elif token == "--kubeconfig":
                    p = Path(value)
                    if not p.is_absolute() or not p.is_file():
                        raise ValueError("unsafe kubeconfig path")
                elif token == "--output" and value != "json":
                    raise ValueError("unsupported output format")
                i += 2
                continue

            # boolean flags
            if token == "--all" and cmd[1:3] != ["get", "values"]:
                raise ValueError("--all only allowed for helm get values")
            if token == "--all-namespaces" and cmd[1] != "list":
                raise ValueError("--all-namespaces only allowed for helm list")
            i += 1

        if cmd[1:3] in (["get", "values"], ["get", "notes"]) and not release_seen:
            raise ValueError("missing release name")

    def _run_json(self, cmd: list[str], label: str) -> Any:
        try:
            self._validate_helm_cmd(cmd)
            out = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return json.loads(out.stdout) or {}
        except subprocess.CalledProcessError as exc:
            log.warning("%s failed: %s", label, exc.stderr.strip())
            return {}
        except json.JSONDecodeError:
            log.warning("%s returned invalid JSON", label)
            return {}
        except ValueError as exc:
            log.warning("%s blocked unsafe command: %s", label, exc)
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
