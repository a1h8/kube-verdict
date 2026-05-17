"""
KubeWhisperer — CLI demo (VHS/asciinema-recordable).
No real Kubernetes cluster required.

Usage:
    /opt/homebrew/bin/python3.11 demo/cli_demo.py
"""
from __future__ import annotations
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(dotenv_path=ROOT / ".env", override=False)

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.rule import Rule

console = Console()

from demo.scenario_builder import build_graph, generate_patch_diffs, INCIDENTS, HEALED_INCIDENTS


def _cluster_table(incidents: list[dict], title: str) -> Table:
    table = Table(title=title, show_header=True, header_style="bold cyan",
                  border_style="dim", padding=(0, 1))
    table.add_column("Service",  style="bold", min_width=20)
    table.add_column("Status",   min_width=18)
    table.add_column("↺",        justify="right", min_width=3)
    table.add_column("Cause",    style="dim")

    _icon  = {"critical": "🔴", "high": "🟠", "medium": "🟡", "ok": "🟢"}
    _style = {"critical": "bold red", "high": "bold yellow", "medium": "yellow", "ok": "bold green"}

    for inc in incidents:
        sev   = inc["severity"]
        style = _style.get(sev, "white")
        table.add_row(
            inc["service"],
            Text(f"{_icon.get(sev,'')} {inc['status']}", style=style),
            str(inc["restarts"]) if inc["restarts"] else "—",
            inc["cause"],
        )
    return table


def _build_infra():
    from vectorstore.embedder import Embedder
    from vectorstore.store import FAISSStore
    from workflow.graph import build_graph as build_wf

    graph = build_graph()
    store = FAISSStore(embedder=Embedder())
    store.index_graph(graph)
    store.index_anchor_violations(graph)
    wf = build_wf()
    return graph, store, wf


_NODE_LABELS: dict[str, tuple[str, str]] = {
    "ingest":          ("✅", "Cluster state collected"),
    "metrics":         ("✅", "Metrics-server checked"),
    "prometheus":      ("✅", "Prometheus alerts correlated"),
    "otel":            ("✅", "OTel traces / Loki logs gathered"),
    "gitops":          ("✅", "GitOps drift checked"),
    "anchor":          ("✅", "Manifest anchors evaluated"),
    "index":           ("✅", "FAISS index ready"),
    "signal_analysis": ("✅", "Signal analysis (PatchTST)"),
    "hypothesize":     ("✅", "Hypotheses generated"),
    "example_lookup":  ("✅", "Knowledge base searched"),
    "analyze":         ("✅", "LLM root-cause analysis done"),
    "increment_retry": ("🔄", "Retry — widening BFS"),
    "archive_path":    ("📁", "Path archived → next hypothesis"),
    "select_best":     ("✅", "Best hypothesis selected"),
    "dry_run":         ("✅", "Dry-run validation done"),
    "human_review":    ("✅", "Human review gate"),
    "remediation":     ("✅", "Remediation applied"),
    "save_example":    ("✅", "Saved to knowledge base"),
}


