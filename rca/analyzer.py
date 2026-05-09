from __future__ import annotations
import logging
import re
import textwrap
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterator

from llm.ollama_client import OllamaClient
from ontology.graph import OntologyGraph
from rca.context_builder import ContextBuilder, ContextWindow
from vectorstore.store import FAISSStore

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = textwrap.dedent("""\
    You are KubeWhisperer, an expert Site Reliability Engineer specialising in
    Kubernetes cluster diagnostics.

    Rules:
    - Only use facts present in the context below. Never invent resource names,
      namespaces, image tags, or error messages.
    - Sections marked CRITICAL must be addressed first. They represent confirmed
      failures or proven divergence between what Helm declared and what K8s shows.
    - Sections marked WARNING contain Kubernetes events — treat them as evidence,
      not as root causes on their own.
    - If context is insufficient, state clearly what additional information is needed.
    - All data is from a local, air-gapped cluster. No data leaves the machine.
""")

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

_PROMPT_TEMPLATE = textwrap.dedent("""\
    ## Cluster information
    Kubernetes version : {kube_version}
    Analysis timestamp : {timestamp}
    Total context chunks: {total_chunks}

    {context_block}

    ---
    ## Incident query
    {query}

    ---
    ## Task — Root Cause Analysis

    Respond using EXACTLY this structure (keep the ### headers):

    ### 1. Summary
    One sentence.

    ### 2. Affected resources
    Bullet list: `kind/namespace/name — symptom observed`

    ### 3. Root cause
    Most probable root cause. Cite specific resource names and drift/event evidence.

    ### 4. Causal chain
    Numbered steps from trigger to visible impact.

    ### 5. Remediation
    Concrete commands. Prefer `kubectl` and `helm`/`helmfile` commands over prose.

    ### 6. Confidence
    LOW | MEDIUM | HIGH — one sentence justification.
""")

# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

@dataclass
class RCAReport:
    query: str
    kube_version: str
    context: ContextWindow
    raw_analysis: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Parsed fields (populated by _parse_analysis)
    summary: str = ""
    affected: list[str] = field(default_factory=list)
    root_cause: str = ""
    causal_chain: list[str] = field(default_factory=list)
    remediation: list[str] = field(default_factory=list)
    confidence: str = ""

    def __post_init__(self):
        _parse_analysis(self)

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    def __str__(self) -> str:
        bar = "─" * 72
        meta = (
            f"K8s {self.kube_version}  |  "
            f"{self.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}  |  "
            f"{len(self.context.seed_entities)} seeds  |  "
            f"{len(self.context.drift)} drift items  |  "
            f"{len(self.context.events)} warning events"
        )
        return f"{bar}\nKubeWhisperer RCA\n{meta}\nQuery: {self.query}\n{bar}\n{self.raw_analysis}\n{bar}"

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "kube_version": self.kube_version,
            "timestamp": self.timestamp.isoformat(),
            "summary": self.summary,
            "affected": self.affected,
            "root_cause": self.root_cause,
            "causal_chain": self.causal_chain,
            "remediation": self.remediation,
            "confidence": self.confidence,
            "context_stats": {
                "seeds": len(self.context.seeds),
                "drift": len(self.context.drift),
                "events": len(self.context.events),
                "helm": len(self.context.helm),
                "related": len(self.context.related),
                "total": self.context.total_chunks,
            },
            "raw_analysis": self.raw_analysis,
        }

# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------

