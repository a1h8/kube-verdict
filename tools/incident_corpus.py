"""The incident corpus: its records, what a record must hold, and its hash — docs/incident-corpus.md
(and its Amendment 2026-10-24).

The rules below were fixed before any record was written; a record that breaks one fails
``tests/unit/test_incident_corpus.py``.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
RECORDS = ROOT / "corpus/incidents"

# docs/incident-corpus.md § Fields (controlled, extended only by editing that file)
FAMILIES = frozenset({
    "crashloop-dependency", "image-pull", "oom", "config-missing", "rbac", "network-policy",
    "scheduling", "quota", "probe", "init-container", "storage", "hpa", "dns", "certificate", "etcd",
    "node", "rollout", "gitops-drift", "helm-drift", "slo", "crashloop", "dependency", "job",
    "resources", "control-plane", "negative"})
SIGNALS = frozenset({"k8s_events", "prometheus", "loki", "otel", "helm", "gitops"})
ORIGINS = frozenset({"synthetic", "public", "enterprise"})
SOURCE_KINDS = frozenset({"legacy-case", "runbook", "failure-story", "approved-run"})
REQUIRED = ("id", "title", "family", "origin", "source", "symptoms", "signals", "kinds",
            "root_cause", "cause_location", "evidence", "remediation")

# A symptom says what is observed, never why ("a symptom that names the cause leaks the answer").
# Words that explain, for every family, then per family. Kubernetes' own reasons (OOMKilled,
# CreateContainerConfigError, FailedScheduling, Evicted, forbidden) are observations and allowed.
CAUSE_WORDS = ("drift", "declared", "because", "caused by", "due to", "root cause", "misconfigur",
               "wrong", "typo", "should be", "instead of")
FAMILY_CAUSE_WORDS = {
    "oom": ("memory limit", "limit is", "too low", "too small"),
    "config-missing": ("missing", "does not exist", "doesn't exist", "deleted", "never created",
                       "not found", "absent"),
    "storage": ("storageclass", "storage class", "does not exist", "not found"),
    "scheduling": ("no node", "label", "not provisioned"),
    "node": ("temp files", "scratch", "accumulat", "fills the disk", "no size limit"),
    "quota": ("too low", "raise", "hard limit"),
    "rbac": ("role", "binding", "permission", "not granted"),
    "probe": ("port", "points at", "health endpoint"),
    "init-container": ("database name", "db_name", "env var", "environment variable"),
    "helm-drift": ("replicacount", "values file", "scaled by hand", "manual"),
}
READ_ONLY = re.compile(r"^\s*kubectl\s+(get|describe|logs|top|events|explain)\b")
_ID = re.compile(r"^inc-\d{4}$")


def load_records(records: Path = RECORDS) -> list[tuple[Path, dict]]:
    return [(p, yaml.safe_load(p.read_text()) or {}) for p in sorted(records.glob("*.yaml"))] \
        if records.is_dir() else []


def problems(path: Path, rec: dict, root: Path = ROOT) -> list[str]:
    """What a record breaks of docs/incident-corpus.md § Fields and of the cause-word rule."""
    out = [f"missing {k}" for k in REQUIRED if not rec.get(k)]
    if rec.get("id") and (not _ID.match(str(rec["id"])) or path.stem != rec["id"]):
        out.append(f"id {rec['id']!r} is not inc-NNNN or not the file name")
    if rec.get("family") and rec["family"] not in FAMILIES:
        out.append(f"family {rec['family']!r} not in the controlled list")
    if rec.get("origin") and rec["origin"] not in ORIGINS:
        out.append(f"origin {rec['origin']!r}")
    source = rec.get("source") or {}
    if source.get("kind") not in SOURCE_KINDS:
        out.append(f"source.kind {source.get('kind')!r}")
    ref = str(source.get("ref") or "")
    if not ref.startswith("http") and not (root / ref).exists():
        out.append(f"source.ref {ref!r} does not resolve")
    if len(rec.get("symptoms") or []) < 2:
        out.append("fewer than 2 symptoms")
    if bad := sorted(set(rec.get("signals") or []) - SIGNALS):
        out.append(f"signals {bad}")
    words = CAUSE_WORDS + FAMILY_CAUSE_WORDS.get(rec.get("family"), ())
    for s in rec.get("symptoms") or []:
        if hits := [w for w in words if w in s.lower()]:
            out.append(f"symptom names the cause ({', '.join(hits)}): {s!r}")
    remediation = rec.get("remediation") or []
    if remediation and all(READ_ONLY.match(c) for c in remediation):
        out.append("remediation is read-only checks only")
    if not rec.get("rollback") and not rec.get("rollback_note"):
        out.append("no rollback and no rollback_note")
    return out


def corpus_hash(records: Path = RECORDS) -> str | None:
    """SHA-256 of the record files sorted by name (name and content); None without a record."""
    files = sorted(records.glob("*.yaml")) if records.is_dir() else []
    if not files:
        return None
    h = hashlib.sha256()
    for p in files:
        h.update(p.name.encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()
