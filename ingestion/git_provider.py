"""
GitProvider — abstracts over local git clones and the GitHub REST API.

Implementations:
  LocalGitProvider  — git clone / git pull into a temp directory.
                      Accepts any HTTPS/SSH/file URL + optional token.
  GithubProvider    — GitHub REST API (no local clone required, rate-limited).

Factory:
  make_provider(url, branch, token) — returns the right provider for any URL.

Both expose the same interface so GitopsCollector is provider-agnostic.
"""
from __future__ import annotations

import base64
import logging
import os
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
from urllib.parse import urlparse

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
    Clones any git repo (shallow, single branch) into a local cache directory
    and keeps it up-to-date with `git pull --ff-only`.

    Works with GitHub, GitLab, Gitea, Gist, self-hosted, Bitbucket — any URL
    accepted by `git clone`. SSH URLs use the system ssh-agent; HTTPS URLs
    accept an optional token, given to git as an HTTP header through its environment — never in
    the URL, so it is not in the clone's `.git/config`, the process list or an error message.

    Parameters
    ----------
    repo_url:   HTTPS, SSH, or file:// URL
    branch:     branch / tag / SHA (default: main)
    token:      optional PAT/oauth token for HTTPS auth
    clone_dir:  parent directory for clones (default: /tmp/kubeverdict-gitops)
    """

    def __init__(
        self,
        repo_url: str,
        branch: str = "main",
        token: str | None = None,
        clone_dir: Path | None = None,
    ) -> None:
        self.repo_url = repo_url
        self.branch = branch
        self._token = token
        # why the checkout is missing or stale, for the caller to report (fix plan 001, H5)
        self.last_error: str | None = None
        self._base = clone_dir or Path("/tmp/kubeverdict-gitops")

    def _repo_dir(self) -> Path:
        name = self.repo_url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
        return self._base / name

    def _git_env(self) -> dict[str, str]:
        """git's environment: the token as an extra HTTP header (git >= 2.31), no prompt."""
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        token = getattr(self, "_token", None)
        if token and urlparse(self.repo_url).scheme in ("https", "http"):
            cred = base64.b64encode(f"oauth2:{token}".encode()).decode()
            i = int(env.get("GIT_CONFIG_COUNT", "0") or 0)
            env.update({
                "GIT_CONFIG_COUNT": str(i + 1),
                f"GIT_CONFIG_KEY_{i}": "http.extraHeader",
                f"GIT_CONFIG_VALUE_{i}": f"Authorization: Basic {cred}",
            })
        return env

    def _ensure_cloned(self) -> Path:
        dest = self._repo_dir()
        if (dest / ".git").exists():
            result = subprocess.run(
                ["git", "-C", str(dest), "pull", "--ff-only"],
                capture_output=True, text=True, env=self._git_env(),
            )
            if result.returncode != 0:
                log.warning("git pull failed for %s: %s", dest, result.stderr.strip())
                self.last_error = f"git pull failed, the checkout may be stale: {result.stderr.strip()[:200]}"
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["git", "clone", "--depth=1", "--branch", self.branch,
                 self.repo_url, str(dest)],
                capture_output=True, text=True, check=True, env=self._git_env(),
            )
        return dest

    def get_file(self, path: str) -> str | None:
        try:
            dest = self._ensure_cloned()
            target = _inside(dest, path)
            if target is None:
                log.warning("LocalGitProvider.get_file(%s): outside the repository — refused", path)
                return None
            return target.read_text(encoding="utf-8")
        except (OSError, subprocess.CalledProcessError) as exc:
            log.warning("LocalGitProvider.get_file(%s): %s", path, exc)
            return None

    def list_files(self, directory: str, pattern: str = "*.yaml") -> list[str]:
        try:
            dest = self._ensure_cloned()
            base = _inside(dest, directory)
            if base is None or not base.is_dir():
                return []
            return [str(p.relative_to(dest)) for p in base.rglob(pattern)
                    if _inside(dest, str(p.relative_to(dest))) is not None]
        except (OSError, subprocess.CalledProcessError):
            return []

    def local_path(self) -> Path | None:
        try:
            return self._ensure_cloned()
        except subprocess.CalledProcessError as exc:
            self.last_error = f"git clone failed: {(exc.stderr or '').strip()[:200] or exc.returncode}"
            return None
        except OSError as exc:
            self.last_error = f"git could not run: {exc}"
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


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _inside(root: Path, rel: str) -> Path | None:
    """Return root/rel resolved (symlinks included), or None when it leaves root."""
    root = root.resolve()
    target = (root / rel).resolve()
    return target if target.is_relative_to(root) else None


def make_provider(
    url: str,
    branch: str = "main",
    token: str | None = None,
) -> GitProvider:
    """Return the right GitProvider for any repo URL + optional auth token.

    Works for GitHub, GitLab, Gitea, Gist, Bitbucket, self-hosted, file://.
    SSH URLs use the system ssh-agent; HTTPS URLs accept a token.
    """
    return LocalGitProvider(repo_url=url, branch=branch, token=token)
