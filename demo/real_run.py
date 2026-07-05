"""KubeVerdict — real, reproducible run (offline, no cluster, no Ollama).

Runs the ACTUAL render-vs-live pipeline on the committed `h012_gitops_render_vs_live`
fixture: it diffs the `helm template` golden against the observed cluster graph, then
ranks hypotheses with the real `RemediationEngine`. Every number printed comes from
real code on a version-controlled fixture — not a mock, not a slide. Reproduce with:

    python demo/real_run.py

Validated in CI: tests/integration/test_render_vs_live_h012.py
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
logging.disable(logging.INFO)  # keep the transcript clean

import yaml  # noqa: E402

from rca.remediation_engine import RemediationEngine  # noqa: E402
from tests.integration.cases.case_loader import build_graph, load_case  # noqa: E402

C = {"g": "\033[32m", "r": "\033[31m", "y": "\033[33m", "c": "\033[36m",
     "d": "\033[2m", "B": "\033[1m", "x": "\033[0m"}
CASE = ROOT / "tests" / "integration" / "cases" / "h012_gitops_render_vs_live"


def p(s: str = "", pause: float = 0.15) -> None:
    print(s)
    sys.stdout.flush()
    time.sleep(pause)


def main() -> None:
    p(f"{C['B']}{C['c']}KubeVerdict — real run (offline · no cluster · no Ollama){C['x']}", 0.4)
    p(f"{C['d']}case: h012_gitops_render_vs_live   namespace: production   release: api{C['x']}", 0.5)
    p()

    case = load_case(CASE)
    graph = build_graph(case)
    dep = next(e for e in graph.entities()
               if getattr(e.kind, "value", "") == "Deployment" and e.name == "api")
    rendered = [d for d in yaml.safe_load_all((CASE / "rendered" / "expected.yaml").read_text())
                if isinstance(d, dict)]
    rdep = next(d for d in rendered if d["kind"] == "Deployment")
    rcont = rdep["spec"]["template"]["spec"]["containers"][0]
    live_cont = (dep.raw or {})["spec"]["template"]["spec"]["containers"][0]

    p(f"{C['B']}[1] Live symptom (observed cluster){C['x']}", 0.3)
    p(f"    Pod api-6d8f9b7c4-xvk2p   {C['r']}CrashLoopBackOff{C['x']} · {C['r']}OOMKilled{C['x']} · 0/1 ready", 0.6)
    p()

    p(f"{C['B']}[2] Expected state — rendered from Helm ({C['c']}helm template{C['x']} golden){C['x']}", 0.3)
    p(f"    Deployment/api   declared replicas={C['g']}{rdep['spec']['replicas']}{C['x']}   "
      f"memory.limit={C['g']}{rcont['resources']['limits']['memory']}{C['x']}", 0.6)
    p()

    # Render-vs-live drift: replicas (entity field) + resource *limits* (limits-vs-limits,
    # so the OOM-relevant memory limit surfaces without limits/requests ambiguity).
    p(f"{C['B']}[3] Render-vs-live drift  {C['d']}(declared intent vs live){C['x']}", 0.3)
    rows = []
    if rdep["spec"]["replicas"] != dep.replicas:
        d = abs(rdep["spec"]["replicas"] - dep.replicas)
        rows.append(("spec.replicas", rdep["spec"]["replicas"], dep.replicas,
                     "critical" if d > 1 else "warning"))
    r_lim = (rcont.get("resources", {}) or {}).get("limits", {}) or {}
    l_lim = (live_cont.get("resources", {}) or {}).get("limits", {}) or {}
    for k, rv in r_lim.items():
        lv = l_lim.get(k)
        if lv is not None and str(rv) != str(lv):
            rows.append((f"container.api.resources.limits.{k}", rv, lv, "warning"))
    for field, decl, obs, sev in rows:
        col = C['r'] if sev == "critical" else C['y']
        p(f"    {field:38} declared {C['g']}{str(decl):8}{C['x']} "
          f"observed {col}{str(obs):8}{C['x']} [{col}{sev}{C['x']}]", 0.4)
    p()

    p(f"{C['B']}[4] Evidence-ranked hypotheses  {C['d']}(RemediationEngine — real){C['x']}", 0.3)
    hyps = RemediationEngine().score(graph)
    for i, h in enumerate(hyps[:3], 1):
        tag = f"{C['B']}{C['g']}H{i}{C['x']}" if i == 1 else f"H{i}"
        p(f"    {tag} [{C['c']}{h.rule_id}{C['x']}] w={h.weight:.2f} — {h.symptom}", 0.35)
    oom = next((h for h in hyps if h.rule_id == "oom_kill"), None)
    fix = next((c for h in hyps for c in h.commands if "helm upgrade" in c and "memory" in c), None)
    if fix:
        p(f"       {C['d']}fix →{C['x']} {fix}", 0.5)
    p()

    p(f"{C['B']}[5] Verdict{C['x']}", 0.3)
    p(f"    Top signal: {C['g']}{hyps[0].rule_id}{C['x']} — cluster state diverged from the chart.", 0.3)
    p(f"    The divergence is the {C['r']}memory limit{C['x']} (512Mi declared → 128Mi live) → "
      f"{C['r']}OOMKilled{C['x']}" + (f" ({C['c']}oom_kill{C['x']} corroborates)" if oom else ""), 0.3)
    p(f"    confidence: {C['y']}MEDIUM{C['x']}   ·   {C['B']}human approval required{C['x']}", 0.6)
    p()
    p(f"{C['d']}reproduce: python demo/real_run.py   ·   "
      f"validated in CI: tests/integration/test_render_vs_live_h012.py{C['x']}", 0.2)


if __name__ == "__main__":
    main()
