"""Veracity grading — did an answer name the real root cause?

One rule for every solver (live capture, fixture replay, rules-only baseline),
read from a case's ``expect.json``. See docs/scenarios-v2.md § Grading v2.

Fields read from ``expect``:

- ``root_cause_contains`` — ground-truth keywords; at least half (min 1) must
  appear, case-insensitive substring (same rule as ``test_root_cause_keywords``).
- ``root_cause_contains_all`` — multi-cause cases: a list of keyword lists, each
  graded with the rule above, all of them must pass. Replaces
  ``root_cause_contains`` when present.
- ``root_cause_must_not_contain`` — the decoy's keywords: any one of them in the
  root cause is a FAIL, even when the keywords above match.
- ``expected_outcome`` — ``cause`` (default), ``no_incident`` or
  ``insufficient_evidence``. For the last two the answer PASSes when its
  confidence label is LOW and it names no forbidden keyword.
"""
from __future__ import annotations

OUTCOMES = ("cause", "no_incident", "insufficient_evidence")


def _matches(text: str, keywords: list[str]) -> list[str]:
    return [kw for kw in keywords if kw.lower() in text]


def _group_passes(matched: list[str], keywords: list[str]) -> bool:
    return len(matched) >= max(1, len(keywords) // 2)


def grade(root_cause: str, expect: dict, confidence: str = "") -> dict:
    """Grade one answer against one case's expectations.

    Returns ``veracity`` (PASS/FAIL), ``reason``, ``matched_keywords`` (flat),
    ``matched_groups`` (one list per keyword group) and ``forbidden_matched``.
    """
    outcome = expect.get("expected_outcome", "cause")
    if outcome not in OUTCOMES:
        raise ValueError(f"unknown expected_outcome {outcome!r}, expected one of {OUTCOMES}")

    text = (root_cause or "").lower()
    forbidden = _matches(text, expect.get("root_cause_must_not_contain", []))

    if outcome == "cause":
        groups = expect.get("root_cause_contains_all") or [expect.get("root_cause_contains", [])]
        if not any(groups):
            raise ValueError("expected_outcome 'cause' needs root_cause_contains or root_cause_contains_all")
        matched_groups = [_matches(text, g) for g in groups]
        missing = [i for i, (m, g) in enumerate(zip(matched_groups, groups)) if not _group_passes(m, g)]
        if missing:
            reason = f"keyword group(s) {missing} below threshold"
        elif forbidden:
            reason = f"names the decoy: {forbidden}"
        else:
            reason = "cause named, no decoy"
        passed = not missing and not forbidden
    else:
        matched_groups = []
        label = (confidence or "").upper()
        if label != "LOW":
            reason = f"{outcome}: confidence {label or 'missing'}, LOW expected"
        elif forbidden:
            reason = f"{outcome}: names a cause anyway: {forbidden}"
        else:
            reason = f"{outcome}: LOW and no cause named"
        passed = label == "LOW" and not forbidden

    return {
        "veracity": "PASS" if passed else "FAIL",
        "reason": reason,
        "matched_keywords": [kw for m in matched_groups for kw in m],
        "matched_groups": matched_groups,
        "forbidden_matched": forbidden,
    }
