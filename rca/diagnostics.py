"""Diagnostics — read-only checks, kept apart from remediation.

Spec: docs/diagnostics.md. A remediation line changes the cluster and goes through
the policy gate; a diagnostic only reads it and is never gated. Rules declare their
checks as ``Check`` (what to run, what it verifies, which output confirms or refutes
the hypothesis); the report carries them as ``Diagnostic``.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

_READ_ONLY_VERBS = {
    "kubectl": {"get", "describe", "logs", "top", "explain", "events",
                "api-resources", "version", "diff"},
    "helm": {"status", "get", "history", "list", "ls", "show", "template", "diff", "lint"},
}
# kubectl <verb> <sub-verb> pairs that only read
_READ_ONLY_SUBVERBS = {("auth", "can-i"), ("rollout", "status"), ("rollout", "history")}
_TEXT_FILTERS = {"grep", "head", "tail", "jq", "yq", "awk", "sort", "wc"}
_CHAINING = re.compile(r";|&&|\|\||>")


def is_read_only(cmd: str) -> bool:
    """True when the line only reads the cluster: a diagnostic, not a remediation.

    Unknown commands and prose are not read-only, so the policy gate keeps
    treating them as actions (docs/test-cases.md § h022).
    """
    line = cmd.strip().lstrip("$ ").strip()
    if not line or _CHAINING.search(line):
        return False
    first, *filters = [seg.split() for seg in line.split("|")]
    if any(not seg or seg[0] not in _TEXT_FILTERS for seg in filters):
        return False
    if len(first) < 2 or first[0] not in _READ_ONLY_VERBS:
        return False
    if first[0] == "kubectl" and tuple(first[1:3]) in _READ_ONLY_SUBVERBS:
        return True
    return first[1] in _READ_ONLY_VERBS[first[0]]


def split_diagnostics(lines: list[str]) -> tuple[list[str], list[str]]:
    """Split remediation lines into (actions, read-only diagnostics), order kept.

    A ``#`` comment goes with the command that follows it; trailing comments
    with no command after them stay with the actions.
    """
    actions: list[str] = []
    checks: list[str] = []
    pending: list[str] = []
    for line in lines:
        if line.strip().startswith("#"):
            pending.append(line)
            continue
        (checks if is_read_only(line) else actions).extend([*pending, line])
        pending = []
    actions.extend(pending)
    return actions, checks


@dataclass(frozen=True)
class Check:
    """A read-only check a rule declares for its hypothesis."""
    command: str
    purpose: str            # what it verifies, one sentence
    confirms_if: str        # the output that supports the hypothesis
    refutes_if: str = ""    # the output that rules it out, when there is one


@dataclass(frozen=True)
class Diagnostic:
    """A read-only check carried by the report, never seen by the policy gate."""
    command: str
    source: str             # "rule" | "llm"
    hypothesis: str = ""    # rule_id for a rule check, "" for an unstructured LLM line
    affected: str = ""      # kind/namespace/name the check is about
    purpose: str = ""
    confirms_if: str = ""
    refutes_if: str = ""

    @classmethod
    def from_check(cls, check: Check, *, hypothesis: str, affected: str) -> "Diagnostic":
        return cls(command=check.command, source="rule", hypothesis=hypothesis,
                   affected=affected, purpose=check.purpose,
                   confirms_if=check.confirms_if, refutes_if=check.refutes_if)

    @classmethod
    def from_llm_line(cls, line: str) -> "Diagnostic":
        return cls(command=line, source="llm")

    def to_dict(self) -> dict:
        return asdict(self)
