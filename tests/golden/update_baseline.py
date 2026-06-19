"""Regenerate the golden baseline intentionally (after a deliberate decision
change). Run: ``python -m tests.golden.update_baseline``."""
from __future__ import annotations

import json
from pathlib import Path

from tests.golden.scenarios import replay_all

BASELINE = Path(__file__).parent / "baseline.json"


def main() -> None:
    BASELINE.write_text(json.dumps(replay_all(), indent=2, sort_keys=True) + "\n")
    print(f"wrote {BASELINE}")


if __name__ == "__main__":
    main()
