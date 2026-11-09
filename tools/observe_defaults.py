"""Observe the defaults the API server applies to built-in kinds (docs/schema-registry.md, step 6).

Kubernetes' OpenAPI v3 holds almost none of them: they are applied by the server's code. This script submits the
minimal objects of ``ontology/spec/upstream/minimal/`` with ``kubectl create --dry-run=server -o json`` and records
every path the server added, with its value, into the bundle (``observed-defaults/``) and its lock. Nothing is created.

    python -m tools.observe_defaults --context k3d-dev --namespace kv-defaults

Run once per server version, in the planned live session, in a namespace with no LimitRange and no mutating webhook.
The server's built-in admission plugins (a default StorageClass, the ServiceAccount token volume, default tolerations)
also add values; they are recorded as observed, and the server version and context say where.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

from tools.fetch_specs import BUNDLE, read_lock

ROOT = Path(__file__).resolve().parent.parent
MINIMAL = ROOT / "ontology" / "spec" / "upstream" / "minimal"
SOURCE = "observed-defaults"
# set by the server for every object, not defaults of the kind
SERVER_SET = {"metadata.uid", "metadata.creationTimestamp", "metadata.resourceVersion", "metadata.generation",
              "metadata.managedFields", "metadata.namespace", "metadata.selfLink"}


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    """Leaf paths. A list item is named by its ``name`` when it has one (as list keys do for containers, ports and
    volumes), else by its index."""
    out: dict[str, Any] = {}
    if isinstance(value, dict) and value:
        for k, v in value.items():
            out.update(flatten(v, f"{prefix}.{k}" if prefix else k))
    elif isinstance(value, list) and value:
        for i, item in enumerate(value):
            tag = f"name={item['name']}" if isinstance(item, dict) and "name" in item else str(i)
            out.update(flatten(item, f"{prefix}[{tag}]"))
    else:
        out[prefix] = value
    return out


def added(submitted: dict, answered: dict) -> dict[str, Any]:
    """Paths the server's answer has and the submitted object had not, without status and server-set metadata."""
    before = flatten(submitted)
    out = {}
    for path, v in flatten(answered).items():
        if path in before or path.startswith("status") or any(path == s or path.startswith(s + ".")
                                                               or path.startswith(s + "[") for s in SERVER_SET):
            continue
        out[path] = v
    return out


Runner = Callable[[list[str], bytes | None], bytes]


def _run(cmd: list[str], stdin: bytes | None = None) -> bytes:
    return subprocess.run(cmd, input=stdin, capture_output=True, check=True, timeout=60).stdout


def observe(context: str, namespace: str, out: Path, minimal: Path = MINIMAL, run: Runner = _run,
            now: str | None = None) -> dict:
    if not context:
        raise ValueError("an explicit --context is required: the defaults are those of a named server")
    version = json.loads(run(["kubectl", "--context", context, "version", "-o", "json"], None))
    server = str((version.get("serverVersion") or {}).get("gitVersion", "unknown"))
    dest = out / SOURCE
    dest.mkdir(parents=True, exist_ok=True)
    files = {}
    for path in sorted(minimal.glob("*.yaml")):
        raw = path.read_bytes()
        submitted = yaml.safe_load(raw)
        answered = json.loads(run(["kubectl", "--context", context, "-n", namespace, "create", "--dry-run=server",
                                   "-o", "json", "-f", "-"], raw))
        record = {"apiVersion": submitted["apiVersion"], "kind": submitted["kind"], "input": path.name,
                  "input_sha256": _sha(raw), "server": server, "defaults": added(submitted, answered)}
        data = (json.dumps(record, indent=1, sort_keys=True, default=str) + "\n").encode()
        (dest / f"{path.stem}.json").write_bytes(data)
        files[f"{path.stem}.json"] = _sha(data)
    lock = read_lock(out)
    entry = {"name": SOURCE, "repo": f"kube-apiserver {server} via context {context}", "requested_tag": server,
             "tag": server, "format": SOURCE, "location": f"kubectl create --dry-run=server, namespace {namespace}",
             "licence": "observed", "files": files, "skipped": [],
             "observed_at": now or datetime.now(timezone.utc).isoformat(timespec="seconds")}
    lock.setdefault("version", 1)
    lock["sources"] = sorted([s for s in lock.get("sources", []) if s["name"] != SOURCE] + [entry],
                             key=lambda s: s["name"])
    (out / "lock.yaml").write_text(yaml.safe_dump(lock, sort_keys=False))
    return entry


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--context", required=True)
    ap.add_argument("--namespace", required=True)
    ap.add_argument("--out", type=Path, default=BUNDLE)
    a = ap.parse_args(argv)
    entry = observe(a.context, a.namespace, a.out)
    print(f"{entry['tag']}: {len(entry['files'])} kind(s) observed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
