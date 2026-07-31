"""
PR/MR-first remediation — phase 2: open the proposed change as a draft PR/MR.

Phase 1 (``patch_builder.py``) turns a verdict into a ``PatchProposal`` — a
declared change to ``values.yaml`` plus a unified diff. Phase 2 pushes that
change to the GitOps repo as a **draft** pull/merge request, where the human
approval gate already lives. KubeVerdict never merges its own proposal.

A single factory dispatches by repo host to the right platform:

    make_change_proposer(repo_url, token=...) -> ChangeProposer
        github.com          -> GithubProposer   (REST v3, /repos/.../pulls)
        gitlab.com / gitlab -> GitlabProposer    (REST v4, /projects/.../merge_requests)
        gitea / forgejo     -> GiteaProposer      (Gitea API, GitHub-compatible)

Each proposer performs the same three steps against its platform's API:

    1. create branch  kubeverdict/fix-<release>-<short-hash>  off the base ref
    2. commit the patched file on that branch
    3. open a draft PR/MR whose body carries the verdict evidence (the diff)

All network calls go through ``requests``; the flow is fully unit-tested offline
with a stubbed transport. See ``docs/pr-mr-first.md``.
"""
from __future__ import annotations

import base64
import hashlib
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from urllib.parse import quote, urlparse

import requests

from remediation.patch_builder import PatchProposal

log = logging.getLogger(__name__)

_BRANCH_PREFIX = "kubeverdict/fix"


@dataclass
class ProposedChange:
    """The opened draft PR/MR."""
    url: str
    branch: str
    provider: str          # github | gitlab | gitea
    draft: bool = True
    number: int | None = None

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "branch": self.branch,
            "provider": self.provider,
            "draft": self.draft,
            "number": self.number,
        }


def _branch_name(patch: PatchProposal) -> str:
    """Deterministic branch name from the patch content."""
    digest = hashlib.sha1((patch.diff or patch.new_content).encode()).hexdigest()[:8]
    release = (patch.release or "change").replace("/", "-")
    return f"{_BRANCH_PREFIX}-{release}-{digest}"


def _default_title(patch: PatchProposal) -> str:
    return f"fix({patch.release or 'gitops'}): reconcile {patch.file_path} to declared state"


