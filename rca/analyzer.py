from __future__ import annotations
import logging
import re
import textwrap
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterator

import telemetry
from llm.ollama_client import OllamaClient
from ontology.graph import OntologyGraph
from rca.context_builder import ContextBuilder, ContextWindow
from vectorstore.store import FAISSStore

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = textwrap.dedent("""\
    You are KubeVerdict, an expert Site Reliability Engineer specialising in
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

    Confidence calibration:
    - HIGH: anchor violations or CRITICAL drift are present AND the causal chain is
      fully traceable from context. Use HIGH when the evidence is unambiguous.
    - MEDIUM: root cause is probable but one link in the causal chain is inferred
      rather than directly observed in the context.
    - LOW: context is sparse, contradictory, or the query cannot be answered
      without additional cluster data.
""")

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

_PROMPT_TEMPLATE = textwrap.dedent("""\
    ## Cluster information
    Kubernetes version   : {kube_version}
    Analysis timestamp   : {timestamp}
    Total context chunks : {total_chunks}
    Context quality score: {pre_llm_label} ({pre_llm_score:.2f}/1.00) — {pre_llm_reasons}

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
    Commands that directly FIX the root cause (kubectl apply, kubectl rollout restart,
    helm upgrade…). Do NOT list diagnostic commands such as kubectl describe,
    kubectl logs, or kubectl get — those are investigation, not remediation.

    ### 6. Confidence
    Choose LOW | MEDIUM | HIGH using this rule:
    - If the context quality score above is HIGH and anchor violations or CRITICAL
      drift are present with a complete causal chain, you MUST answer HIGH.
    - If one causal link is inferred rather than observed, answer MEDIUM.
    - If context is sparse or contradictory, answer LOW.
    Start your answer with exactly one word: HIGH, MEDIUM, or LOW.
""")

# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

_READ_ONLY_VERBS = {
    "kubectl": {"get", "describe", "logs", "top", "explain", "events",
                "api-resources", "version", "diff"},
    "helm": {"status", "get", "history", "list", "ls", "show", "template", "diff", "lint"},
}
_TEXT_FILTERS = {"grep", "head", "tail", "jq", "yq", "awk", "sort", "wc"}
_CHAINING = re.compile(r";|&&|\|\||>")


def is_read_only(cmd: str) -> bool:
    """True when the line only reads the cluster: a diagnostic, not a remediation.

    Unknown commands and prose are not read-only, so the policy gate keeps
    treating them as actions (docs/test-cases.md § h022).
    """
    line = cmd.strip().lstrip("$ ").strip()
    if not line or _CHAINING.search(line):
        return False
    first, *filters = [seg.split() for seg in line.split("|")]
    if any(not seg or seg[0] not in _TEXT_FILTERS for seg in filters):
        return False
    if len(first) < 2 or first[0] not in _READ_ONLY_VERBS:
        return False
    if first[0] == "kubectl" and first[1:3] == ["auth", "can-i"]:
        return True
    return first[1] in _READ_ONLY_VERBS[first[0]]


def split_diagnostics(lines: list[str]) -> tuple[list[str], list[str]]:
    """Split remediation lines into (actions, read-only diagnostics), order kept.

    A ``#`` comment goes with the command that follows it; trailing comments
    with no command after them stay with the actions.
    """
    actions: list[str] = []
    checks: list[str] = []
    pending: list[str] = []
    for line in lines:
        if line.strip().startswith("#"):
            pending.append(line)
            continue
        (checks if is_read_only(line) else actions).extend([*pending, line])
        pending = []
    actions.extend(pending)
    return actions, checks


