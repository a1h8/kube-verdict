"""
Case contract builder.

Reads a dialogue simulation JSON (tests/integration/use_cases/sim_results/*.json)
and generates or updates the matching case files (input.json / expect.json).

Three entry points:
  update_expect_from_sim   — rebuild expect.json from a single sim result
  update_input_from_sim    — enrich input.json anchors/symptom from the sim LLM output
  recalibrate_all          — recalibrate confidence_score_min for all cases with sim results
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# K8s vocabulary used for keyword extraction
# ---------------------------------------------------------------------------

_K8S_ERROR_REASONS = {
    "CrashLoopBackOff", "OOMKilled", "ImagePullBackOff", "ErrImagePull",
    "Pending", "Failed", "Evicted", "Terminating", "Unknown", "FailedScheduling",
    "BackOff", "ContainerCreating", "PodInitializing", "Unschedulable",
}

_K8S_CONCEPTS = {
    "drift", "limit", "request", "memory", "cpu", "restart", "probe",
    "readiness", "liveness", "image", "registry", "secret", "configmap",
    "storageclass", "volume", "mount", "port", "selector", "label", "annotation",
    "taint", "toleration", "affinity", "quota", "rbac", "dns", "ingress",
    "service", "backend", "endpoint", "network", "policy", "forbidden",
    "unauthorized", "eviction", "threshold", "pressure",
}

_K8S_COMMANDS = {"helm", "kubectl", "upgrade", "rollback", "patch", "apply", "describe"}

_ALL_K8S = _K8S_ERROR_REASONS | _K8S_CONCEPTS | _K8S_COMMANDS


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

def extract_keywords(text: str, max_n: int = 8) -> list[str]:
    """Return K8s-relevant keywords found in text, case-preserved."""
    low = text.lower()
    found_lower: set[str] = set()
    result: list[str] = []

    for kw in _ALL_K8S:
        if kw.lower() in low and kw.lower() not in found_lower:
            found_lower.add(kw.lower())
            result.append(kw)

    return result[:max_n]


def extract_fix_commands(remediation: list[str]) -> list[str]:
    """
    Extract minimal command substrings from remediation lines.
    E.g. "helm upgrade api ./chart --set mem=512Mi" → "helm upgrade"
    """
    seen: dict[str, bool] = {}
    for cmd in remediation:
        stripped = cmd.strip().lstrip("-• ").split()
        if len(stripped) >= 2 and stripped[0] in _K8S_COMMANDS:
            key = f"{stripped[0]} {stripped[1]}"
            seen[key] = True
            # also capture --set key if present
            for part in stripped:
                if part.startswith("--set") or part.startswith("resources.") or "." in part:
                    seen[part.lstrip("-")] = True
    return list(seen.keys())[:4]


def _confidence_label(score: float) -> str:
    if score >= 0.70:
        return "HIGH"
    if score >= 0.40:
        return "MEDIUM"
    return "LOW"


def _score_min(root_score: float) -> float:
    return max(round(root_score - 0.12, 2), 0.25)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def update_expect_from_sim(
    case_dir: Path,
    sim_json: dict,
    dry_run: bool = False,
) -> tuple[dict, list[str]]:
    """
    Rebuild expect.json fields that can be derived from the sim result.

    Returns (new_expect_dict, list_of_changes).
    If dry_run=True, nothing is written to disk.
    """
    expect_path = case_dir / "expect.json"
    expect      = json.loads(expect_path.read_text())

    root_node   = sim_json["tree"]
    root_score  = root_node["score"]
    root_text   = root_node.get("raw_analysis", "") + " " + root_node.get("root_cause", "")
    remediation = root_node.get("remediation", [])

    new_keywords = extract_keywords(root_text)
    new_commands = extract_fix_commands(remediation)
    new_score_min = _score_min(root_score)
    new_confidence = _confidence_label(root_score)

    changes: list[str] = []
    new_expect = dict(expect)

    if new_keywords:
        merged = list(dict.fromkeys(expect.get("root_cause_contains", []) + new_keywords))
        if merged != expect.get("root_cause_contains"):
            changes.append(f"root_cause_contains: {expect.get('root_cause_contains')} → {merged}")
            new_expect["root_cause_contains"] = merged

    if new_commands:
        merged_cmds = list(dict.fromkeys(expect.get("fix_commands_contain", []) + new_commands))
        if merged_cmds != expect.get("fix_commands_contain"):
            changes.append(f"fix_commands_contain: {expect.get('fix_commands_contain')} → {merged_cmds}")
            new_expect["fix_commands_contain"] = merged_cmds

    old_min = expect.get("confidence_score_min", 0)
    if abs(new_score_min - old_min) >= 0.02:
        changes.append(f"confidence_score_min: {old_min} → {new_score_min}")
        new_expect["confidence_score_min"] = new_score_min

    if new_confidence != expect.get("confidence"):
        changes.append(f"confidence: {expect.get('confidence')} → {new_confidence}")
        new_expect["confidence"] = new_confidence

    if not dry_run and changes:
        expect_path.write_text(json.dumps(new_expect, indent=2, ensure_ascii=False) + "\n")

    return new_expect, changes


def update_input_from_sim(
    case_dir: Path,
    sim_json: dict,
    dry_run: bool = False,
) -> tuple[dict, list[str]]:
    """
    Enrich input.json with anchors / symptom inferred from the sim LLM output.

    Anchors are parsed from lines matching:
      "Kind/ns/name: field declared='X' [src] | observed='Y' [drift]"
    """
    input_path = case_dir / "input.json"
    inp        = json.loads(input_path.read_text())

    root_node = sim_json["tree"]
    raw_text  = root_node.get("raw_analysis", "")

    changes: list[str] = []
    new_input = dict(inp)

    # Extract anchor-like lines from LLM output
    anchor_pattern = re.compile(
        r"((?:Pod|Deployment|Service|Ingress|PVC|PersistentVolumeClaim|"
        r"ConfigMap|Secret|StatefulSet|DaemonSet|ReplicaSet|Node)"
        r"/[^\s:]+/[^\s:]+:[^\n]+declared='[^']+'\s*\[[^\]]+\])",
        re.IGNORECASE,
    )
    new_anchors = anchor_pattern.findall(raw_text)
    existing    = set(inp.get("anchors") or [])
    added       = [a for a in new_anchors if a not in existing]
    if added:
        merged = list(existing) + added
        changes.append(f"anchors: +{len(added)} new entries")
        new_input["anchors"] = merged

    # Enrich symptom if currently empty
    if not inp.get("symptom") and root_node.get("root_cause"):
        new_input["symptom"] = root_node["root_cause"][:300]
        changes.append("symptom: set from LLM root_cause")

    if not dry_run and changes:
        input_path.write_text(json.dumps(new_input, indent=2, ensure_ascii=False) + "\n")

    return new_input, changes


def recalibrate_all(
    sim_results_dir: Path,
    cases_root: Path,
    dry_run: bool = False,
) -> dict[str, list[str]]:
    """
    For every *.json in sim_results_dir, update the matching case's expect.json
    with the real observed score.

    Returns {case_name: [changes]} for all cases processed.
    """
    results: dict[str, list[str]] = {}

    for sim_path in sorted(sim_results_dir.glob("*.json")):
        case_name = sim_path.stem
        case_dir  = cases_root / case_name
        if not case_dir.is_dir():
            continue

        sim_json = json.loads(sim_path.read_text())
        _, changes = update_expect_from_sim(case_dir, sim_json, dry_run=dry_run)
        results[case_name] = changes

    return results