def _default_body(patch: PatchProposal) -> str:
    return (
        "Proposed by KubeVerdict — declared-state fix for detected drift.\n\n"
        f"- release: `{patch.release or '—'}`\n"
        f"- namespace: `{patch.namespace or '—'}`\n"
        f"- file: `{patch.file_path}`\n\n"
        "```diff\n" + (patch.diff or "").rstrip("\n") + "\n```\n\n"
        "Review and merge to let GitOps reconcile. Rollback = revert this PR/MR. "
        "KubeVerdict does not merge its own proposals."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Base
# ─────────────────────────────────────────────────────────────────────────────

class ChangeProposer(ABC):
    """Opens a ``PatchProposal`` as a draft PR/MR on one git platform."""

    provider: str = ""

    def __init__(self, repo: str, base: str = "main", token: str | None = None,
                 api_base: str | None = None) -> None:
        if not token:
            raise PermissionError(
                f"{type(self).__name__} requires a token to open a change")
        self.repo = repo                    # "owner/repo"
        self.base = base
        self._token = token
        self._api = (api_base or self._default_api()).rstrip("/")

    @abstractmethod
    def _default_api(self) -> str: ...

    @abstractmethod
    def propose(self, patch: PatchProposal, *, draft: bool = True,
                title: str | None = None, body: str | None = None) -> ProposedChange: ...

    # small shared HTTP helper — raises on non-2xx, returns parsed JSON
    def _request(self, method: str, url: str, *, headers: dict,
                 json: dict | None = None, ok: tuple[int, ...] = (200, 201)) -> dict:
        resp = requests.request(method, url, headers=headers, json=json, timeout=20)
        if resp.status_code not in ok:
            raise RuntimeError(
                f"{method} {url} -> {resp.status_code}: {resp.text[:300]}")
        try:
            return resp.json()
        except Exception:
            return {}


# ─────────────────────────────────────────────────────────────────────────────
# GitHub  (and Gitea, which mirrors the same API)
# ─────────────────────────────────────────────────────────────────────────────

class GithubProposer(ChangeProposer):
    """GitHub REST v3: branch → commit via Contents API → draft PR."""

    provider = "github"

    def _default_api(self) -> str:
        return "https://api.github.com"

    def _headers(self) -> dict:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._token}",
        }

    # Gitea supports drafts only via a "WIP:" title prefix; GitHub has a real flag.
    def _supports_draft_flag(self) -> bool:
        return True

    def propose(self, patch: PatchProposal, *, draft: bool = True,
                title: str | None = None, body: str | None = None) -> ProposedChange:
        h = self._headers()
        branch = _branch_name(patch)
        title = title or _default_title(patch)
        body = body or _default_body(patch)

        # 1. base sha → create branch
        ref = self._request(
            "GET", f"{self._api}/repos/{self.repo}/git/refs/heads/{self.base}",
            headers=h)
        base_sha = ref["object"]["sha"]
        self._request(
            "POST", f"{self._api}/repos/{self.repo}/git/refs", headers=h,
            json={"ref": f"refs/heads/{branch}", "sha": base_sha}, ok=(201,))

        # 2. commit the patched file (update if it exists, else create)
        file_sha = self._existing_file_sha(patch.file_path, h)
        put: dict = {
            "message": title,
            "content": base64.b64encode(patch.new_content.encode()).decode(),
            "branch": branch,
        }
        if file_sha:
            put["sha"] = file_sha
        self._request(
            "PUT", f"{self._api}/repos/{self.repo}/contents/{patch.file_path}",
            headers=h, json=put)

        # 3. open the PR
        pr_title = title if self._supports_draft_flag() or not draft else f"WIP: {title}"
        pr_body: dict = {"title": pr_title, "head": branch, "base": self.base, "body": body}
        if self._supports_draft_flag():
            pr_body["draft"] = draft
        pr = self._request(
            "POST", f"{self._api}/repos/{self.repo}/pulls", headers=h,
            json=pr_body, ok=(201,))
        return ProposedChange(
            url=pr.get("html_url", ""), branch=branch, provider=self.provider,
            draft=draft, number=pr.get("number"))

    def _existing_file_sha(self, path: str, headers: dict) -> str | None:
        resp = requests.get(
            f"{self._api}/repos/{self.repo}/contents/{path}?ref={self.base}",
            headers=headers, timeout=20)
        if resp.status_code == 200:
            return resp.json().get("sha")
        return None


class GiteaProposer(GithubProposer):
    """Gitea / Forgejo — GitHub-compatible API under /api/v1, no draft flag."""

    provider = "gitea"

    def _default_api(self) -> str:  # pragma: no cover - api_base always supplied
        return "https://gitea.com/api/v1"

    def _headers(self) -> dict:
        return {
            "Accept": "application/json",
            "Authorization": f"token {self._token}",
        }

    def _supports_draft_flag(self) -> bool:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# GitLab
# ─────────────────────────────────────────────────────────────────────────────