def _generate_rollback(remediation: list[str]) -> list[str]:
    """Generate best-effort inverse commands for each remediation command."""
    rollback: list[str] = []
    for raw in remediation:
        cmd = raw.strip().lstrip("$ ")
        if cmd.startswith("kubectl rollout restart"):
            rollback.append(cmd.replace("rollout restart", "rollout undo"))
        elif cmd.startswith("kubectl set image"):
            parts = cmd.split()
            target = next((p for p in parts if "/" in p and p.split("/")[0] in
                           ("deployment", "deploy", "daemonset", "ds", "statefulset", "sts")), None)
            if target:
                ns_parts = [parts[i + 1] for i, p in enumerate(parts) if p == "-n" and i + 1 < len(parts)]
                ns = f"-n {ns_parts[0]}" if ns_parts else ""
                rollback.append(f"kubectl rollout undo {target} {ns}".strip())
        elif cmd.startswith("helm upgrade"):
            parts = cmd.split()
            if len(parts) >= 3:
                release = parts[2]
                ns_parts = [parts[i + 1] for i, p in enumerate(parts) if p == "-n" and i + 1 < len(parts)]
                ns = f"-n {ns_parts[0]}" if ns_parts else ""
                rollback.append(f"helm rollback {release} {ns}".strip())
        elif cmd.startswith("kubectl apply -f"):
            rollback.append(cmd.replace("apply -f", "delete -f", 1))
        elif cmd.startswith("kubectl create"):
            rollback.append(cmd.replace("create", "delete", 1))
    return rollback


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
    diagnostics: list[str] = field(default_factory=list)   # read-only checks, never gated
    confidence: str = ""
    rollback: list[str] = field(default_factory=list)

    def __post_init__(self):
        _parse_analysis(self)
        self.rollback = _generate_rollback(self.remediation)

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
        return f"{bar}\nKubeVerdict RCA\n{meta}\nQuery: {self.query}\n{bar}\n{self.raw_analysis}\n{bar}"

    def to_dict(self) -> dict:
        # Canonical decision + patch-safety projection (policy_gate + blast_radius).
        from models.incident_report import IncidentReport
        incident = IncidentReport.from_rca(self)
        return {
            "query": self.query,
            "kube_version": self.kube_version,
            "timestamp": self.timestamp.isoformat(),
            "summary": self.summary,
            "affected": self.affected,
            "root_cause": self.root_cause,
            "causal_chain": self.causal_chain,
            "remediation": self.remediation,
            "diagnostics": self.diagnostics,
            "rollback": self.rollback,
            "confidence": self.confidence,
            "context_stats": {
                "seeds": len(self.context.seeds),
                "drift": len(self.context.drift),
                "events": len(self.context.events),
                "helm": len(self.context.helm),
                "related": len(self.context.related),
                "total": self.context.total_chunks,
                "anchors": list(self.context.anchors),
                "retrieval": self.context.retrieval_stats,
                "jaccard": self.context.jaccard_stats,
            },
            "events":            list(self.context.events),
            "traces":            list(self.context.traces),
            "alerts":            list(self.context.alerts),
            "anchor_fixes":      list(self.context.anchor_fixes),
            "policy_violations": list(self.context.policy_violations),
            "pre_llm_confidence": (
                {
                    "score": self.context.pre_llm_confidence.score,
                    "label": self.context.pre_llm_confidence.label,
                    "reasons": list(self.context.pre_llm_confidence.reasons),
                }
                if self.context.pre_llm_confidence
                else None
            ),
            "blast_radius": incident.blast_radius.to_dict(),
            "decision": incident.decision.to_dict(),
            "raw_analysis": self.raw_analysis,
        }

# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------

