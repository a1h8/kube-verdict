"""S2's cases, frozen by hash before any run (docs/s2-correlation-cases.md, step 4).

    python tests/s2/frozen.py            # prints the hashes
    python tests/s2/frozen.py --write    # writes tests/s2/frozen.json (only at the freeze)

A case changed after the freeze is a new case, measured on a new block: tests/unit/test_s2_frozen.py
fails when a frozen case's files differ from its hash.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

CASES = Path(__file__).resolve().parent / "cases"
FROZEN = Path(__file__).resolve().parent / "frozen.json"


def case_hash(case_dir: Path) -> str:
    """SHA-256 over every file of the case, in path order: its relative path, then its bytes."""
    h = hashlib.sha256()
    for path in sorted(p for p in case_dir.rglob("*") if p.is_file()):
        h.update(path.relative_to(case_dir).as_posix().encode() + b"\0")
        h.update(path.read_bytes() + b"\0")
    return h.hexdigest()


def hashes() -> dict[str, str]:
    return {d.name: case_hash(d) for d in sorted(CASES.iterdir()) if d.is_dir()}


if __name__ == "__main__":
    current = hashes()
    if "--write" in sys.argv:
        FROZEN.write_text(json.dumps({"frozen_on": "2026-11-08", "cases": current}, indent=2) + "\n")
    print(json.dumps(current, indent=2))