class GitlabProposer(ChangeProposer):
    """GitLab REST v4: branch → commit via Files API → draft MR."""

    provider = "gitlab"

    def _default_api(self) -> str:
        return "https://gitlab.com/api/v4"

    def _headers(self) -> dict:
        return {"PRIVATE-TOKEN": self._token}

    def _project(self) -> str:
        return quote(self.repo, safe="")   # url-encoded "owner/repo"

    def propose(self, patch: PatchProposal, *, draft: bool = True,
                title: str | None = None, body: str | None = None) -> ProposedChange:
        h = self._headers()
        proj = self._project()
        branch = _branch_name(patch)
        title = title or _default_title(patch)
        body = body or _default_body(patch)

        # 1. create branch off base
        self._request(
            "POST", f"{self._api}/projects/{proj}/repository/branches"
            f"?branch={quote(branch, safe='')}&ref={quote(self.base, safe='')}",
            headers=h, ok=(201,))

        # 2. commit the patched file (create-or-update, GitLab picks the action)
        file_url = (f"{self._api}/projects/{proj}/repository/files/"
                    f"{quote(patch.file_path, safe='')}")
        commit = {
            "branch": branch,
            "content": patch.new_content,
            "commit_message": title,
        }
        resp = requests.put(file_url, headers=h, json=commit, timeout=20)
        if resp.status_code == 400:  # file does not exist yet → create
            resp = requests.post(file_url, headers=h, json=commit, timeout=20)
        if resp.status_code not in (200, 201):
            raise RuntimeError(
                f"commit {file_url} -> {resp.status_code}: {resp.text[:300]}")

        # 3. open the MR (GitLab marks drafts by a "Draft:" title prefix)
        mr_title = f"Draft: {title}" if draft else title
        mr = self._request(
            "POST", f"{self._api}/projects/{proj}/merge_requests", headers=h,
            json={
                "source_branch": branch,
                "target_branch": self.base,
                "title": mr_title,
                "description": body,
            }, ok=(201,))
        return ProposedChange(
            url=mr.get("web_url", ""), branch=branch, provider=self.provider,
            draft=draft, number=mr.get("iid"))


# ─────────────────────────────────────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────────────────────────────────────

def make_change_proposer(
    repo_url: str,
    *,
    base: str = "main",
    token: str | None = None,
    provider: str | None = None,
) -> ChangeProposer:
    """Return the right ``ChangeProposer`` for a repo URL.

    Dispatch is by host (``github.com`` / ``gitlab.com`` / gitea|forgejo), with an
    explicit ``provider`` override for self-hosted instances whose host name does
    not reveal the platform. Self-hosted GitHub/GitLab/Gitea are supported by
    passing ``provider=`` and are reached at ``https://<host>/api/<...>``.
    """
    owner_repo, host = _parse_repo(repo_url)
    kind = (provider or _detect_provider(host) or "").lower()

    if kind == "github":
        api = None if host in ("github.com", "") else f"https://{host}/api/v3"
        return GithubProposer(owner_repo, base=base, token=token, api_base=api)
    if kind == "gitea" or kind == "forgejo":
        api = f"https://{host}/api/v1" if host else None
        return GiteaProposer(owner_repo, base=base, token=token, api_base=api)
    if kind == "gitlab":
        api = None if host in ("gitlab.com", "") else f"https://{host}/api/v4"
        return GitlabProposer(owner_repo, base=base, token=token, api_base=api)

    raise ValueError(
        f"cannot infer git platform for {repo_url!r}; pass provider="
        "'github'|'gitlab'|'gitea'")


def _detect_provider(host: str) -> str | None:
    h = host.lower()
    if "github" in h:
        return "github"
    if "gitlab" in h:
        return "gitlab"
    if "gitea" in h or "forgejo" in h or "codeberg" in h:
        return "gitea"
    return None


def _parse_repo(repo_url: str) -> tuple[str, str]:
    """Return ("owner/repo", host) from an HTTPS or SSH git URL."""
    url = repo_url.strip()
    if url.startswith("git@") or (":" in url and "://" not in url and "@" in url):
        # git@host:owner/repo.git
        host, _, path = url.partition("@")[2].partition(":")
        owner_repo = path
    else:
        p = urlparse(url)
        host = p.hostname or ""
        owner_repo = p.path
    owner_repo = owner_repo.strip("/")
    if owner_repo.endswith(".git"):
        owner_repo = owner_repo[:-4]
    return owner_repo, host
