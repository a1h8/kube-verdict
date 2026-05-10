"""
GitopsCollector — GitOps-aware drift detection.

Pipeline per HelmRelease:
  1. Resolve chart location (git repo directory or remote ref)
  2. helm template ��� rendered Kubernetes manifests
  3. Diff rendered vs live entities in OntologyGraph
  4. Annotate entities with gitops.* drift items

Designed to run AFTER K8sCollector + HelmCollector so the graph already
contains the live cluster state.

Usage
-----
    from ingestion.git_provider import GithubProvider
    from ingestion.gitops_collector import GitopsCollector

    provider = GithubProvider("myorg/infra", ref="main",
                              token=os.getenv("GITHUB_TOKEN"))
    collector = GitopsCollector(provider, charts_path="charts")
    drifts = collector.collect(graph)
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ontology.entities import DriftItem, HelmRelease
from ontology.graph import OntologyGraph
from ingestion.git_provider import GitProvider
from ingestion.manifest_differ import ManifestDiffer
from ingestion.manifest_renderer import ManifestRenderer

log = logging.getLogger(__name__)


class GitopsCollector:
    """
    Parameters
    ----------
    provider:       GitProvider giving access to the infra repository.
    charts_path:    Path inside the repo where local Helm charts live
                    (e.g. "charts" → looks for charts/<release.chart>/Chart.yaml).
    renderer:       ManifestRenderer instance (injectable for tests).
    differ:         ManifestDiffer instance (injectable for tests).
    track_orphans:  Whether to flag cluster resources absent from rendered output.
    """

    def __init__(
        self,
        provider: GitProvider,
        charts_path: str = "charts",
        renderer: ManifestRenderer | None = None,
        differ: ManifestDiffer | None = None,
        track_orphans: bool = False,
    ) -> None:
        self._provider = provider
        self._charts_path = charts_path.rstrip("/")
        self._renderer = renderer or ManifestRenderer()
        self._differ = differ or ManifestDiffer(track_orphans=track_orphans)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def collect(self, graph: OntologyGraph) -> list[DriftItem]:
        """
        Process all HelmRelease entities in the graph.
        Returns a flat list of DriftItems (also annotated onto entities).
        """
        releases = [e for e in graph.entities() if isinstance(e, HelmRelease)]
        log.info("GitopsCollector: %d release(s) to process", len(releases))

        all_drifts: list[DriftItem] = []
        for release in releases:
            all_drifts.extend(self._process(graph, release))

        critical = sum(1 for d in all_drifts if d.severity == "critical")
        log.info(
            "GitopsCollector: %d drift item(s) total, %d critical",
            len(all_drifts), critical,
        )
        return all_drifts

    # ------------------------------------------------------------------
    # Per-release logic
    # ------------------------------------------------------------------

    def _process(self, graph: OntologyGraph, release: HelmRelease) -> list[DriftItem]:
        chart_ref, repo_url = self._resolve_chart(release)
        if chart_ref is None:
            log.debug("gitops: cannot resolve chart for %s/%s — skipping",
                      release.namespace, release.name)
            return []

        rendered = self._renderer.render(
            chart=chart_ref,
            release_name=release.name,
            namespace=release.namespace or "default",
            values=release.values,
            value_files=release.value_files,
            repo_url=repo_url,
            chart_version=release.chart_version or None,
        )

        if not rendered:
            log.debug("gitops: no manifests rendered for %s/%s",
                      release.namespace, release.name)
            return []

        drifts = self._differ.diff(rendered, graph, release_uid=release.uid)

        if drifts:
            release.annotations["gitops.drift_count"] = str(len(drifts))
            critical = sum(1 for d in drifts if d.severity == "critical")
            if critical:
                release.annotations["gitops.critical_drifts"] = str(critical)
            log.info(
                "gitops: %s/%s — %d drift(s), %d critical",
                release.namespace, release.name, len(drifts), critical,
            )
        return drifts

    def _resolve_chart(
        self, release: HelmRelease
    ) -> tuple[str | None, str | None]:
        """
        Returns (chart_ref, repo_url).

        Priority:
        1. Local directory in git repo:  <charts_path>/<chart-name>/Chart.yaml
        2. Local directory named after the release
        3. Remote chart reference "repo/chart-name" ��� passed through to helm
        4. Bare chart name — passed through (helm will try configured repos)
        """
        local_root = self._provider.local_path()

        if local_root is not None:
            for candidate in (release.chart, release.name):
                chart_dir = local_root / self._charts_path / candidate
                if (chart_dir / "Chart.yaml").is_file():
                    return str(chart_dir), None

        # Remote chart: "repo/chart-name" format — no repo URL needed (uses `helm repo`)
        if release.chart and "/" in release.chart:
            return release.chart, None

        # Bare name with a known chart version — let helm resolve
        if release.chart and release.chart_version:
            return release.chart, None

        return None, None
