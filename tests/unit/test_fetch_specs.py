"""tools/fetch_specs.py, with the HTTP layer faked: no network (docs/schema-registry.md § 1)."""
import json

import pytest
import yaml

from tools.fetch_specs import API, FetchError, GitHub, fetch, load_sources, verify

CRD = b"apiVersion: apiextensions.k8s.io/v1\nkind: CustomResourceDefinition\nmetadata: {name: apps.argoproj.io}\n"
KUSTOMIZATION = b"apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\nresources: [a.yaml]\n"


def fake(routes: dict[str, bytes | object]):
    calls = []

    def get(url):
        calls.append(url)
        if url not in routes:
            raise FetchError(f"{url}: 404")
        v = routes[url]
        return v if isinstance(v, bytes) else json.dumps(v).encode()
    return get, calls


def argo_routes(tag="v3.1.0", crd=CRD):
    return {
        f"{API}/repos/argoproj/argo-cd/releases/latest": {"tag_name": tag},
        f"{API}/repos/argoproj/argo-cd/contents/manifests/crds?ref={tag}": [
            {"type": "file", "name": "application-crd.yaml", "download_url": "https://raw/app"},
            {"type": "file", "name": "kustomization.yaml", "download_url": "https://raw/kust"},
            {"type": "dir", "name": "sub", "download_url": None},
            {"type": "file", "name": "README.md", "download_url": "https://raw/readme"}],
        "https://raw/app": crd, "https://raw/kust": KUSTOMIZATION,
        f"{API}/repos/argoproj/argo-cd/license": {"license": {"spdx_id": "Apache-2.0"}},
    }


ARGO = {"name": "argo-cd", "repo": "argoproj/argo-cd", "tag": "latest", "dir": "manifests/crds", "match": "*.yaml",
        "format": "crd"}


def test_latest_resolved_listed_files_only_crds_kept(tmp_path):
    get, calls = fake(argo_routes())
    lock = fetch([ARGO], tmp_path, GitHub(get), now="2026-11-09T00:00:00+00:00")
    s = lock["sources"][0]
    assert s["tag"] == "v3.1.0" and s["requested_tag"] == "latest" and s["licence"] == "Apache-2.0"
    assert list(s["files"]) == ["application-crd.yaml"]          # README filtered by match, kustomization not a CRD
    assert s["skipped"] == ["kustomization.yaml"]
    assert (tmp_path / "argo-cd" / "application-crd.yaml").read_bytes() == CRD
    assert "https://raw/readme" not in calls
    assert yaml.safe_load((tmp_path / "lock.yaml").read_text())["sources"][0]["files"] == s["files"]
    assert verify(tmp_path) == []


def test_pinned_tag_is_not_resolved(tmp_path):
    get, calls = fake(argo_routes(tag="v2.0.0"))
    fetch([{**ARGO, "tag": "v2.0.0"}], tmp_path, GitHub(get))
    assert not any(c.endswith("/releases/latest") for c in calls)


def test_missing_directory_stops(tmp_path):
    get, _ = fake({f"{API}/repos/argoproj/argo-cd/releases/latest": {"tag_name": "v3.1.0"}})
    with pytest.raises(FetchError):
        fetch([ARGO], tmp_path, GitHub(get))


def test_no_matching_file_stops(tmp_path):
    get, _ = fake(argo_routes())
    with pytest.raises(FetchError, match="no file matches"):
        fetch([{**ARGO, "match": "*.json"}], tmp_path, GitHub(get))


def test_release_asset(tmp_path):
    get, _ = fake({f"{API}/repos/cm/cm/releases/latest": {"tag_name": "v1.18.0"},
                   f"{API}/repos/cm/cm/releases/tags/v1.18.0": {"assets": [
                       {"name": "other.yaml", "browser_download_url": "https://dl/other"},
                       {"name": "cert-manager.crds.yaml", "browser_download_url": "https://dl/crds"}]},
                   "https://dl/crds": CRD})
    lock = fetch([{"name": "cert-manager", "repo": "cm/cm", "tag": "latest", "asset": "cert-manager.crds.yaml",
                   "format": "crd"}], tmp_path, GitHub(get))
    assert list(lock["sources"][0]["files"]) == ["cert-manager.crds.yaml"]
    assert lock["sources"][0]["licence"] == "unknown"              # the licence call failed: said, not guessed


def test_changed_file_at_same_tag_refused_unless_update(tmp_path):
    get, _ = fake(argo_routes())
    fetch([ARGO], tmp_path, GitHub(get))
    get2, _ = fake(argo_routes(crd=CRD + b"# changed\n"))
    with pytest.raises(FetchError, match="differ from the lock"):
        fetch([ARGO], tmp_path, GitHub(get2))
    fetch([ARGO], tmp_path, GitHub(get2), update=True)
    assert verify(tmp_path) == []


def test_verify_offline_detects_tamper_missing_and_extra(tmp_path):
    get, _ = fake(argo_routes())
    fetch([ARGO], tmp_path, GitHub(get))
    (tmp_path / "argo-cd" / "application-crd.yaml").write_bytes(CRD + b"x")
    (tmp_path / "argo-cd" / "extra.yaml").write_bytes(CRD)
    problems = verify(tmp_path)
    assert "sha256 differs argo-cd/application-crd.yaml" in problems
    assert "not in the lock argo-cd/extra.yaml" in problems
    (tmp_path / "argo-cd" / "application-crd.yaml").unlink()
    assert "missing argo-cd/application-crd.yaml" in verify(tmp_path)


def test_other_sources_kept_in_lock(tmp_path):
    get, _ = fake(argo_routes())
    fetch([ARGO], tmp_path, GitHub(get))
    get2, _ = fake({f"{API}/repos/x/y/contents/d?ref=v1": [{"type": "file", "name": "a.json",
                                                            "download_url": "https://raw/a"}],
                    "https://raw/a": b"{}"})
    lock = fetch([{"name": "k", "repo": "x/y", "tag": "v1", "dir": "d", "match": "*.json", "format": "openapi-v3"}],
                 tmp_path, GitHub(get2))
    assert [s["name"] for s in lock["sources"]] == ["argo-cd", "k"]


def test_sources_file_is_valid_and_checked(tmp_path):
    names = [s["name"] for s in load_sources()]
    assert "kubernetes" in names and "argo-cd" in names
    bad = tmp_path / "s.yaml"
    bad.write_text("sources: [{name: a, repo: r, tag: t, format: crd, dir: d, asset: x}]")
    with pytest.raises(FetchError, match="exactly one of dir or asset"):
        load_sources(bad)
    bad.write_text("sources: [{name: a, repo: r, tag: t, format: json, dir: d}]")
    with pytest.raises(FetchError, match="format"):
        load_sources(bad)
