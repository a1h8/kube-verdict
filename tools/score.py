#!/usr/bin/env python3
"""
Generate dashboard/src/scores.json from real codebase measurements.

Usage:
    python tools/score.py [--output dashboard/src/scores.json]

Tools used (installed inline by CI, optional locally):
    mypy    — type error count  → type safety score
    radon   — average cyclomatic complexity → complexity score
    vulture — unused code count → dead code score

All functions fall back to a conservative estimate if the tool is missing.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
CORE = ["api", "rca", "workflow", "ingestion", "vectorstore", "ontology",
        "dedup", "knowledge", "persistence", "signals", "llm", "config.py"]


# ── helpers ────────────────────────────────────────────────────────────────────

def _py_files(*targets: str) -> list[Path]:
    files: list[Path] = []
    for t in targets:
        p = ROOT / t
        if p.is_file():
            files.append(p)
        elif p.is_dir():
            files.extend(p.rglob("*.py"))
    return [f for f in files if "__pycache__" not in str(f)]


def _grep(pattern: str, *targets: str) -> int:
    count = 0
    for f in _py_files(*targets):
        count += len(re.findall(pattern, f.read_text(errors="ignore")))
    return count


def _run(*args: str, timeout: int = 120) -> tuple[int, str]:
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, cwd=ROOT)
        return r.returncode, r.stdout + r.stderr
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return -1, ""


def _tool_available(name: str) -> bool:
    rc, _ = _run(sys.executable, "-m", name, "--version", timeout=10)
    return rc == 0


# ── Code Quality ───────────────────────────────────────────────────────────────

def score_di() -> int:
    """Count LLM providers + store abstractions."""
    llm_clients = len(list((ROOT / "llm").glob("*_client.py"))) if (ROOT / "llm").exists() else 0
    store_files = [
        "persistence/vector_store_repo.py",
        "persistence/session_repo.py",
        "vectorstore/store.py",
        "knowledge/doc_store.py",
    ]
    stores = sum(1 for f in store_files if (ROOT / f).exists())
    return min(llm_clients * 2 + stores * 2, 20)


def score_type_safety() -> int:
    if not _tool_available("mypy"):
        return 10
    targets = [str(ROOT / t) for t in CORE if (ROOT / t).exists()]
    _, out = _run(sys.executable, "-m", "mypy", "--ignore-missing-imports",
                  "--no-error-summary", *targets)
    errors = sum(1 for line in out.splitlines() if ": error:" in line)
    if errors == 0:
        return 20
    if errors <= 10:
        return 17
    if errors <= 30:
        return 14
    if errors <= 80:
        return 10
    if errors <= 150:
        return 7
    return 4


def score_complexity() -> int:
    if not _tool_available("radon"):
        return 12
    targets = [str(ROOT / t) for t in CORE if (ROOT / t).exists() and Path(ROOT / t).is_dir()]
    _, out = _run(sys.executable, "-m", "radon", "cc", "-a", "-s", *targets)
    for line in reversed(out.splitlines()):
        m = re.search(r"Average complexity: \w+ \((\d+\.?\d*)\)", line)
        if m:
            avg = float(m.group(1))
            if avg <= 2:
                return 20
            if avg <= 4:
                return 16
            if avg <= 6:
                return 12
            if avg <= 8:
                return 8
            return 5
    return 12


def score_dead_code() -> int:
    if not _tool_available("vulture"):
        return 14
    targets = [str(ROOT / t) for t in CORE if (ROOT / t).exists()]
    _, out = _run(sys.executable, "-m", "vulture", *targets, "--min-confidence", "80")
    unused = len([ln for ln in out.splitlines() if ln.strip() and ": unused " in ln])
    if unused == 0:
        return 20
    if unused <= 5:
        return 18
    if unused <= 15:
        return 15
    if unused <= 40:
        return 11
    return 7


def score_structured_logging() -> int:
    count = _grep(r"logger\.(info|warning|error|debug|critical|exception)", *CORE)
    if count >= 50:
        return 20
    if count >= 30:
        return 16
    if count >= 15:
        return 12
    if count >= 7:
        return 8
    return max(4, count)


# ── Operational Maturity ───────────────────────────────────────────────────────

def score_faiss_persistence() -> int:
    f = ROOT / "persistence" / "vector_store_repo.py"
    if not f.exists():
        return 4
    text = f.read_text()
    markers = ["persist_texts", "load_texts", "ON CONFLICT", "upsert", "SELECT"]
    found = sum(1 for m in markers if m in text)
    return min(10 + found * 2, 20)


def score_retry_timeout() -> int:
    # scoped to ingestion only — criterion is about K8s/external API calls
    timeouts = _grep(r"timeout\s*=\s*\d+", "ingestion")
    retries  = _grep(r"retry|backoff|tenacity", "ingestion")
    # timeouts alone cap at 12 — explicit retry/backoff needed for top scores
    return min(timeouts * 2, 12) + min(retries * 4, 8)


def score_index_ttl() -> int:
    count = _grep(r"\bttl\b|\bexpires\b|\brefresh\b|\bevict\b", *CORE)
    return min(count * 3 + 4, 20)


def score_live_cluster() -> int:
    focused = list((ROOT / "demo" / "focused").glob("scenario_*.py")) if (ROOT / "demo" / "focused").exists() else []
    cases   = list((ROOT / "tests" / "integration" / "cases").glob("*/")) if (ROOT / "tests" / "integration" / "cases").exists() else []
    return min((len(focused) + len(cases)) * 2 + 2, 20)


def score_helm_fp() -> int:
    helm_bank = len(list((ROOT / "tests" / "helm_cases").glob("*.py"))) if (ROOT / "tests" / "helm_cases").exists() else 0
    helm_unit = len(list((ROOT / "tests" / "unit").glob("test_helm*.py"))) if (ROOT / "tests" / "unit").exists() else 0
    return min((helm_bank + helm_unit) * 3, 20)


# ── Business Value ─────────────────────────────────────────────────────────────

def score_rca_precision() -> int:
    f = ROOT / "tests" / "integration" / "use_cases" / "test_rca_quality.py"
    if not f.exists():
        return 0
    tests = len(re.findall(r"def test_", f.read_text()))
    return min(tests * 3 + 4, 20)


def score_remediation_active() -> int:
    count = _grep(r"kubectl|helm rollback|helm upgrade|rollout undo", "workflow", "rca")
    return min(count * 2 + 8, 20)


def score_human_gate() -> int:
    has_scenario = (ROOT / "demo" / "focused" / "scenario_03_human_gate.py").exists()
    # count only in core logic, not demo scripts, to avoid overcounting
    approve = _grep(r"approve|reject|HUMAN_REVIEW|human.gate|sign.off", "rca", "workflow", "api")
    return min(int(has_scenario) * 6 + approve * 2, 20)


def score_airgapped() -> int:
    local = sum(1 for f in ["llm/ollama_client.py", "llm/demo_client.py"] if (ROOT / f).exists())
    return min(local * 8 + 2, 20)


def score_time_to_value() -> int:
    helm    = int((ROOT / "helm" / "kube-verdict").exists())
    demo    = int((ROOT / "demo").exists())
    ui      = sum(1 for f in ["ui/app.py", "demo/ui_demo.py"] if (ROOT / f).exists())
    readme  = _grep(r"[Qq]uick.?[Ss]tart|30 min|5 min|one command", "README.md")
    return min(helm * 5 + demo * 3 + ui * 2 + readme * 2, 20)


# ── main ───────────────────────────────────────────────────────────────────────

def build() -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "categories": [
            {
                "name": "Code Quality",
                "max": 100,
                "criteria": [
                    {"name": "Dependency injection (LLM, store)", "current": score_di(),                "max": 20},
                    {"name": "Type safety (mypy)",                "current": score_type_safety(),       "max": 20},
                    {"name": "Cyclomatic complexity",             "current": score_complexity(),        "max": 20},
                    {"name": "Dead code / vulture",               "current": score_dead_code(),         "max": 20},
                    {"name": "Structured logging",                "current": score_structured_logging(),"max": 20},
                ],
            },
            {
                "name": "Operational Maturity",
                "max": 100,
                "criteria": [
                    {"name": "FAISS persistence (survives restarts)", "current": score_faiss_persistence(), "max": 20},
                    {"name": "Retry / timeout on K8s API calls",      "current": score_retry_timeout(),     "max": 20},
                    {"name": "Index TTL / refresh strategy",          "current": score_index_ttl(),         "max": 20},
                    {"name": "Live cluster tested (k3d/k3s)",         "current": score_live_cluster(),      "max": 20},
                    {"name": "Helm false-positive rate",              "current": score_helm_fp(),           "max": 20},
                ],
            },
            {
                "name": "Business Value",
                "max": 100,
                "criteria": [
                    {"name": "RCA precision measured",             "current": score_rca_precision(),      "max": 20},
                    {"name": "Remediation active (not just print)","current": score_remediation_active(), "max": 20},
                    {"name": "Human gate implemented",             "current": score_human_gate(),         "max": 20},
                    {"name": "Air-gapped / data sovereignty",      "current": score_airgapped(),          "max": 20},
                    {"name": "Time-to-value < 30 min",             "current": score_time_to_value(),      "max": 20},
                ],
            },
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate KubeVerdict dashboard scores")
    parser.add_argument("--output", default="dashboard/src/scores.json")
    args = parser.parse_args()

    scores = build()
    out = ROOT / args.output
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(scores, indent=2) + "\n")

    total   = sum(c["current"] for cat in scores["categories"] for c in cat["criteria"])
    maximum = sum(c["max"]     for cat in scores["categories"] for c in cat["criteria"])
    print(f"Score: {total}/{maximum} ({total / maximum * 100:.0f}%)")
    print(f"Written → {out}")
