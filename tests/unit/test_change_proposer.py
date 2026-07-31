"""PR/MR-first phase 2 — open a PatchProposal as a draft PR/MR (offline).

All platform HTTP is stubbed: a FakeTransport records requests and returns canned
responses per (method, url-substring), so the branch → commit → PR/MR flow is
validated without any network.
"""
from __future__ import annotations

import base64

import pytest

from remediation import change_proposer as cp
from remediation.change_proposer import (
    GiteaProposer,
    GithubProposer,
    GitlabProposer,
    make_change_proposer,
)
from remediation.patch_builder import PatchProposal


def _patch() -> PatchProposal:
    return PatchProposal(
        release="api", namespace="production", file_path="chart/values.yaml",
        changes={"replicaCount": 3},
        diff="--- a/chart/values.yaml\n+++ b/chart/values.yaml\n"
             "@@\n-replicaCount: 1\n+replicaCount: 3\n",
        new_content="replicaCount: 3\n",
        source_remediation=["helm upgrade api ./chart -n production --set replicaCount=3"],
    )


class _Resp:
    def __init__(self, status: int, payload: dict | None = None, text: str = ""):
        self.status_code = status
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class FakeTransport:
    """Records calls and answers by (method, url-substring)."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[tuple[str, str, dict | None]] = []

    def _match(self, method: str, url: str):
        for (m, frag), resp in self.routes.items():
            if m == method and frag in url:
                return resp
        raise AssertionError(f"unrouted {method} {url}")

    def request(self, method, url, headers=None, json=None, timeout=None):
        self.calls.append((method, url, json))
        return self._match(method, url)

    def get(self, url, headers=None, timeout=None):
        self.calls.append(("GET", url, None))
        return self._match("GET", url)

    def put(self, url, headers=None, json=None, timeout=None):
        self.calls.append(("PUT", url, json))
        return self._match("PUT", url)

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append(("POST", url, json))
        return self._match("POST", url)


@pytest.fixture
def transport(monkeypatch):
    holder = {}

    def install(routes):
        t = FakeTransport(routes)
        monkeypatch.setattr(cp, "requests", t)
        holder["t"] = t
        return t

    return install


# ── Factory dispatch ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("url,cls,provider", [
    ("https://github.com/acme/api", GithubProposer, "github"),
    ("git@github.com:acme/api.git", GithubProposer, "github"),
    ("https://gitlab.com/acme/api", GitlabProposer, "gitlab"),
    ("https://gitea.example.com/acme/api", GiteaProposer, "gitea"),
    ("https://codeberg.org/acme/api", GiteaProposer, "gitea"),
])
def test_factory_dispatches_by_host(url, cls, provider):
    p = make_change_proposer(url, token="t")
    assert isinstance(p, cls)
    assert p.provider == provider
    assert p.repo == "acme/api"


def test_factory_self_hosted_needs_explicit_provider():
    with pytest.raises(ValueError):
        make_change_proposer("https://git.internal.corp/acme/api", token="t")
    p = make_change_proposer("https://git.internal.corp/acme/api",
                             token="t", provider="gitlab")
    assert isinstance(p, GitlabProposer)
    assert p._api == "https://git.internal.corp/api/v4"


def test_token_required():
    with pytest.raises(PermissionError):
        make_change_proposer("https://github.com/acme/api")


# ── GitHub flow ──────────────────────────────────────────────────────────────

def test_github_opens_draft_pr(transport):
    t = transport({
        ("GET",  "/git/refs/heads/main"): _Resp(200, {"object": {"sha": "base123"}}),
        ("POST", "/git/refs"):            _Resp(201, {}),
        ("GET",  "/contents/chart/values.yaml"): _Resp(200, {"sha": "old456"}),
        ("PUT",  "/contents/chart/values.yaml"): _Resp(200, {}),
        ("POST", "/pulls"): _Resp(201, {"html_url": "https://github.com/acme/api/pull/7",
                                        "number": 7}),
    })
    proposer = make_change_proposer("https://github.com/acme/api", token="tok")
    change = proposer.propose(_patch())

    assert change.provider == "github"
    assert change.url.endswith("/pull/7")
    assert change.number == 7
    assert change.draft is True
    assert change.branch.startswith("kubeverdict/fix-api-")

    # branch created off the base sha
    post_refs = next(j for m, u, j in t.calls if m == "POST" and "/git/refs" in u)
    assert post_refs["sha"] == "base123"
    assert post_refs["ref"] == f"refs/heads/{change.branch}"

    # file committed with base64 of the patched content, on the new branch
    put = next(j for m, u, j in t.calls if m == "PUT" and "/contents/" in u)
    assert base64.b64decode(put["content"]).decode() == "replicaCount: 3\n"
    assert put["branch"] == change.branch
    assert put["sha"] == "old456"  # updates the existing file

    # PR opened as a real draft, body carries the diff
    pr = next(j for m, u, j in t.calls if m == "POST" and "/pulls" in u)
    assert pr["draft"] is True
    assert pr["head"] == change.branch
    assert pr["base"] == "main"
    assert "replicaCount: 3" in pr["body"]


def test_github_creates_file_when_absent(transport):
    t = transport({
        ("GET",  "/git/refs/heads/main"): _Resp(200, {"object": {"sha": "b"}}),
        ("POST", "/git/refs"):            _Resp(201, {}),
        ("GET",  "/contents/chart/values.yaml"): _Resp(404, {}),
        ("PUT",  "/contents/chart/values.yaml"): _Resp(201, {}),
        ("POST", "/pulls"): _Resp(201, {"html_url": "u", "number": 1}),
    })
    make_change_proposer("https://github.com/acme/api", token="tok").propose(_patch())
    put = next(j for m, u, j in t.calls if m == "PUT" and "/contents/" in u)
    assert "sha" not in put  # create, not update


# ── Gitea flow (no draft flag → WIP: title) ──────────────────────────────────

def test_gitea_marks_draft_via_wip_title(transport):
    t = transport({
        ("GET",  "/git/refs/heads/main"): _Resp(200, {"object": {"sha": "b"}}),
        ("POST", "/git/refs"):            _Resp(201, {}),
        ("GET",  "/contents/chart/values.yaml"): _Resp(200, {"sha": "s"}),
        ("PUT",  "/contents/chart/values.yaml"): _Resp(200, {}),
        ("POST", "/pulls"): _Resp(201, {"html_url": "https://gitea.example.com/acme/api/pulls/3",
                                        "number": 3}),
    })
    proposer = make_change_proposer("https://gitea.example.com/acme/api", token="tok")
    change = proposer.propose(_patch())
    assert change.provider == "gitea"
    pr = next(j for m, u, j in t.calls if m == "POST" and "/pulls" in u)
    assert "draft" not in pr
    assert pr["title"].startswith("WIP:")


# ── GitLab flow ──────────────────────────────────────────────────────────────

def test_gitlab_opens_draft_mr(transport):
    t = transport({
        ("POST", "/repository/branches"): _Resp(201, {}),
        ("PUT",  "/repository/files/"):   _Resp(200, {}),
        ("POST", "/merge_requests"): _Resp(201, {"web_url": "https://gitlab.com/acme/api/-/merge_requests/9",
                                                 "iid": 9}),
    })
    proposer = make_change_proposer("https://gitlab.com/acme/api", token="tok")
    change = proposer.propose(_patch())

    assert change.provider == "gitlab"
    assert change.url.endswith("/merge_requests/9")
    assert change.number == 9

    # project id is url-encoded owner/repo
    branch_call = next(u for m, u, j in t.calls if m == "POST" and "/branches" in u)
    assert "acme%2Fapi" in branch_call

    commit = next(j for m, u, j in t.calls if m == "PUT" and "/files/" in u)
    assert commit["content"] == "replicaCount: 3\n"
    assert commit["branch"] == change.branch

    mr = next(j for m, u, j in t.calls if m == "POST" and "/merge_requests" in u)
    assert mr["title"].startswith("Draft:")
    assert mr["source_branch"] == change.branch
    assert mr["target_branch"] == "main"


def test_gitlab_creates_file_when_absent(transport):
    t = transport({
        ("POST", "/repository/branches"): _Resp(201, {}),
        ("PUT",  "/repository/files/"):   _Resp(400, {}, text="file does not exist"),
        ("POST", "/repository/files/"):   _Resp(201, {}),
        ("POST", "/merge_requests"): _Resp(201, {"web_url": "u", "iid": 2}),
    })
    make_change_proposer("https://gitlab.com/acme/api", token="tok").propose(_patch())
    assert any(m == "POST" and "/files/" in u for m, u, j in t.calls)
