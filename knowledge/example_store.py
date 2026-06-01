"""
Resolved-incident example store.

Successful RCA runs (human-approved remediations) are persisted as JSON and
indexed into the FAISS vector store so ContextBuilder can surface similar past
incidents alongside live cluster state.

When example_lookup_node finds a strong cosine-similarity match the full
multi-path analysis loop is short-circuited — the operator sees the known fix
immediately with HIGH confidence.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid a runtime knowledge→decision import; from_report is duck-typed
    from decision.models import IncidentReport

_DEFAULT_DIR = Path("./data/examples")

EXAMPLE_UID_PREFIX = "example:"


def _entity_kinds(affected: list[str]) -> list[str]:
    """Extract unique resource kinds from 'Kind/ns/name' references, order-preserving."""
    kinds: list[str] = []
    for ref in affected:
        kind = str(ref).split("/", 1)[0].strip()
        if kind and kind not in kinds:
            kinds.append(kind)
    return kinds


@dataclass
class ResolvedIncident:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    query: str = ""
    hypothesis: str = ""
    root_cause: str = ""
    anchor_violations: list[str] = field(default_factory=list)
    entity_kinds: list[str] = field(default_factory=list)
    remediation: list[str] = field(default_factory=list)
    confidence: str = ""
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @classmethod
    def from_report(
        cls,
        report: "IncidentReport",
        *,
        hypothesis: str = "",
        anchor_violations: list[str] | None = None,
    ) -> "ResolvedIncident":
        """Build a ResolvedIncident from the canonical IncidentReport.

        ``hypothesis`` and ``anchor_violations`` are workflow/graph context the
        report doesn't carry, so they're passed explicitly. ``entity_kinds`` is
        derived from the report's affected-resource references.
        """
        return cls(
            query=report.query,
            hypothesis=hypothesis,
            root_cause=report.root_cause,
            anchor_violations=list(anchor_violations or []),
            entity_kinds=_entity_kinds(report.affected),
            remediation=list(report.remediation),
            confidence=report.confidence,
        )


class ExampleStore:
    """File-backed store for resolved incidents."""

    def __init__(self, data_dir: Path | str = _DEFAULT_DIR) -> None:
        self._dir = Path(data_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def save(self, incident: ResolvedIncident) -> ResolvedIncident:
        path = self._dir / f"{incident.id}.json"
        path.write_text(json.dumps(asdict(incident), indent=2, ensure_ascii=False))
        return incident

    def get(self, incident_id: str) -> ResolvedIncident | None:
        path = self._dir / f"{incident_id}.json"
        if not path.exists():
            return None
        try:
            return ResolvedIncident(**json.loads(path.read_text()))
        except Exception:
            return None

    def list(self) -> list[ResolvedIncident]:
        incidents = []
        for f in sorted(
            self._dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        ):
            try:
                incidents.append(ResolvedIncident(**json.loads(f.read_text())))
            except Exception:
                pass
        return incidents


class _ExampleChunk:
    """Duck-typed entity accepted by FAISSStore.add_entity()."""

    def __init__(self, incident: ResolvedIncident) -> None:
        self.uid       = f"{EXAMPLE_UID_PREFIX}{incident.id}"
        self.name      = f"incident-{incident.id}"
        self.namespace = "example-store"
        self.kind      = "ResolvedIncident"
        self._incident = incident

    def to_text(self) -> str:
        inc = self._incident
        return (
            f"RESOLVED INCIDENT: {inc.query}\n"
            f"Root cause: {inc.root_cause}\n"
            f"Hypothesis: {inc.hypothesis}\n"
            f"Entities: {', '.join(inc.entity_kinds)}\n"
            f"Anchor violations: {', '.join(inc.anchor_violations)}\n"
            f"Fix: {'; '.join(inc.remediation)}\n"
            f"Confidence: {inc.confidence}"
        )


class ExampleIndexer:
    """Index resolved incidents into an existing FAISSStore."""

    def __init__(self, store) -> None:
        self._store = store

    def index_example(self, incident: ResolvedIncident) -> None:
        self._store.add_entity(_ExampleChunk(incident))

    def index_all(self, example_store: ExampleStore) -> int:
        """Re-index all stored incidents. Returns count."""
        count = 0
        for incident in example_store.list():
            self.index_example(incident)
            count += 1
        return count
