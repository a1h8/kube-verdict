"""
Enterprise documentation store.

Documents are persisted as individual JSON files under ./data/docs/.
Each document is chunked and indexed into the FAISS vector store at run time
so the LLM context builder can retrieve relevant enterprise knowledge alongside
cluster state.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

_DEFAULT_DIR = Path("./data/docs")


@dataclass
class EnterpriseDoc:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    title: str = ""
    content: str = ""
    tags: list[str] = field(default_factory=list)
    source: str = "manual"    # manual | upload | url
    url: str = ""             # original URL when source="url"
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class DocStore:
    """Simple file-backed store for enterprise documents."""

    def __init__(self, data_dir: Path = _DEFAULT_DIR) -> None:
        self._dir = Path(data_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    # ── CRUD ─────────────────────────────────────────────────────────────────

    def save(self, doc: EnterpriseDoc) -> EnterpriseDoc:
        path = self._dir / f"{doc.id}.json"
        path.write_text(json.dumps(asdict(doc), indent=2, ensure_ascii=False))
        return doc

    def get(self, doc_id: str) -> EnterpriseDoc | None:
        path = self._dir / f"{doc_id}.json"
        if not path.exists():
            return None
        try:
            return EnterpriseDoc(**json.loads(path.read_text()))
        except Exception:
            return None

    def list(self) -> list[EnterpriseDoc]:
        docs = []
        for f in sorted(self._dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                docs.append(EnterpriseDoc(**json.loads(f.read_text())))
            except Exception:
                pass
        return docs

    def delete(self, doc_id: str) -> bool:
        path = self._dir / f"{doc_id}.json"
        if path.exists():
            path.unlink()
            return True
        return False

    # ── Helpers ───────────────────────────────────────────────────────────────

    def count(self) -> int:
        return sum(1 for _ in self._dir.glob("*.json"))
