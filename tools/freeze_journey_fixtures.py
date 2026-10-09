#!/usr/bin/env python3
"""Freeze h013 / h014 / h015 Decision Journeys for the vitrine, from the real code.

Runs each case through the LangGraph workflow offline (the same scenario as
tests/integration/test_workflow_e2e_h013_h015.py, via
tests/integration/workflow_harness.py), serialises the resulting session with
the API's own ``_state_to_response`` and writes
``dashboard/src/sampleJourneys/<case>.json``.

Every file is tagged ``"source": "fixture-replay"``: the inputs are fixtures and
the LLM is scripted, so the vitrine must never present it as a live capture.
Never hand-edit the output — re-run this tool. Spec: docs/test-cases.md →
*Broadened offline coverage* §1.

Usage::

    python tools/freeze_journey_fixtures.py            # all three cases
    python tools/freeze_journey_fixtures.py h014       # one case
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from api.models import SessionStatus  # noqa: E402
from api.routes.sessions import _state_to_response  # noqa: E402
from api.session_store import Session  # noqa: E402
from tests.integration.workflow_harness import CASE_SCRIPTS, run_case  # noqa: E402

OUT_DIR = ROOT / "dashboard" / "src" / "sampleJourneys"
SOURCE = "fixture-replay"


def journey(key: str) -> dict:
    """The vitrine payload for one case: the API response plus provenance."""
    run = run_case(key)
    session = Session(
        session_id=f"{SOURCE}-{key}",
        status=SessionStatus(run.status),
        review_payload=run.review_payload,
        last_state=run.state,
    )
    payload = _state_to_response(session).model_dump(mode="json")
    return {"source": SOURCE, "case": CASE_SCRIPTS[key].case, **payload}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("cases", nargs="*", default=sorted(CASE_SCRIPTS),
                        help="case keys (default: all)")
    args = parser.parse_args(argv)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for key in args.cases:
        path = OUT_DIR / f"{key}.json"
        path.write_text(json.dumps(journey(key), indent=2, ensure_ascii=False) + "\n")
        print(f"wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
