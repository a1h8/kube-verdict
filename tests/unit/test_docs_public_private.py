"""docs/target/ holds private notes: never tracked, never linked from a tracked file.

Public docs are English and self-contained; the specs code and tests cite live under
docs/ (see docs/knowledge-sources.md, docs/veracity-replay.md).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = "docs/target"

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or not (ROOT / ".git").exists(),
    reason="needs a git checkout",
)


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def test_private_folder_is_not_tracked():
    assert _git("ls-files", PRIVATE).stdout.strip() == ""


def test_private_folder_is_ignored():
    assert _git("check-ignore", "-q", f"{PRIVATE}/README.md").returncode == 0


def test_no_tracked_file_links_to_private_folder():
    # .gitignore names the folder on purpose; everything else must not.
    hits = _git("grep", "-n", "-E", r"docs/target|\]\(target/|\.\./target/",
                "--", ".", ":!.gitignore", ":!tests/unit/test_docs_public_private.py")
    assert hits.stdout == "", f"tracked files point to {PRIVATE}/:\n{hits.stdout}"
