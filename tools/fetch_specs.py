"""Build the bundle of upstream definitions the product reads (docs/schema-registry.md § 1, source 3).

Run on a machine with network access; the bundle is then carried to where the product runs, as for an air-gapped
install. The product itself never downloads a definition.

    python -m tools.fetch_specs                         # fetch every source of ontology/spec/upstream/sources.yaml
    python -m tools.fetch_specs --only argo-cd          # one source
    python -m tools.fetch_specs --verify                # offline: check a bundle against its lock
    python -m tools.fetch_specs --update                # accept files that differ from the lock

Every directory is listed through the GitHub API: no file name is guessed, and a directory or asset that does not
exist at the tag stops the run. ``GITHUB_TOKEN``, when set, raises the API's rate limit; nothing else is sent.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import yaml

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "ontology" / "spec" / "upstream" / "sources.yaml"
BUNDLE = ROOT / "ontology" / "spec" / "upstream" / "bundle"
API = "https://api.github.com"
USER_AGENT = "kube-verdict-fetch-specs"
FORMATS = {"openapi-v3", "crd"}


class FetchError(RuntimeError):
    pass


def _http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    token = os.environ.get("GITHUB_TOKEN")
    if token and url.startswith(API):
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()
    except Exception as exc:  # noqa: BLE001 - reported with the URL, the run stops
        raise FetchError(f"{url}: {exc}") from exc


class GitHub:
    def __init__(self, get: Callable[[str], bytes] = _http_get):
        self._get = get

    def json(self, path: str):
        return json.loads(self._get(f"{API}{path}"))

    def raw(self, url: str) -> bytes:
        return self._get(url)

    def resolve_tag(self, repo: str, tag: str) -> str:
        return str(self.json(f"/repos/{repo}/releases/latest")["tag_name"]) if tag == "latest" else tag

    def list_dir(self, repo: str, path: str, tag: str) -> list[dict]:
        entries = self.json(f"/repos/{repo}/contents/{path}?ref={tag}")
        if not isinstance(entries, list):
            raise FetchError(f"{repo}/{path}@{tag} is not a directory")
        return [e for e in entries if e.get("type") == "file"]

    def asset(self, repo: str, tag: str, name: str) -> dict:
        rel = self.json(f"/repos/{repo}/releases/tags/{tag}")
        found = [a for a in rel.get("assets", []) if a.get("name") == name]
        if not found:
            raise FetchError(f"{repo}@{tag} has no release asset {name!r}")
        return {"name": name, "download_url": found[0]["browser_download_url"]}

    def licence(self, repo: str) -> str:
        try:
            return str(self.json(f"/repos/{repo}/license")["license"]["spdx_id"])
        except (FetchError, KeyError, TypeError):
            return "unknown"


def load_sources(path: Path = SOURCES) -> list[dict]:
    doc = yaml.safe_load(path.read_text()) or {}
    why = []
    for i, s in enumerate(doc.get("sources") or []):
        if not s.get("name") or not s.get("repo") or not s.get("tag"):
            why.append(f"sources[{i}]: name, repo and tag are required")
        if s.get("format") not in FORMATS:
            why.append(f"sources[{i}]: format must be one of {sorted(FORMATS)}")
        if bool(s.get("dir")) == bool(s.get("asset")):
            why.append(f"sources[{i}]: exactly one of dir or asset")
    if why:
        raise FetchError("; ".join(why))
    return list(doc["sources"])


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_crd(data: bytes) -> bool:
    try:
        return any(isinstance(d, dict) and d.get("kind") == "CustomResourceDefinition"
                   for d in yaml.safe_load_all(data.decode()))
    except yaml.YAMLError:
        return False


def fetch_source(gh: GitHub, src: dict, out: Path) -> dict:
    """Download one source into ``out/<name>/`` and return its lock entry."""
    tag = gh.resolve_tag(src["repo"], src["tag"])
    if src.get("asset"):
        entries = [gh.asset(src["repo"], tag, src["asset"])]
    else:
        entries = [e for e in gh.list_dir(src["repo"], src["dir"], tag)
                   if fnmatch.fnmatch(e["name"], src.get("match", "*"))]
    if not entries:
        raise FetchError(f"{src['name']}: no file matches at {tag}")
    files, skipped = {}, []
    dest = out / src["name"]
    dest.mkdir(parents=True, exist_ok=True)
    for e in sorted(entries, key=lambda x: x["name"]):
        data = gh.raw(e["download_url"])
        if src["format"] == "crd" and not _is_crd(data):
            skipped.append(e["name"])          # e.g. a kustomization.yaml next to the CRDs
            continue
        (dest / e["name"]).write_bytes(data)
        files[e["name"]] = _sha(data)
    return {"name": src["name"], "repo": src["repo"], "requested_tag": src["tag"], "tag": tag,
            "format": src["format"], "location": src.get("dir") or f"release asset {src['asset']}",
            "licence": gh.licence(src["repo"]), "files": files, "skipped": skipped}


def read_lock(out: Path) -> dict:
    p = out / "lock.yaml"
    return yaml.safe_load(p.read_text()) if p.exists() else {"sources": []}


def verify(out: Path) -> list[str]:
    """Offline: every file of the lock is present with its sha256, and no file is in the bundle without the lock."""
    lock, problems, listed = read_lock(out), [], set()
    for s in lock.get("sources", []):
        for name, sha in s["files"].items():
            p = out / s["name"] / name
            listed.add(p)
            if not p.exists():
                problems.append(f"missing {s['name']}/{name}")
            elif _sha(p.read_bytes()) != sha:
                problems.append(f"sha256 differs {s['name']}/{name}")
    for p in out.glob("*/*"):
        if p.is_file() and p not in listed:
            problems.append(f"not in the lock {p.relative_to(out)}")
    return problems


def fetch(sources: list[dict], out: Path, gh: GitHub, update: bool = False, now: str | None = None) -> dict:
    """Fetch the sources; a file that differs from the existing lock at the same tag is refused unless ``update``."""
    old = {s["name"]: s for s in read_lock(out).get("sources", [])}
    entries = []
    for src in sources:
        entry = fetch_source(gh, src, out)
        prev = old.get(src["name"])
        if prev and prev["tag"] == entry["tag"] and prev["files"] != entry["files"] and not update:
            raise FetchError(f"{src['name']}@{entry['tag']}: files differ from the lock (use --update)")
        entries.append(entry)
    names = {e["name"] for e in entries}
    lock = {"version": 1, "fetched_at": now or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sources": [s for s in old.values() if s["name"] not in names] + entries}
    lock["sources"].sort(key=lambda s: s["name"])
    (out / "lock.yaml").write_text(yaml.safe_dump(lock, sort_keys=False))
    return lock


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=BUNDLE)
    ap.add_argument("--sources", type=Path, default=SOURCES)
    ap.add_argument("--only", action="append", default=[])
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--update", action="store_true")
    a = ap.parse_args(argv)
    if a.verify:
        problems = verify(a.out)
        print("\n".join(problems) or "bundle matches its lock")
        return 1 if problems else 0
    sources = [s for s in load_sources(a.sources) if not a.only or s["name"] in a.only]
    a.out.mkdir(parents=True, exist_ok=True)
    try:
        lock = fetch(sources, a.out, GitHub(), update=a.update)
    except FetchError as exc:
        print(f"stopped: {exc}", file=sys.stderr)
        return 2
    for s in lock["sources"]:
        print(f"{s['name']:28} {s['tag']:12} {s['licence']:12} {len(s['files'])} file(s), skipped {s['skipped']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
