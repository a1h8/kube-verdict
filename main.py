"""
KubeVerdict — command-line entry point.

Runs the canonical investigation pipeline (``services.investigation_service``), the
same LangGraph workflow as the REST API and the MCP ``kube_rca`` tool: one pipeline,
whatever the entry point (docs/fix-plan-001.md, H3). Proposal-only: it stops at the
policy verdict and never executes a remediation.

The LLM provider is selected by `LLM_PROVIDER` in .env (ollama | groq |
anthropic | openai | google | demo); defaults to Ollama for the local,
air-gapped path. See `llm.build_llm_client`.

Usage:
    python main.py --query "pods are crashlooping in namespace production"
    python main.py --load-index --query "OOMKilled on worker nodes"
    python main.py --query "ingress returns 502" --stream
    python main.py -n default -n kube-system --query "coredns not resolving"

Config is read from .env (see .env.example). CLI flags override .env values.
"""
import argparse
import asyncio
import sys

import config as cfg  # loads .env
from services.investigation_service import run_investigation, verdict_summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KubeVerdict — Kubernetes Root Cause Analysis")
    parser.add_argument("--namespace", "-n", action="append", dest="namespaces",
                        help="Namespace to analyse (repeatable). Overrides KUBE_NAMESPACES.")
    parser.add_argument("--kubeconfig", default=None)
    parser.add_argument("--context", default=None)
    parser.add_argument("--query", "-q", required=True,
                        help="Incident description or question to analyse.")
    parser.add_argument("--helmfile", default=None,
                        help="Path to helmfile.yaml or helmfile.d/. Overrides HELMFILE_PATH.")
    parser.add_argument("--helm-environment", default=None,
                        help="Helmfile environment. Overrides HELMFILE_ENVIRONMENT.")
    parser.add_argument("--load-index", action="store_true",
                        help="Use the persisted FAISS index as the knowledge store.")
    parser.add_argument("--stream", action="store_true",
                        help="Print each pipeline step as it completes.")
    parser.add_argument("--policy", action="store_true",
                        help="Deprecated: policy reports are always collected by the pipeline.")
    args = parser.parse_args(argv)

    if args.helmfile:
        cfg.HELMFILE_PATH = args.helmfile
    if args.helm_environment:
        cfg.HELMFILE_ENVIRONMENT = args.helm_environment
    if args.policy:
        print("note: --policy is no longer needed — the pipeline collects policy reports.", file=sys.stderr)

    store = None
    if args.load_index:
        from vectorstore import Embedder, FAISSStore
        store = FAISSStore(embedder=Embedder())
        store.load()

    seen: set[str] = set()

    def on_step(state: dict) -> None:
        for key in state:
            if key not in seen:
                seen.add(key)
                print(f"  · {key}", flush=True)

    if args.stream:
        print(f"Investigating: {args.query!r}")
    state = asyncio.run(run_investigation(
        query=args.query,
        namespaces=args.namespaces or cfg.KUBE_NAMESPACES or None,
        kubeconfig=args.kubeconfig or cfg.KUBECONFIG,
        kube_context=args.context or cfg.KUBE_CONTEXT,
        store=store,
        on_step=on_step if args.stream else None,
    ))
    print(_render(verdict_summary(state), state))
    return 0


def _render(summary: dict, state: dict) -> str:
    lines = [f"Query:      {summary.get('query') or ''}",
             f"Root cause: {summary.get('root_cause') or '(none)'}",
             f"Confidence: {summary.get('confidence') or '?'}",
             f"Verdict:    {summary.get('verdict') or '?'}"]
    for reason in summary.get("verdict_reasons") or []:
        lines.append(f"  - {reason}")
    if summary.get("remediation"):
        lines.append("Remediation (proposal, human approval required):")
        lines += [f"  $ {cmd}" for cmd in summary["remediation"]]
    if summary.get("diagnostics"):
        lines.append("Read-only checks:")
        for check in summary["diagnostics"]:
            command = check.get("command") if isinstance(check, dict) else str(check)
            lines.append(f"  $ {command}")
    if state.get("error"):
        lines.append(f"Error: {state['error']}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
