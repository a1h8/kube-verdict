"""The LLM's confidence label, normalised once for the product and the measurement."""
from __future__ import annotations

import re


def normalize_label(confidence: str) -> str:
    """``"MEDIUM."``, ``"low — sparse context"``, ``"LOW (rule-assisted…)"`` → the bare label.

    A live model decorates the label; comparing it raw would fail a correct LOW and hide a
    wrong HIGH from the overconfidence count. The confidence router and the policy score read
    the same value as the veracity grader (docs/fix-plan-001.md, H20: the product took the
    first word, so ``MEDIUM:`` matched nothing).
    """
    m = re.match(r"\s*([A-Za-z]+)", confidence or "")
    return m.group(1).upper() if m else ""
