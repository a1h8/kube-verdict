"""
The recorded sample investigation behind ``POST /sessions/sample``, so the Decision Journey UI can
be demoed without a live cluster or Ollama.

It is generated, not written by hand: ``tools/freeze_journey_fixtures.py`` runs h014 offline
through the real workflow (fixtures, scripted LLM) and writes ``api/sample_state.json``; a test
fails when it no longer matches the code. The hand-written sample it replaces showed hypothesis
retries on the LLM's label, a pipeline the product no longer runs (docs/fix-plan-001.md, H72).
"""
from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

SAMPLE_PATH = Path(__file__).resolve().parent / "sample_state.json"


@lru_cache(maxsize=1)
def _recorded() -> dict[str, Any]:
    return json.loads(SAMPLE_PATH.read_text())


def sample_state() -> dict[str, Any]:
    """The workflow state of the recorded run, labelled as a fixture replay."""
    recorded = _recorded()
    return {**copy.deepcopy(recorded["state"]), "source": recorded["source"]}


def sample_review_payload() -> dict[str, Any]:
    return copy.deepcopy(_recorded()["review_payload"])