class RCAAnalyzer:
    """
    Full KubeVerdict pipeline:

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

        _t0 = time.perf_counter()
        analysis = self.llm.generate(prompt, system=_SYSTEM_PROMPT)
        telemetry.record_llm_call_duration(time.perf_counter() - _t0, node="analyze")

        report = RCAReport(
            query=query,
            kube_version=kube_version,
            context=ctx,
            raw_analysis=analysis,
        )

        # LOW confidence fallback — enrich remediation with rule-based hypotheses
        if (report.confidence or "").upper().startswith("LOW"):
            report = _apply_rule_fallback(report, self.graph)

        return report

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
            url = getattr(self.llm, "url", "remote API")
            log.warning("LLM not reachable (%s) — check credentials/connectivity", url)
        elif not self.llm.model_is_pulled():
            log.warning("Model '%s' not available", self.llm.model)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _kube_version(graph: OntologyGraph) -> str:
    return str(graph.server_version) if graph.server_version else "unknown"


def _build_prompt(query: str, ctx: ContextWindow, kube_version: str) -> str:
    conf = ctx.pre_llm_confidence
    return _PROMPT_TEMPLATE.format(
        kube_version=kube_version,
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        total_chunks=ctx.total_chunks,
        pre_llm_label=conf.label if conf else "N/A",
        pre_llm_score=conf.score if conf else 0.0,
        pre_llm_reasons=", ".join(conf.reasons) if conf else "",
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
    report.remediation, report.diagnostics = split_diagnostics(_extract_bullets(remed_block))


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
    # strip trailing markdown header artifacts (e.g. lone "###" left by LLM)
    cleaned = re.sub(r'\n\s*#{1,6}\s*$', '', snippet.strip())
    return cleaned.strip()


def _extract_bullets(block: str) -> list[str]:
    lines = []
    for line in block.splitlines():
        line = line.strip()
        if (line.startswith(("-", "*", "•"))
                or re.match(r"^\d+\.", line)
                or re.match(r"^(kubectl|helm|helmfile|docker)\b", line)):
            lines.append(re.sub(r"^[-*•\d.]+\s*", "", line).strip())
    return [ln for ln in lines if ln]


def _apply_rule_fallback(report: RCAReport, graph: OntologyGraph) -> RCAReport:
    """
    Enrich a LOW-confidence report with rule-based weighted hypotheses.

    Decision fields (summary, root_cause, causal_chain, affected) are populated
    from the top-weighted hypothesis when the LLM left them empty.
    Commands are appended to report.remediation for all matching hypotheses.
    """
    try:
        from rca.remediation_engine import RemediationEngine
        hypotheses = RemediationEngine().score(graph)
    except Exception as exc:
        log.warning("remediation_engine failed: %s", exc)
        return report

    if not hypotheses:
        return report

    top = hypotheses[0]

    # ── Structured decision fields: fill gaps left by the LLM ────────────────
    if not report.summary:
        report.summary = (
            f"{top.symptom} on {top.affected} "
            f"(rule: {top.rule_id}, confidence: {top.weight:.0%})"
        )

    if not report.root_cause:
        report.root_cause = top.explanation or top.symptom

    if not report.causal_chain:
        chain: list[str] = []
        for h in hypotheses[:3]:
            for ev in h.evidence:
                entry = f"[{h.rule_id}] {ev}"
                if entry not in chain:
                    chain.append(entry)
        if chain:
            report.causal_chain = chain

    existing_affected = set(report.affected)
    for h in hypotheses:
        if h.affected not in existing_affected:
            report.affected.append(h.affected)
            existing_affected.add(h.affected)

    # ── Remediation / diagnostics: one header + commands per hypothesis ──────
    # The header goes with its commands: into remediation if the hypothesis has
    # an action, into diagnostics if it has a read-only check (or both).
    extra = {"remediation": [], "diagnostics": []}
    seen = {key: set(getattr(report, key)) for key in extra}

    for h in hypotheses:
        header = f"[rule:{h.rule_id} w={h.weight:.2f}] {h.symptom} ({h.affected})"
        actions, checks = split_diagnostics(list(h.commands))
        for key, cmds in (("remediation", actions), ("diagnostics", checks)):
            if not cmds:
                continue
            for line in (header, *cmds):
                if line not in seen[key]:
                    extra[key].append(line)
                    seen[key].add(line)

    for key, lines in extra.items():
        if lines:
            setattr(report, key, getattr(report, key) + lines)
    if extra["remediation"] or extra["diagnostics"]:
        log.info(
            "rule fallback: added %d action(s) and %d diagnostic(s) from %d hypothesis(es)",
            len(extra["remediation"]), len(extra["diagnostics"]), len(hypotheses),
        )

    report.confidence = (
        f"LOW (rule-assisted — top: {top.rule_id} w={top.weight:.2f}, "
        f"{len(hypotheses)} hypothesis(es))"
    )

    return report
