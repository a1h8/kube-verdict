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
from rca.diagnostics import Diagnostic, is_read_only, split_diagnostics  # noqa: F401 — re-exported
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
    helm upgrade…). Diagnostic commands (kubectl describe, kubectl logs, kubectl get)
    go in section 7, not here.

    ### 6. Confidence
    Choose LOW | MEDIUM | HIGH using this rule:
    - If the context quality score above is HIGH and anchor violations or CRITICAL
      drift are present with a complete causal chain, you MUST answer HIGH.
    - If one causal link is inferred rather than observed, answer MEDIUM.
    - If context is sparse or contradictory, answer LOW.
    Start your answer with exactly one word: HIGH, MEDIUM, or LOW.

    ### 7. Checks
    Up to 5 read-only commands that would confirm or rule out the root cause above,
    one per line:
    - <command> | verifies: <what it shows> | confirms if: <output> | refutes if: <output>
    Read-only only (kubectl get / describe / logs / top, helm status / get / diff).
    Never print Secret values (no kubectl get secret -o yaml or -o json).
""")

# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

# Version of the analyze prompt above. Recorded by the veracity replay so runs made
# with different prompts are never compared as one (docs/diagnostics.md § Step 2).
PROMPT_VERSION = "v2"   # v2: section 7 "Checks" (docs/diagnostics.md § Step 2)


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
        elif cmd.startswith("kubectl create"):
            inverse = _delete_created(cmd)
            if inverse:
                rollback.append(inverse)
        # `kubectl apply -f X` has no computable inverse: apply also modifies objects that
        # existed, and `delete -f X` would remove them (docs/fix-plan-001.md, H19).
    return rollback


def _delete_created(cmd: str) -> str:
    """`kubectl create <kind> <name> [flags]` → `kubectl delete <kind> <name> [-n ns]`."""
    parts = cmd.split()
    words = [p for p in parts[2:] if not p.startswith("-")]
    ns = [parts[i + 1] for i, p in enumerate(parts) if p in ("-n", "--namespace") and i + 1 < len(parts)]
    if len(words) < 2 or "=" in words[1] or "-f" in parts or "--filename" in parts:
        return ""
    kind, name = words[0], words[1]
    if kind in ("secret", "service", "svc") and len(words) >= 3:   # `create secret generic x`
        name = words[2]
    return f"kubectl delete {kind} {name}" + (f" -n {ns[0]}" if ns else "")


# Deleting these is not undone by recreating them from a controller: data or every object in them
# goes with them.
_SELF_HEALING_DELETE = {"pod", "pods", "po"}
# A command starts with a lowercase program name; rule labels (`[rule:…] …`) and prose do not.
_COMMAND_START = re.compile(r"^[a-z][a-z0-9._-]*(\s|$)")


def unrecoverable_commands(remediation: list[str]) -> list[str]:
    """Actions with no computed inverse (docs/fix-plan-001.md, H19).

    Read-only lines, comments and prose are not actions; deleting a pod is recovered by its controller;
    any other line needs its own inverse from ``_generate_rollback`` — one inverse among several
    commands does not make the whole set reversible.
    """
    out: list[str] = []
    for raw in remediation:
        cmd = raw.strip().lstrip("$ ").strip()
        if not cmd or not _COMMAND_START.match(cmd) or is_read_only(cmd):
            continue
        parts = cmd.split()
        if parts[:2] == ["kubectl", "delete"] and len(parts) > 2 and \
                parts[2].split("/")[0].split(".")[0] in _SELF_HEALING_DELETE:
            continue
        if not _generate_rollback([cmd]):
            out.append(cmd)
    return out


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
    diagnostics: list[Diagnostic] = field(default_factory=list)   # read-only checks, never gated
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
            "diagnostics": [d.to_dict() for d in self.diagnostics],
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
    has_checks = re.search(r"7\. Checks", text, re.IGNORECASE) is not None
    report.confidence = _extract_section(text, "6. Confidence", "7. Checks" if has_checks else None)

    affected_block = _extract_section(text, "2. Affected resources", "3. Root")
    report.affected = _extract_bullets(affected_block)

    chain_block = _extract_section(text, "4. Causal chain", "5. Remediation")
    report.causal_chain = _extract_bullets(chain_block)

    remed_block = _extract_section(text, "5. Remediation", "6. Confidence")
    report.remediation, checks = split_diagnostics(_extract_bullets(remed_block))
    # Read-only lines the LLM put under Remediation: no hypothesis or expected
    # output yet — step 2 of docs/diagnostics.md gives the LLM its own section.
    recovered = [Diagnostic.from_llm_line(line) for line in checks]
    proposed = _parse_checks(_extract_section(text, "7. Checks", None)) if has_checks else []
    seen = {d.command for d in proposed}
    report.diagnostics = proposed + [d for d in recovered if d.command not in seen]


_MAX_LLM_CHECKS = 5
_SECRET_READ = re.compile(
    r"\bget\s+secrets?\b.*(-o|--output)[ =]?(yaml|json|jsonpath|go-template)", re.IGNORECASE)


def _parse_checks(block: str) -> list[Diagnostic]:
    """Section 7: ``- <command> | verifies: … | confirms if: … | refutes if: …``.

    Only read-only commands are kept (an action can never enter through here),
    never one that prints Secret data, at most five, deduplicated on the command.
    """
    out: list[Diagnostic] = []
    for raw in block.splitlines():
        line = raw.strip()
        if not line.startswith(("-", "*", "•")):
            continue
        command, *fields = [part.strip() for part in line.lstrip("-*• ").split(" | ")]
        command = command.strip("`")
        if not is_read_only(command) or _SECRET_READ.search(command):
            log.info("section 7: dropped non read-only or Secret-reading check: %s", command)
            continue
        if any(d.command == command for d in out):
            continue
        parsed = {"verifies": "", "confirms if": "", "refutes if": ""}
        for field_text in fields:
            key, _, value = field_text.partition(":")
            if key.strip().lower() in parsed:
                parsed[key.strip().lower()] = value.strip()
        out.append(Diagnostic(command=command, source="llm", purpose=parsed["verifies"],
                              confirms_if=parsed["confirms if"], refutes_if=parsed["refutes if"]))
        if len(out) == _MAX_LLM_CHECKS:
            break
    return out


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

    # ── Remediation: one header + actions per hypothesis ──────────────────────
    # ── Diagnostics: the rule's structured checks (docs/diagnostics.md) ───────
    existing_cmds: set[str] = set(report.remediation)
    extra: list[str] = []
    seen_checks = {(d.command, d.affected) for d in report.diagnostics}
    diagnostics: list[Diagnostic] = []

    def _add_diagnostic(d: Diagnostic) -> None:
        if (d.command, d.affected) not in seen_checks:
            seen_checks.add((d.command, d.affected))
            diagnostics.append(d)

    for h in hypotheses:
        # safety net: a read-only line left in commands() is still not an action
        actions, leftovers = split_diagnostics(list(h.commands))
        if [a for a in actions if not a.strip().startswith("#")]:
            header = f"[rule:{h.rule_id} w={h.weight:.2f}] {h.symptom} ({h.affected})"
            for line in (header, *actions):
                if line not in existing_cmds:
                    extra.append(line)
                    existing_cmds.add(line)
        for check in h.checks:
            _add_diagnostic(Diagnostic.from_check(check, hypothesis=h.rule_id, affected=h.affected))
        for line in leftovers:
            if not line.strip().startswith("#"):
                _add_diagnostic(Diagnostic(command=line, source="rule",
                                           hypothesis=h.rule_id, affected=h.affected))

    if extra:
        report.remediation = report.remediation + extra
    if diagnostics:
        report.diagnostics = report.diagnostics + diagnostics
    if extra or diagnostics:
        log.info(
            "rule fallback: added %d action line(s) and %d diagnostic(s) from %d hypothesis(es)",
            len(extra), len(diagnostics), len(hypotheses),
        )

    report.confidence = (
        f"LOW (rule-assisted — top: {top.rule_id} w={top.weight:.2f}, "
        f"{len(hypotheses)} hypothesis(es))"
    )

    return report
