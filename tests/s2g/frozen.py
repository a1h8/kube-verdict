"""S2-GitOps' cases, frozen by hash before any run (docs/s2g/gitops-cases.md), as S2's (tests/s2/frozen.py).

    python tests/s2g/frozen.py            # prints the hashes
    python tests/s2g/frozen.py --write    # writes tests/s2g/frozen.json (only at the freeze)
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.s2.frozen import case_hash  # noqa: E402

CASES = Path(__file__).resolve().parent / "cases"
FROZEN = Path(__file__).resolve().parent / "frozen.json"


def hashes() -> dict[str, str]:
    return {d.name: case_hash(d) for d in sorted(CASES.iterdir()) if d.is_dir()}


if __name__ == "__main__":
    hs = hashes()
    if "--write" in sys.argv:
        FROZEN.write_text(json.dumps({"frozen_on": date.today().isoformat(), "cases": hs}, indent=2) + "\n")
    print(json.dumps(hs, indent=2))
