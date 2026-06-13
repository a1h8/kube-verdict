#!/usr/bin/env python3
"""
KubeVerdict — command-line entry point.

The LLM provider is selected by `LLM_PROVIDER` in .env (ollama | groq |
anthropic | openai | google | demo); defaults to Ollama for the local,
air-gapped path. See `llm.build_llm_client`.

Usage:
    # Collect from cluster, index, then run RCA
    python main.py --query "pods are crashlooping in namespace production"

    # Re-use an existing index (skip cluster collection)
    python main.py --load-index --query "OOMKilled on worker nodes"

    # Stream output token by token
    python main.py --query "ingress returns 502" --stream

    # Limit to specific namespaces
    python main.py -n default -n kube-system --query "coredns not resolving"

Config is read from .env (see .env.example). CLI flags override .env values.
"""
import argparse

import config as cfg  # loads .env
from ingestion import K8sCollector, HelmCollector, HelmfileCollector, HelmDriftDetector, PolicyCollector
from llm import build_llm_client
from rca import RCAAnalyzer
from vectorstore import Embedder, FAISSStore


def main() -> None:
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
                        help="Load an existing FAISS index instead of re-collecting.")
    parser.add_argument("--stream", action="store_true",
                        help="Stream Mistral output token by token.")
    parser.add_argument("--policy", action="store_true",
                        help="Collect OPA / Kyverno PolicyReport violations (requires wgpolicyk8s.io CRD).")
    args = parser.parse_args()

    namespaces = args.namespaces or cfg.KUBE_NAMESPACES or None
    kubeconfig = args.kubeconfig or cfg.KUBECONFIG
    context = args.context or cfg.KUBE_CONTEXT

    embedder = Embedder()
    store = FAISSStore(embedder=embedder)

    if args.load_index:
        store.load()
        # Reconstruct a minimal graph shell just to carry the server version
        # The store already has all entity texts — graph is only needed for BFS.
        # For --load-index mode we skip BFS and rely purely on FAISS.
        from ontology.graph import OntologyGraph
        graph = OntologyGraph()
    else:
        collector = K8sCollector(kubeconfig=kubeconfig, context=context)
        graph = collector.collect(namespaces=namespaces)

        helm = HelmCollector(kubeconfig=kubeconfig, kube_context=context)
        helm.collect(graph, namespaces=namespaces)

        helmfile_path = args.helmfile or cfg.HELMFILE_PATH
        if helmfile_path:
            hf = HelmfileCollector(
                helmfile_path=helmfile_path,
                environment=args.helm_environment or cfg.HELMFILE_ENVIRONMENT,
                use_cli=cfg.HELMFILE_USE_CLI,
            )
            hf.collect(graph)

        # Drift detection: Helm declared vs K8s observed + events correlation
        drift_count = HelmDriftDetector().detect_all(graph)
        if drift_count:
            import logging
            logging.getLogger(__name__).info(
                "%d drift item(s) annotated on graph entities", drift_count
            )

        if args.policy:
            policy_result = PolicyCollector().collect(graph, namespaces=namespaces)
            import logging as _logging
            _logging.getLogger(__name__).info(
                "policy violations: fail=%d audit=%d webhooks=%d",
                policy_result.fail_count,
                policy_result.audit_count,
                policy_result.mutation_webhooks,
            )

        print(graph.summary())

        store.index_graph(graph)
        store.save()

    print(store.summary())
    print()

    llm = build_llm_client()
    analyzer = RCAAnalyzer(graph=graph, store=store, llm=llm)

    if args.stream:
        print(f"Analysing: {args.query!r}\n")
        report = None
        for item in analyzer.stream_analyze(args.query):
            if isinstance(item, str):
                print(item, end="", flush=True)
            else:
                report = item
        print()
        if report:
            _print_report_meta(report)
    else:
        report = analyzer.analyze(args.query)
        print(report)
        if report.remediation:
            print("\n── Quick remediation ──")
            for cmd in report.remediation:
                print(f"  $ {cmd}")


def _print_report_meta(report) -> None:
    ctx = report.context
    print(
        f"\n[K8s: {report.kube_version}  |  "
        f"seeds={len(ctx.seeds)}  drift={len(ctx.drift)}  "
        f"events={len(ctx.events)}  helm={len(ctx.helm)}  "
        f"related={len(ctx.related)}  |  "
        f"confidence={report.confidence or '?'}]"
    )


if __name__ == "__main__":
    main()