async def _run() -> None:
    import os
    from langgraph.types import Command

    _provider = os.getenv("LLM_PROVIDER", "ollama").lower()
    _model = {
        "ollama":    os.getenv("OLLAMA_MODEL", "mistral"),
        "anthropic": os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
        "openai":    os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        "google":    os.getenv("GOOGLE_MODEL", "gemini-2.0-flash"),
    }.get(_provider, _provider)
    llm_label = f"{_provider.capitalize()}/{_model}"

    # ── Header ────────────────────────────────────────────────────────────────
    console.print()
    console.rule("[bold cyan]🔍 KubeWhisperer[/]  [dim]Automated Root Cause Analysis[/]")
    console.print()

    # ── Broken cluster state ──────────────────────────────────────────────────
    console.print(_cluster_table(INCIDENTS, "Cluster state — [bold red]DEGRADED[/]"))
    console.print()

    query = (
        "Multiple services are down in the kubewhisperer-demo namespace. "
        "Identify all root causes and provide precise remediation commands."
    )
    console.print(Panel(query, title="[bold]Query[/]", border_style="cyan", padding=(0, 1)))
    console.print()
    console.rule("[yellow]Running analysis…[/]")
    console.print()

    # ── Build infra ───────────────────────────────────────────────────────────
    graph, store, wf = _build_infra()
    patches = generate_patch_diffs(build_graph())   # fresh graph for correct diffs

    cfg_run = {
        "configurable": {
            "thread_id":    f"cli-demo-{int(time.time())}",
            "graph":        graph,
            "store":        store,
            "auto_approve": True,
        }
    }
    initial_state = {
        "query":      query,
        "namespaces": ["kubewhisperer-demo"],
        "edge_log":   [],
    }

    t0          = time.time()
    raw_analysis = ""

    # ── Stream nodes ──────────────────────────────────────────────────────────
    async for updates in wf.astream(initial_state, cfg_run, stream_mode="updates"):
        for node_name, node_output in updates.items():
            t_now = round(time.time() - t0, 1)
            if node_name in _NODE_LABELS:
                icon, label = _NODE_LABELS[node_name]
                suffix = f"  [dim]({llm_label})[/]" if node_name == "analyze" else ""
                console.print(f"  [{t_now:5.1f}s]  {icon}  {label}{suffix}")
            if node_name == "example_lookup":
                console.print(f"          [yellow]⏳  Calling {llm_label}…[/]")
            if node_name in ("analyze", "archive_path"):
                raw = (node_output or {}).get("raw_analysis", "")
                if raw:
                    raw_analysis = raw

    # Auto-approve if human review interrupted
    snapshot = wf.get_state(cfg_run)
    if snapshot.next:
        t_now = round(time.time() - t0, 1)
        console.print(f"  [{t_now:5.1f}s]  🤖  Auto-approve — applying remediation…")
        async for updates in wf.astream(Command(resume="approve"), cfg_run, stream_mode="updates"):
            for node_name, node_output in updates.items():
                t_now = round(time.time() - t0, 1)
                if node_name in _NODE_LABELS:
                    icon, label = _NODE_LABELS[node_name]
                    console.print(f"  [{t_now:5.1f}s]  {icon}  {label}")
        snapshot = wf.get_state(cfg_run)

    t_total = round(time.time() - t0, 1)
    report  = snapshot.values.get("report_dict") or {}
    conf_raw = (report.get("confidence") or "?").split()[0].upper()

    console.print()
    console.rule(f"[green]✅  Done in {t_total}s[/]")
    console.print()

    # ── Analysis output ───────────────────────────────────────────────────────
    if raw_analysis:
        console.print(Panel(
            raw_analysis[:2000],
            title="[bold]Root cause analysis[/]",
            border_style="green",
            padding=(0, 1),
        ))
        console.print()

    # ── Remediation commands ──────────────────────────────────────────────────
    cmds = report.get("remediation") or []
    if cmds:
        console.rule("[bold yellow]Remediation[/]")
        for cmd in cmds:
            console.print(f"  [bold cyan]$[/] {cmd}")
        console.print()

    # ── Git diffs (computed from anchor annotations) ──────────────────────────
    if patches:
        console.rule("[bold blue]Proposed patches (git diff)[/]")
        for p in patches:
            console.print(f"\n  [dim]{p['entity']}  —  {p['field']}[/]")
            for line in p["diff"].splitlines():
                if line.startswith("+") and not line.startswith("+++"):
                    console.print(f"  [bold green]{line}[/]")
                elif line.startswith("-") and not line.startswith("---"):
                    console.print(f"  [bold red]{line}[/]")
                else:
                    console.print(f"  [dim]{line}[/]")
        console.print()

    # ── Confidence + healed state ─────────────────────────────────────────────
    _conf_style = {"HIGH": "bold green", "MEDIUM": "bold yellow", "LOW": "bold red"}
    console.print(f"  Confidence  [{_conf_style.get(conf_raw, 'white')}]{conf_raw}[/]")
    console.print()
    console.print(_cluster_table(HEALED_INCIDENTS, "Cluster state — [bold green]HEALTHY[/]"))
    console.print()
    console.print("[bold green]DONE[/]")


if __name__ == "__main__":
    asyncio.run(_run())
