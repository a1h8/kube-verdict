"""
GitProvider — abstracts over local git clones and the GitHub REST API.

Two implementations:
  LocalGitProvider  — git clone / git pull into a temp directory.
  GithubProvider    — GitHub REST API (no local clone required, rate-limited).

Both expose the same interface so GitopsCollector is provider-agnostic.
"""
from __future__ import annotations

import base64
import logging
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path

import requests

log = logging.getLogger(__name__)


class GitProvider(ABC):
    """Read-only access to a git repository tree."""

    @abstractmethod
    def get_file(self, path: str) -> str | None:
        """Return the UTF-8 content of a file, or None on failure."""

    @abstractmethod
    def list_files(self, directory: str, pattern: str = "*.yaml") -> list[str]:
        """Return relative paths of matching files under directory."""

    @abstractmethod
    def local_path(self) -> Path | None:
        """Return a local filesystem path to the repo root, or None if remote-only."""


# ─────────────────────────────────────────────────────────────────────────────
# Local clone
# ─────────────────────────────────────────────────────────────────────────────

class LocalGitProvider(GitProvider):
    """
    Clones a git repo (shallow, single branch) into a local cache directory
    and keeps it up-to-date with `git pull --ff-only`.

    Parameters
    ----------
    repo_url:   HTTPS or SSH URL, e.g. https://github.com/myorg/infra.git
    branch:     branch / tag / SHA to check out (default: main)
    clone_dir:  parent directory for clones (default: /tmp/kubewhisperer-gitops)
    """

    def __init__(
        self,
        repo_url: str,
        branch: str = "main",
        clone_dir: Path | None = None,
    ) -> None:
        self.repo_url = repo_url
        self.branch = branch
        self._base = clone_dir or Path("/tmp/kubewhisperer-gitops")

    def _repo_dir(self) -> Path:
        name = self.repo_url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
        return self._base / name

    def _ensure_cloned(self) -> Path:
        dest = self._repo_dir()
        if (dest / ".git").exists():
            result = subprocess.run(
                ["git", "-C", str(dest), "pull", "--ff-only"],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                log.warning("git pull failed for %s: %s", dest, result.stderr.strip())
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["git", "clone", "--depth=1", "--branch", self.branch,
                 self.repo_url, str(dest)],
                capture_output=True, text=True, check=True,
            )
        return dest

    def get_file(self, path: str) -> str | None:
        try:
            dest = self._ensure_cloned()
            return (dest / path).read_text(encoding="utf-8")
        except (OSError, subprocess.CalledProcessError) as exc:
            log.warning("LocalGitProvider.get_file(%s): %s", path, exc)
            return None

    def list_files(self, directory: str, pattern: str = "*.yaml") -> list[str]:
        try:
            dest = self._ensure_cloned()
            base = dest / directory
            if not base.is_dir():
                return []
            return [str(p.relative_to(dest)) for p in base.rglob(pattern)]
        except (OSError, subprocess.CalledProcessError):
            return []

    def local_path(self) -> Path | None:
        try:
            return self._ensure_cloned()
        except subprocess.CalledProcessError:
            return None


# ─────────────────────────────────────────────────────────────────────────────
# GitHub REST API
# ─────────────────────────────────────────────────────────────────────────────

class GithubProvider(GitProvider):
    """
    Fetches files from a GitHub repository via the REST API.
    No local clone — each file is fetched on demand.

    Parameters
    ----------
    repo:   "owner/repo"
    ref:    branch, tag, or commit SHA (default: main)
    token:  GitHub personal access token (recommended to avoid rate limits)
    """

    _API = "https://api.github.com"

    def __init__(
        self,
        repo: str,
        ref: str = "main",
        token: str | None = None,
    ) -> None:
        self.repo = repo
        self.ref = ref
        self._headers = {"Accept": "application/vnd.github+json"}
        if token:
            self._headers["Authorization"] = f"Bearer {token}"
        self._tree_cache: list[dict] | None = None

    def get_file(self, path: str) -> str | None:
        url = f"{self._API}/repos/{self.repo}/contents/{path}?ref={self.ref}"
        try:
            resp = requests.get(url, headers=self._headers, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data.get("encoding") == "base64":
                return base64.b64decode(data["content"]).decode("utf-8")
        except Exception as exc:
            log.warning("GithubProvider.get_file(%s): %s", path, exc)
        return None

    def list_files(self, directory: str, pattern: str = "*.yaml") -> list[str]:
        tree = self._get_tree()
        suffix = pattern.lstrip("*")
        return [
            item["path"] for item in tree
            if item.get("type") == "blob"
            and item["path"].startswith(directory.rstrip("/") + "/")
            and item["path"].endswith(suffix)
        ]

    def local_path(self) -> Path | None:
        return None

    def _get_tree(self) -> list[dict]:
        if self._tree_cache is not None:
            return self._tree_cache
        url = f"{self._API}/repos/{self.repo}/git/trees/{self.ref}?recursive=1"
        try:
            resp = requests.get(url, headers=self._headers, timeout=15)
            resp.raise_for_status()
            self._tree_cache = resp.json().get("tree", [])
        except Exception as exc:
            log.warning("GithubProvider._get_tree: %s", exc)
            self._tree_cache = []
        return self._tree_cache
