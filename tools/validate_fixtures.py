"""Check the objects of the cases' kube/ fixtures against the definitions (docs/schema-registry.md § 4).

    python -m tools.validate_fixtures tests/integration/cases tests/s2/cases

The registry is built from the committed bundle. A case that fails is reported, never fixed here: a fix is a dated
amendment of that case.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import yaml

from ontology.schema_registry import SchemaRegistry, validate


def objects(case: Path):
    for path in sorted((case / "kube").rglob("*.yaml")):
        for doc in yaml.safe_load_all(path.read_text()):
            if not isinstance(doc, dict):
                continue
            items = doc.get("items", []) if str(doc.get("kind", "")).endswith("List") else [doc]
            for item in items:
                yield path.relative_to(case), item


def check_case(reg: SchemaRegistry, case: Path) -> tuple[int, list[str]]:
    n, problems = 0, []
    for rel, obj in objects(case):
        n += 1
        name = (obj.get("metadata") or {}).get("name", "?")
        problems += [f"{rel} {name}: {p}" for p in validate(reg, obj)]
    return n, problems


def reason(problem: str) -> str:
    tail = problem.split(": ", 2)[-1]
    for key in ("not in the definition", "required, missing", "no definition", "is not one of", "where the definition"):
        if key in tail:
            return key
    return "other"


def main(argv: list[str]) -> int:
    reg = SchemaRegistry.from_bundle()
    total_cases = failing = total_objs = 0
    reasons: Counter = Counter()
    for root in map(Path, argv or ["tests/integration/cases", "tests/s2/cases"]):
        for case in sorted(p for p in root.iterdir() if (p / "kube").is_dir()):
            total_cases += 1
            n, problems = check_case(reg, case)
            total_objs += n
            if problems:
                failing += 1
                print(f"{case}: {len(problems)} problem(s) over {n} object(s)")
                for p in problems:
                    print(f"  {p}")
                    reasons[reason(p)] += 1
    print(f"\n{failing}/{total_cases} cases fail, {total_objs} objects; by reason: {dict(reasons)}")
    return 1 if failing else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