class RCAAnalyzer:
    """
    Full KubeWhisperer pipeline:

    ContextBuilder (seeds + drift + events + helm + BFS/FAISS/dedup/tfidf)
         ↓
    Structured prompt (sections ordered by severity)
         ↓
    Mistral via Ollama (local, temperature=0.1)
         ↓
    RCAReport (parsed structured fields + raw text)
    """

    def __init__(
        self,
        graph: OntologyGraph,
        store: FAISSStore,
        llm: OllamaClient | None = None,
    ) -> None:
        self.graph = graph
        self.store = store
        self.llm = llm or OllamaClient()
        self._ctx_builder = ContextBuilder(graph, store)
        self._check_llm()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(self, query: str) -> RCAReport:
        kube_version = _kube_version(self.graph)
        log.info("RCA start  query=%r  k8s=%s", query, kube_version)

        ctx = self._ctx_builder.build(query)
        prompt = _build_prompt(query, ctx, kube_version)

        log.info(
            "Calling %s  chunks=%d (seeds=%d drift=%d events=%d helm=%d related=%d)",
            self.llm.model, ctx.total_chunks,
            len(ctx.seeds), len(ctx.drift),
            len(ctx.events), len(ctx.helm), len(ctx.related),
        )

        analysis = self.llm.generate(prompt, system=_SYSTEM_PROMPT)

        return RCAReport(
            query=query,
            kube_version=kube_version,
            context=ctx,
            raw_analysis=analysis,
        )

    def stream_analyze(self, query: str) -> Iterator[str | RCAReport]:
        """Yields str tokens, then a final RCAReport."""
        kube_version = _kube_version(self.graph)
        ctx = self._ctx_builder.build(query)
        prompt = _build_prompt(query, ctx, kube_version)

        tokens: list[str] = []
        for token in self.llm.stream_generate(prompt, system=_SYSTEM_PROMPT):
            tokens.append(token)
            yield token

        yield RCAReport(
            query=query,
            kube_version=kube_version,
            context=ctx,
            raw_analysis="".join(tokens),
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _check_llm(self) -> None:
        if not self.llm.is_available():
            log.warning("Ollama not reachable at %s — run: ollama serve", self.llm.url)
        elif not self.llm.model_is_pulled():
            log.warning(
                "Model '%s' not pulled — run: ollama pull %s",
                self.llm.model, self.llm.model,
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _kube_version(graph: OntologyGraph) -> str:
    return str(graph.server_version) if graph.server_version else "unknown"


def _build_prompt(query: str, ctx: ContextWindow, kube_version: str) -> str:
    return _PROMPT_TEMPLATE.format(
        kube_version=kube_version,
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        total_chunks=ctx.total_chunks,
        context_block=ctx.to_prompt_block(),
        query=query,
    )


def _parse_analysis(report: RCAReport) -> None:
    """
    Best-effort extraction of structured fields from Mistral's markdown output.
    Falls back gracefully — raw_analysis always contains the full text.
    """
    text = report.raw_analysis

    report.summary = _extract_section(text, "1. Summary", "2. Affected")
    report.root_cause = _extract_section(text, "3. Root cause", "4. Causal")
    report.confidence = _extract_section(text, "6. Confidence", None)

    affected_block = _extract_section(text, "2. Affected resources", "3. Root")
    report.affected = _extract_bullets(affected_block)

    chain_block = _extract_section(text, "4. Causal chain", "5. Remediation")
    report.causal_chain = _extract_bullets(chain_block)

    remed_block = _extract_section(text, "5. Remediation", "6. Confidence")
    report.remediation = _extract_bullets(remed_block)


def _extract_section(text: str, start_marker: str, end_marker: str | None) -> str:
    pattern = re.escape(start_marker)
    start = re.search(pattern, text, re.IGNORECASE)
    if not start:
        return ""
    begin = start.end()
    if end_marker:
        end = re.search(re.escape(end_marker), text[begin:], re.IGNORECASE)
        snippet = text[begin: begin + end.start()] if end else text[begin:]
    else:
        snippet = text[begin:]
    return snippet.strip()


def _extract_bullets(block: str) -> list[str]:
    lines = []
    for line in block.splitlines():
        line = line.strip()
        if (line.startswith(("-", "*", "•"))
                or re.match(r"^\d+\.", line)
                or re.match(r"^(kubectl|helm|helmfile|docker)\b", line)):
            lines.append(re.sub(r"^[-*•\d.]+\s*", "", line).strip())
    return [ln for ln in lines if ln]
