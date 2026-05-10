"""
ManifestRenderer — wraps `helm template` to produce rendered Kubernetes manifests.

Supports:
  - Local chart directories (from git clone)
  - Remote charts via --repo URL
  - Values dicts and value files
  - Multi-document YAML output
"""
from __future__ import annotations

import logging
import subprocess
from typing import Any

import yaml

log = logging.getLogger(__name__)


class ManifestRenderer:
    """
    Renders a Helm chart to a list of Kubernetes resource dicts.

    Each dict is a parsed YAML document with at minimum a `kind` key.
    Returns [] on any helm failure — caller decides how to handle.
    """

    def render(
        self,
        chart: str,
        release_name: str,
        namespace: str = "default",
        values: dict[str, Any] | None = None,
        value_files: list[str] | None = None,
        repo_url: str | None = None,
        chart_version: str | None = None,
    ) -> list[dict]:
        """
        Parameters
        ----------
        chart:          Local path OR "repo/chart-name" OR bare chart name.
        release_name:   Helm release name (used as .Release.Name in templates).
        namespace:      Target namespace (.Release.Namespace).
        values:         Inline values dict (passed as --set k=v).
        value_files:    Paths to values files (passed as -f path).
        repo_url:       Chart repository URL (passed as --repo).
        chart_version:  Chart version constraint (passed as --version).
        """
        cmd = ["helm", "template", release_name, chart,
               "--namespace", namespace or "default",
               "--include-crds"]

        if repo_url:
            cmd += ["--repo", repo_url]
        if chart_version:
            cmd += ["--version", chart_version]
        for vf in (value_files or []):
            cmd += ["-f", vf]
        for k, v in self._flatten(values or {}).items():
            cmd += ["--set", f"{k}={v}"]

        log.debug("helm template: %s", " ".join(cmd))

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, check=True, timeout=60,
            )
            docs = list(self._parse_multidoc(result.stdout))
            log.info(
                "helm template %s/%s: %d resource(s) rendered",
                namespace, release_name, len(docs),
            )
            return docs
        except subprocess.CalledProcessError as exc:
            log.warning(
                "helm template failed for %s/%s: %s",
                namespace, release_name, (exc.stderr or "").strip()[:200],
            )
        except subprocess.TimeoutExpired:
            log.warning("helm template timed out for %s/%s", namespace, release_name)
        return []

    # ------------------------------------------------------------------

    @staticmethod
    def _parse_multidoc(text: str) -> list[dict]:
        docs = []
        for doc in yaml.safe_load_all(text):
            if isinstance(doc, dict) and doc.get("kind"):
                docs.append(doc)
        return docs

    @staticmethod
    def _flatten(values: dict, prefix: str = "") -> dict[str, str]:
        """Flatten nested dict to dot-notation for --set flags."""
        result: dict[str, str] = {}
        for k, v in values.items():
            key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                result.update(ManifestRenderer._flatten(v, key))
            elif not isinstance(v, (list, dict)):
                result[key] = str(v)
        return result
