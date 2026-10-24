#!/usr/bin/env python3
"""Which corpus candidates are kept apart from the native cases — docs/incident-corpus.md
§ Amendment 2026-10-24 (protocol 001, amendments 2026-10-23 and 2026-10-24).

A candidate is excluded if (1) its source is a legacy case with the name of a native case,
(2) it names a resource of a native case's fixtures (workload, pod-name prefix, ConfigMap,
Secret, Service, or a namespace outside the generic list), (3) one of its observable lines (a log
line, an event message) appears verbatim in a native case's fixtures, or (4) its ``source.ref``
points into the native cases. Rules 2 and 3 apply to a legacy source before it is rewritten
(its structured fields) and to a record after (its whole text).

    python tools/corpus_exclusions.py [--out corpus/EXCLUSIONS.md]

Exits 1 when a committed record is excluded.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
NATIVE = ROOT / "tests/integration/cases"
LEGACY = ROOT / "cases"
RECORDS = ROOT / "corpus/incidents"

# name an environment, not a case: a closed list (CPI-20), extended only by a dated amendment
GENERIC_NAMESPACES = frozenset({"default", "production", "staging", "monitoring", "kube-system"})
FIXTURE_DIRS = ("kube", "helm", "chart", "rendered", "policy", "prometheus", "loki", "otel")
MIN_LINE = 20               # a shorter line ("connection refused") is not a symptom of its own

WORKLOADS = {"Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job", "CronJob"}
NAMED_KINDS = WORKLOADS | {"Pod", "ConfigMap", "Secret", "Service"}
_REF = re.compile(r"^([A-Z][A-Za-z]+)/(?:([a-z0-9][a-z0-9-]*)/)?([a-z0-9][a-z0-9.-]*)")
_HASHED = re.compile(r"^(.+?)-(?=[a-z0-9]*\d)[a-z0-9]{6,10}(?:-[a-z0-9]{5})?$")  # rs hash [+ pod]
_SUFFIX = re.compile(r"^(.+?)-(?:\d+|(?=[a-z]*\d)[a-z0-9]{5})$")                 # ordinal, pod id
_TOKEN = re.compile(r"[a-z0-9][a-z0-9._-]*")
_NAMESPACE = re.compile(r"(?:-n|--namespace)[ =]([a-z0-9-]+)|namespace[: ]+([a-z0-9-]+)"
                        r"|\.([a-z0-9-]+)\.svc\b")


def pod_prefix(name: str) -> str:
    """The workload a pod or ReplicaSet name belongs to: hash, pod id or ordinal removed."""
    name = name.lower()
    m = _HASHED.match(name) or _SUFFIX.match(name)
    return m.group(1) if m else name


@dataclass
class Facts:
    """What a fixture or a source names: resources, namespaces, observable lines."""
    names: set[str] = field(default_factory=set)
    namespaces: set[str] = field(default_factory=set)
    lines: set[str] = field(default_factory=set)

    def add_name(self, kind: str, name: str) -> None:
        if name:
            self.names.add(pod_prefix(name) if kind in {"Pod", "ReplicaSet"} else name.lower())

    def add_namespace(self, ns: str) -> None:
        if ns:
            self.namespaces.add(ns.lower())

    def add_line(self, text: str) -> None:
        line = " ".join(str(text).split())
        if len(line) >= MIN_LINE:
            self.lines.add(line)


def _walk(o, facts: Facts, key: str = "") -> None:
    """Structured fields only: object names by kind, references, labels that name a workload,
    namespaces, event messages and log lines. Prose (a case's notes, a symptom) is not read."""
    if isinstance(o, dict):
        kind = o.get("kind")
        meta = o.get("metadata") if isinstance(o.get("metadata"), dict) else None
        if kind in NAMED_KINDS and meta:
            facts.add_name(kind, str(meta.get("name") or ""))
        if meta:
            facts.add_namespace(str(meta.get("namespace") or ""))
        if kind in NAMED_KINDS and isinstance(o.get("name"), str):   # involvedObject, ownerReferences
            facts.add_name(kind, o["name"])
        for k, v in o.items():
            if isinstance(v, str):
                _leaf(k, v, facts)
            elif k in ("secretRef", "configMapRef", "secretKeyRef", "configMapKeyRef", "configMap") \
                    and isinstance(v, dict) and isinstance(v.get("name"), str):
                facts.add_name("Secret" if "ecret" in k else "ConfigMap", v["name"])
            elif k == "values" and isinstance(v, list):                 # a Loki stream
                for pair in v:
                    if isinstance(pair, list) and len(pair) == 2 and isinstance(pair[1], str):
                        facts.add_line(pair[1])
                _walk(v, facts, k)
            else:
                _walk(v, facts, k)
    elif isinstance(o, list):
        for v in o:
            if isinstance(v, str):
                _leaf(key, v, facts)
            else:
                _walk(v, facts, key)


def _leaf(key: str, value: str, facts: Facts) -> None:
    if m := _REF.match(value):
        kind, ns, name = m.groups()
        if kind in NAMED_KINDS:
            facts.add_name(kind, name)
        if ns:
            facts.add_namespace(ns)
    if key in ("namespace", "k8s_namespace_name"):
        facts.add_namespace(value)
    elif key in ("release", "service", "service_name", "app", "app.kubernetes.io/name",
                 "app.kubernetes.io/instance", "deployment", "configmap", "secret", "secretName"):
        facts.add_name("", value)
    elif key in ("pod", "k8s_pod_name", "pod_name"):
        facts.add_name("Pod", value)
    elif key in ("message", "error_message"):
        facts.add_line(value)


def _load(path: Path) -> list:
    """The documents of a JSON or YAML file; [] for a template or a file that does not parse."""
    try:
        text = path.read_text()
        if path.suffix == ".json":
            return [json.loads(text)]
        if path.suffix in (".yaml", ".yml"):
            return [d for d in yaml.safe_load_all(text) if d is not None]
    except (ValueError, yaml.YAMLError, UnicodeDecodeError):
        pass
    return []


def facts_of(paths: list[Path]) -> Facts:
    facts = Facts()
    for p in paths:
        for doc in _load(p):
            _walk(doc, facts)
    return facts


def native_facts(native: Path = NATIVE) -> dict[str, Facts]:
    """Per native case, what its fixtures name (never ``expect.json``)."""
    return {d.name: facts_of([p for sub in FIXTURE_DIRS if (d / sub).is_dir()
                              for p in sorted((d / sub).rglob("*")) if p.is_file()])
            for d in sorted(native.glob("h0*")) if d.is_dir()}


def legacy_sources(legacy: Path = LEGACY) -> list[Path]:
    return sorted([d for d in legacy.glob("0*") if d.is_dir()]
                  + [d for d in (legacy / "helm_cases").glob("*") if d.is_dir()])


@dataclass
class Verdict:
    candidate: str
    kept: bool
    rule: str = ""
    match: str = ""


def _overlap(facts: Facts, native: dict[str, Facts]) -> tuple[str, str] | None:
    """(rule, matching text) of the first native case the facts collide with, or None."""
    for case, nf in native.items():
        if hits := sorted(facts.names & nf.names):
            return "2 resource", f"{', '.join(hits)} ({case})"
        if hits := sorted((facts.namespaces - GENERIC_NAMESPACES) & (nf.namespaces - GENERIC_NAMESPACES)):
            return "2 namespace", f"{', '.join(hits)} ({case})"
        for line in facts.lines:
            for other in nf.lines:
                if line in other or other in line:
                    return "3 verbatim line", f"{line[:80]} ({case})"
    return None


def check_source(src: Path, native: dict[str, Facts]) -> Verdict:
    """A legacy case, before it is rewritten: its structured fields (never expect.json)."""
    label = src.relative_to(src.parents[1] if src.parent.name == "helm_cases" else src.parent).as_posix()
    label = f"cases/{label}"
    if f"h{src.name}" in native:
        return Verdict(label, False, "1 name of a native case", f"h{src.name}")
    hit = _overlap(facts_of([p for p in sorted(src.rglob("*")) if p.is_file() and p.name != "expect.json"]),
                   native)
    return Verdict(label, hit is None, *(hit or ("", "")))


def record_facts(record: dict) -> Facts:
    """A record's whole text: every token is a candidate name (a pod name by its prefix, a DNS
    name by its first label); namespaces where the text says so; symptoms and evidence as lines."""
    facts = Facts()
    strings: list[str] = []
    _strings(record, strings)
    for s in strings:
        for tok in _TOKEN.findall(s.lower()):
            tok = tok.strip("._-")
            for name in {tok, tok.split(".")[0], pod_prefix(tok), pod_prefix(tok.split(".")[0])}:
                facts.names.add(name)
        for m in _NAMESPACE.finditer(s.lower()):
            facts.add_namespace(next(g for g in m.groups() if g))
    loc = str(record.get("cause_location") or "")
    if loc.count("/") >= 2:
        facts.add_namespace(loc.split("/")[0])
    for key in ("symptoms", "evidence"):
        for s in record.get(key) or []:
            facts.add_line(s)
    return facts


def _strings(o, out: list[str]) -> None:
    if isinstance(o, str):
        out.append(o)
    elif isinstance(o, dict):
        for v in o.values():
            _strings(v, out)
    elif isinstance(o, list):
        for v in o:
            _strings(v, out)


def check_record(path: Path, native: dict[str, Facts], sources: dict[str, Verdict]) -> Verdict:
    """A committed record: rule 4, its legacy source kept, then rules 2 and 3 on its text."""
    record = yaml.safe_load(path.read_text()) or {}
    source = record.get("source") or {}
    ref = str(source.get("ref") or "")
    label = f"corpus/incidents/{path.name}"
    if ref.startswith("tests/integration/cases"):
        return Verdict(label, False, "4 source in the native cases", ref)
    if source.get("kind") == "legacy-case":
        src = sources.get(ref.rstrip("/"))
        if src is None:
            return Verdict(label, False, "source not a legacy case", ref)
        if not src.kept:
            return Verdict(label, False, f"source excluded ({src.rule})", ref)
    hit = _overlap(record_facts(record), native)
    return Verdict(label, hit is None, *(hit or ("", "")))


def run(native_dir: Path = NATIVE, legacy_dir: Path = LEGACY, records_dir: Path = RECORDS
        ) -> tuple[list[Verdict], list[Verdict]]:
    native = native_facts(native_dir)
    sources = [check_source(s, native) for s in legacy_sources(legacy_dir)]
    by_ref = {v.candidate: v for v in sources}
    records = [check_record(p, native, by_ref) for p in sorted(records_dir.glob("*.yaml"))] \
        if records_dir.is_dir() else []
    return sources, records


def render(sources: list[Verdict], records: list[Verdict]) -> str:
    def rows(vs):
        return [f"| `{v.candidate}` | {'kept' if v.kept else 'excluded'} | {v.rule or '—'} | "
                f"{v.match.replace('|', '/') or '—'} |" for v in vs]
    head = ["| Candidate | Verdict | Rule | Match |", "|---|---|---|---|"]
    return "\n".join([
        "# Corpus exclusions", "",
        "Generated by `tools/corpus_exclusions.py` — do not edit. Rules: `docs/incident-corpus.md` "
        "§ Amendment 2026-10-24. Generic namespaces (never excluding): "
        + ", ".join(f"`{n}`" for n in sorted(GENERIC_NAMESPACES)) + ".", "",
        f"## Legacy sources ({sum(v.kept for v in sources)} kept of {len(sources)})", "",
        *head, *rows(sources), "",
        f"## Records ({sum(v.kept for v in records)} kept of {len(records)})", "",
        *(head + rows(records) if records else ["No record yet."]), ""])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    sources, records = run()
    text = render(sources, records)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
    print(text)
    return 0 if all(v.kept for v in records) else 1


if __name__ == "__main__":
    sys.exit(main())
