"""The raw input of a case: every fixture file the collectors read, and nothing else.

Spec: docs/veracity-replay.md § Comparison, Amendment 2026-10-22 ("Raw input, fixed now").
What the ``raw:<model>`` solver hands the model: the files under the collectors' directories,
each under a header naming its path relative to the case, comments removed (some name the
cause, and no collector reads them). Never ``expect.json``, a ``README.md``, ``rendered/``
(Git's render of ``chart/`` + ``helm/``) or the case's directory name.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

RAW_DIRS = ("kube", "helm", "chart", "policy", "prometheus", "loki", "otel")

_TEMPLATE_COMMENT = re.compile(r"\{\{-?\s*/\*.*?\*/\s*-?\}\}", re.S)


def raw_files(case_dir: Path) -> list[Path]:
    """The fixture files of the case the raw model may read, in sorted order."""
    files = [p for d in RAW_DIRS if (case_dir / d).is_dir()
             for p in (case_dir / d).rglob("*") if p.is_file()]
    return sorted(files, key=lambda p: p.relative_to(case_dir).as_posix())


def _strip_line_comment(line: str) -> str:
    """``line`` without a ``#`` comment: at the start, or after a space outside quotes."""
    quote = ""
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "'\"":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1].isspace()):
            return line[:i].rstrip()
    return line


def strip_comments(text: str) -> str:
    """Comments removed from a YAML text or a Helm template, data unchanged.

    Plain YAML is parsed and written back. A template (``{{ }}``) is not YAML, so its
    ``{{/* */}}`` blocks and ``#`` comments are removed line by line; lines left empty
    by a removed comment go too.
    """
    try:
        docs = list(yaml.safe_load_all(text))
    except yaml.YAMLError:
        docs = None
    if docs is not None and "{{" not in text:
        docs = [d for d in docs if d is not None]
        return yaml.safe_dump_all(docs, sort_keys=False, default_flow_style=False).rstrip() + "\n"
    out = []
    for line in _TEMPLATE_COMMENT.sub("", text).splitlines():
        kept = _strip_line_comment(line)
        if kept.strip() or not line.strip():
            out.append(kept)
    return "\n".join(out).rstrip() + "\n"


def raw_block(case_dir: Path) -> tuple[str, list[str]]:
    """(the files as one text block, their paths relative to the case)."""
    parts, names = [], []
    for path in raw_files(case_dir):
        rel = path.relative_to(case_dir).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        if path.suffix in (".yaml", ".yml"):
            text = strip_comments(text)
        parts.append(f"=== {rel} ===\n{text.rstrip()}\n")
        names.append(rel)
    return "\n".join(parts), names
