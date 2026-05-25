#!/usr/bin/env python3
"""
KubeWhisperer — Alertmanager webhook demo.

Shows the full alert → RCA pipeline:
  Alertmanager FIRING  →  POST /webhook/alertmanager
  → session created    →  GET  /sessions/{id}/state  (polling)
  → RCA completed      →  incident report printed

Usage
─────
  # Against a running API server (uvicorn api.app:app)
  python demo/demo_webhook.py

  # Custom server
  python demo/demo_webhook.py --api http://kubewhisperer.internal:8000

  # Offline — no server needed, uses in-process ASGI transport
  python demo/demo_webhook.py --offline

  # Custom payload
  python demo/demo_webhook.py --payload demo/alertmanager_payload.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

PAYLOAD_FILE = Path(__file__).parent / "alertmanager_payload.json"
W = 68
POLL_INTERVAL = 0.5
TIMEOUT = 120


# ── formatting ────────────────────────────────────────────────────────────────

def _banner(title: str) -> str:
    return f"\n{'═' * W}\n  {title}\n{'═' * W}"

def _section(title: str) -> str:
    return f"\n  {'─' * (W - 2)}\n  {title}\n  {'─' * (W - 2)}"

def _bullet(items: list, indent: int = 4) -> str:
    pad = " " * indent
    return "\n".join(f"{pad}• {i}" for i in items) if items else f"{pad}(none)"


# ── core ──────────────────────────────────────────────────────────────────────

async def run(api_base: str, payload: dict, offline: bool) -> None:
    import httpx

    transport = None
    if offline:
        from api.app import app as _asgi_app
        transport = httpx.ASGITransport(app=_asgi_app)  # type: ignore[arg-type]
        api_base = "http://test"

    async with httpx.AsyncClient(transport=transport, base_url=api_base, timeout=10) as client:

        # ── Step 1: send webhook ─────────────────────────────────────────────
        print(_banner("KubeWhisperer — Alertmanager → RCA Demo"))

        alerts = payload.get("alerts", [])
        firing = [a for a in alerts if a.get("status") == "firing"]
        print(f"\n  Payload      : {len(alerts)} alert(s), {len(firing)} firing")
        for a in firing:
            lbl = a.get("labels", {})
            print(f"  Alert        : {lbl.get('alertname')}  "
                  f"pod={lbl.get('pod', '—')}  "
                  f"ns={lbl.get('namespace', '—')}  "
                  f"severity={lbl.get('severity', '—')}")
        annotation_summary = (firing[0].get("annotations", {}).get("summary", "") if firing else "")
        if annotation_summary:
            print(f"  Description  : {annotation_summary}")

        print(_section("Step 1/3  Sending webhook to KubeWhisperer"))
        t0 = time.perf_counter()
        resp = await client.post("/api/v1/webhook/alertmanager", json=payload)
        resp.raise_for_status()
        body = resp.json()
        session_ids: list[str] = body["session_ids"]
        skipped: int = body["skipped"]

        print(f"  Status       : {resp.status_code} Accepted")
        print(f"  Sessions     : {len(session_ids)} created  (skipped: {skipped})")
        for sid in session_ids:
            print(f"  Session ID   : {sid}")

        if not session_ids:
            print("\n  No firing alerts — nothing to investigate.")
            return

        # ── Step 2: poll until complete ──────────────────────────────────────
        print(_section("Step 2/3  RCA running…"))

        session_id = session_ids[0]
        deadline = time.perf_counter() + TIMEOUT
        status = "RUNNING"
        dots = 0

        while time.perf_counter() < deadline:
            state_resp = await client.get(f"/api/v1/sessions/{session_id}/state")
            state = state_resp.json()
            status = state.get("status", "UNKNOWN")

            if status in ("COMPLETED", "AWAITING_REVIEW", "FAILED"):
                break

            dots = (dots + 1) % 4
            print(f"  [{status}] {'.' * dots}   ", end="\r", flush=True)
            await asyncio.sleep(POLL_INTERVAL)

        print(f"  [{status}]     ")   # overwrite polling line with final status
        elapsed = time.perf_counter() - t0
        print(f"  Elapsed      : {elapsed:.1f}s")

        if status == "FAILED":
            print(f"\n  Error: {state.get('error')}")
            return

        # ── Step 3: display results ──────────────────────────────────────────
        print(_section("Step 3/3  Incident Report"))

        inc = state.get("incident_report") or {}
        report = state.get("report") or {}
        confidence = state.get("confidence") or inc.get("confidence") or "—"
        root_cause = inc.get("root_cause") or report.get("root_cause") or report.get("summary") or "—"
        remediation = inc.get("remediation") or report.get("remediation") or []
        rollback = inc.get("rollback") or report.get("rollback") or []
        blast = state.get("blast_radius") or {}

        query = state.get("query", "")
        ns_match = re.search(r"in namespace (\S+)", query)
        namespace = ns_match.group(1) if ns_match else "—"

        print(f"\n  Session      : {session_id}")
        print(f"  Confidence   : {confidence}")
        print(f"  Namespace    : {namespace}")

        print("\n  Root cause :")
        for line in root_cause.splitlines():
            print(f"    {line.strip()}")

        events = state.get("events") or []
        if events:
            print(f"\n  Key evidence ({len(events)}):")
            print(_bullet(events[:5]))

        if remediation:
            print("\n  Proposed fix :")
            print(_bullet([f"$ {c}" for c in remediation]))

        if rollback:
            print("\n  Rollback plan :")
            print(_bullet([f"$ {c}" for c in rollback]))

        if blast:
            risk = blast.get("risk", "—")
            summary = blast.get("summary", "")
            print(f"\n  Blast radius : {risk}  —  {summary}")

        if status == "AWAITING_REVIEW":
            print(f"\n  {'─' * (W - 2)}")
            print("  Human approval required.\n")
            print(f"  curl -s -X POST {api_base}/api/v1/sessions/{session_id}/feedback \\")
            print( "       -H 'Content-Type: application/json' \\")
            print( "       -d '{\"human_decision\": \"approve\"}'")
            print(f"\n  {'─' * (W - 2)}")

            # ── Step 4: interactive approval ─────────────────────────────────
            print(_section("Step 4/4  Approve remediation? [y/N]"))
            try:
                answer = input("  > ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = ""

            if answer in ("y", "yes"):
                resp = await client.post(
                    f"/api/v1/sessions/{session_id}/feedback",
                    json={"human_decision": "approve"},
                )
                resp.raise_for_status()
                print("\n  ✓  Approved — applying remediation...")

                # ── Step 5: apply fix + watch pods ────────────────────────────
                import shlex
                import subprocess
                ns_labels = payload.get("commonLabels", {}).get("namespace", "")
                print(_section("Step 5/5  Applying fix manifests"))
                fix = subprocess.run(
                    ["bash", "demo/cluster_setup.sh", "--fix"],
                    capture_output=False,
                )
                if fix.returncode == 0 and ns_labels:
                    print(_section(f"Pods in {ns_labels}"))
                    result = subprocess.run(
                        shlex.split(f"kubectl get pods -n {ns_labels}"),
                        capture_output=True, text=True,
                    )
                    print(result.stdout)
            else:
                print("\n  Skipped — run manually:")
                print(f"  curl -s -X POST {api_base}/api/v1/sessions/{session_id}/feedback \\")
                print( "       -H 'Content-Type: application/json' \\")
                print( "       -d '{\"human_decision\": \"approve\"}'")

        print(f"\n{'═' * W}\n")


# ── entrypoint ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="KubeWhisperer Alertmanager webhook demo")
    parser.add_argument("--api", default="http://localhost:8001", help="API base URL")
    parser.add_argument("--payload", default=str(PAYLOAD_FILE), help="Alertmanager payload JSON file")
    parser.add_argument("--offline", action="store_true",
                        help="Run in-process without a server (mocks graph execution)")
    args = parser.parse_args()

    payload_path = Path(args.payload)
    if not payload_path.exists():
        print(f"Payload file not found: {payload_path}", file=sys.stderr)
        sys.exit(1)

    payload = json.loads(payload_path.read_text())

    if args.offline:
        # Suppress httpx / faiss log noise — demo output only
        logging.disable(logging.INFO)

        from unittest.mock import patch
        import asyncio as _asyncio
        from api.models import SessionStatus
        from api.session_store import Session
        from tests.integration.api.conftest import COMPLETED_STATE

        import api.session_store as _ss_mod

        async def _preserve_query_graph(session: Session, initial_state: dict, resume_cmd=None):
            await asyncio.sleep(0)
            merged = {**COMPLETED_STATE, "query": initial_state.get("query", "")}
            _ss_mod.get_store().set_last_state(session.session_id, merged)
            _ss_mod.get_store().set_status(session.session_id, SessionStatus.COMPLETED)

        async def _patched():
            with patch("api.routes.sessions._run_graph", side_effect=_preserve_query_graph):
                await run(args.api, payload, offline=True)

        _asyncio.run(_patched())
    else:
        asyncio.run(run(args.api, payload, offline=False))


if __name__ == "__main__":
    main()
