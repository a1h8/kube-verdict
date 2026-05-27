#!/usr/bin/env python
"""
Generate ontology/generated_entities.py from ontology/gen_config.yaml.

Reads kubernetes.client.models.V1<model> openapi_types to validate field paths,
then emits a @dataclass per kind with:
  - typed attributes derived from the config
  - auto-wired __post_init__ (kind, namespace)
  - a to_text() that flattens non-empty fields into searchable tokens

Usage:
    python tools/gen_entities.py
    python tools/gen_entities.py --dry-run    # print to stdout only
"""
from __future__ import annotations

import argparse
import importlib
import sys

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent
CONFIG_PATH = REPO_ROOT / "ontology" / "gen_config.yaml"
OUTPUT_PATH = REPO_ROOT / "ontology" / "generated_entities.py"

_HEADER = '''\
# ──────────────────────────────────────────────────────────────────────────────
# AUTO-GENERATED — do not edit by hand.
# Re-generate with:  python tools/gen_entities.py
# Source of truth:   ontology/gen_config.yaml
#                    kubernetes.client.models (openapi_types)
# ──────────────────────────────────────────────────────────────────────────────
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

from ontology.entities import K8sEntity, ResourceKind


'''

# Python type string for each generator type alias
_TYPE_MAP = {
    "str":          ("str", '""'),
    "int":          ("int", "0"),
    "bool":         ("bool", "False"),
    "str_list":     ("list[str]", "field(default_factory=list)"),
    "raw_list":     ("list[dict]", "field(default_factory=list)"),
    "quantity_map": ("dict[str, str]", "field(default_factory=dict)"),
}


def _k8s_model(model_name: str):
    """Return the kubernetes.client.models class for model_name."""
    models = importlib.import_module("kubernetes.client.models")
    return getattr(models, model_name, None)


def _resolve_field_type(model_cls, path: str) -> str | None:
    """
    Walk a dot-path against openapi_types to validate it exists.
    Supports top-level fields (e.g. StorageClass.provisioner) as well as
    nested paths (e.g. spec.limits, status.active).
    Returns the openapi type string or None if not found.
    """
    import re
    parts = path.split(".")
    models = importlib.import_module("kubernetes.client.models")

    current_cls = model_cls
    for part in parts:
        types = getattr(current_cls, "openapi_types", {})
        t = types.get(part)
        if t is None:
            return None
        m = re.search(r"\b(V\w+)\b", t)
        nested_name = m.group(1) if m else None
        nested_cls = getattr(models, nested_name, None) if nested_name else None
        current_cls = nested_cls or current_cls
    return t


def _generate_kind(kind_cfg: dict) -> str:
    name = kind_cfg["name"]
    model_name = kind_cfg["model"]
    doc = kind_cfg.get("doc", "")
    fields_cfg = kind_cfg.get("fields", [])

    model_cls = _k8s_model(model_name)
    if model_cls is None:
        print(f"  WARNING: {model_name} not found in kubernetes.client.models — skipping",
              file=sys.stderr)
        return ""

    lines: list[str] = []

    # ── Docstring ────────────────────────────────────────────────────────────
    lines.append('@dataclass')
    lines.append(f'class {name}(K8sEntity):')
    if doc:
        lines.append(f'    """{doc}"""')

    # ── Attributes ───────────────────────────────────────────────────────────
    attr_names: list[str] = []
    for f in fields_cfg:
        attr = f["attr"]
        ftype_key = f.get("type", "str")
        py_type, default = _TYPE_MAP.get(ftype_key, ("Any", "None"))
        fdoc = f.get("doc", "")

        # Validate path against the model
        api_type = _resolve_field_type(model_cls, f["path"])
        if api_type is None:
            print(f"  WARNING: {name}.{f['path']} not found in {model_name} — "
                  f"keeping as declared", file=sys.stderr)

        comment = f"  # {fdoc}" if fdoc else ""
        if "field(" in default:
            lines.append(f"    {attr}: {py_type} = {default}{comment}")
        else:
            lines.append(f"    {attr}: {py_type} = {default}{comment}")
        attr_names.append(attr)

    # ── __post_init__ ────────────────────────────────────────────────────────
    lines.append("")
    lines.append("    def __post_init__(self):")
    lines.append(f"        self.kind = ResourceKind.{name.upper()}")

    # ── to_text() ────────────────────────────────────────────────────────────
    lines.append("")
    lines.append("    def to_text(self) -> str:")
    lines.append(f"        parts = [f'kind={name} name={{self.name}}']")
    lines.append("        if self.namespace:")
    lines.append("            parts.append(f'namespace={self.namespace}')")
    for attr in attr_names:
        lines.append(f"        if self.{attr}:")
        lines.append(f"            parts.append(f'{attr}={{self.{attr}}}')")
    lines.append("        return ' '.join(parts)")
    lines.append("")

    return "\n".join(lines)


def _enum_entries(kinds: list[dict]) -> str:
    """Produce ResourceKind enum entries to append."""
    entries = []
    for k in kinds:
        name = k["name"]
        entries.append(f'    {name.upper()} = "{name}"')
    return "\n".join(entries)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cfg = yaml.safe_load(CONFIG_PATH.read_text())
    kinds = cfg.get("kinds", [])

    blocks: list[str] = [_HEADER]

    # Enum comment block (informational — actual enum lives in entities.py)
    blocks.append("# ── ResourceKind entries required in ontology/entities.py ──────────────────")
    blocks.append("# Add these to the ResourceKind enum if not already present:")
    for k in kinds:
        blocks.append(f"#   {k['name'].upper()} = \"{k['name']}\"")
    blocks.append("")
    blocks.append("")

    for kind_cfg in kinds:
        print(f"Generating {kind_cfg['name']}…")
        block = _generate_kind(kind_cfg)
        if block:
            blocks.append(block)

    output = "\n".join(blocks)

    if args.dry_run:
        print(output)
    else:
        OUTPUT_PATH.write_text(output)
        print(f"\nWrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
