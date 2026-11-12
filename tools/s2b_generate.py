"""One batch of S2b scenarios written by Grok through the xAI API (docs/s2b-generation.md).

    python tools/s2b_generate.py --batch pilot-1 --dry-run            # shows what would be sent, no network
    python tools/s2b_generate.py --batch pilot-1 --model <exact id>   # one call, after the user's go

The key comes from ``XAI_API_KEY`` or ``~/.config/xai/api_key`` (mode 600 only); it is never printed or written.
One request per batch, fixed temperature, no retry. Everything sent and received is kept as given under
``tests/s2b/scenarios/grok-<date>-<batch>/``.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import stat
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRIEF = ROOT / "tests/s2b/scenarios/brief-v2.md"
OUT_ROOT = ROOT / "tests/s2b/scenarios"
API = "https://api.x.ai/v1"
TEMPERATURE = 0.2
# the pilot of docs/s2b-generation.md: 8 families with a cause, 3 with no incident, one scenario each
PILOT_CAUSE = ("quota", "netpol", "dependency", "limitrange", "hpa", "webhook", "configmap", "pdb")
PILOT_NO_INCIDENT = ("ingress", "storage", "pullsecret")


class KeyError_(RuntimeError):
    pass


def read_key(home: Path | None = None) -> tuple[str, str]:
    """(key, where it came from). Never logs the key. The file must be mode 600."""
    key = os.environ.get("XAI_API_KEY", "").strip()
    if key:
        return key, "env XAI_API_KEY"
    path = (home or Path.home()) / ".config/xai/api_key"
    if not path.exists():
        raise KeyError_("no key: set XAI_API_KEY or write ~/.config/xai/api_key (mode 600)")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode != 0o600:
        raise KeyError_(f"~/.config/xai/api_key has mode {oct(mode)}; only 0o600 is accepted")
    key = path.read_text().strip()
    if not key:
        raise KeyError_("~/.config/xai/api_key is empty")
    return key, "file ~/.config/xai/api_key"


def request_text(cause: tuple[str, ...], no_incident: tuple[str, ...]) -> str:
    lines = ["Write the following scenarios, following the brief exactly, one YAML document each, separated by ---:"]
    lines += [f"- one scenario with a cause in family `{f}` (id `{f}-1`)" for f in cause]
    lines += [f"- one scenario with no incident in family `{f}` (id `{f}-0`)" for f in no_incident]
    lines.append("Output only the YAML documents.")
    return "\n".join(lines)


def build_request(model: str, cause: tuple[str, ...], no_incident: tuple[str, ...], max_tokens: int) -> dict:
    return {"model": model, "temperature": TEMPERATURE, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": BRIEF.read_text()},
                         {"role": "user", "content": request_text(cause, no_incident)}]}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _call(method: str, path: str, key: str, body: dict | None = None, timeout: int = 900) -> tuple[bytes, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{API}{path}", data=data, method=method,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                          "User-Agent": "s2b-generate"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:      # no retry: a failure is reported, not hidden
        raw = resp.read()
    return raw, json.loads(raw)


def main(argv: list[str] | None = None, home: Path | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--batch", required=True, help="batch name, e.g. pilot-1")
    ap.add_argument("--model", default="", help="exact model id, as the API's model list gives it")
    ap.add_argument("--cause", default=",".join(PILOT_CAUSE))
    ap.add_argument("--no-incident", default=",".join(PILOT_NO_INCIDENT))
    ap.add_argument("--max-tokens", type=int, default=32000)
    ap.add_argument("--dry-run", action="store_true", help="print what would be sent; no network call")
    args = ap.parse_args(argv)
    cause = tuple(f for f in args.cause.split(",") if f)
    none = tuple(f for f in args.no_incident.split(",") if f)

    try:
        key, where = read_key(home)
        key_status = f"found ({where})"
    except KeyError_ as exc:
        key, key_status = "", f"MISSING: {exc}"

    req = build_request(args.model or "<model id from GET /v1/models>", cause, none, args.max_tokens)
    if args.dry_run:
        print(json.dumps({"would_call": [f"GET {API}/models", f"POST {API}/chat/completions"],
                          "key": key_status, "brief_sha256": sha256(BRIEF.read_bytes()),
                          "request_sha256": sha256(json.dumps(req, sort_keys=True).encode()),
                          "model": req["model"], "temperature": req["temperature"], "max_tokens": req["max_tokens"],
                          "user_message": req["messages"][1]["content"], "system_message_chars":
                          len(req["messages"][0]["content"])}, indent=1))
        return 0
    if not key:
        print(key_status, file=sys.stderr)
        return 2

    models_raw, models = _call("GET", "/models", key, timeout=60)
    ids = sorted(m.get("id", "") for m in models.get("data", []))
    if not args.model or args.model not in ids:
        print("choose --model among: " + ", ".join(ids), file=sys.stderr)
        return 1
    out = OUT_ROOT / f"grok-{dt.date.today().isoformat()}-{args.batch}"
    if out.exists():
        print(f"{out} exists: a batch is never overwritten", file=sys.stderr)
        return 1
    out.mkdir(parents=True)
    (out / "models.json").write_bytes(models_raw)
    (out / "request.json").write_text(json.dumps(req, indent=1) + "\n")
    t0 = time.perf_counter()
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    try:
        raw, resp = _call("POST", "/chat/completions", key, req)
    except Exception as exc:  # noqa: BLE001 — recorded as the batch's outcome, never retried
        (out / "provenance.json").write_text(json.dumps({"started": started, "model": args.model,
                                                         "error": repr(exc)}, indent=1) + "\n")
        print(f"call failed, recorded in {out}: {exc!r}", file=sys.stderr)
        return 1
    (out / "response.json").write_bytes(raw)
    text = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    (out / "scenarios.yaml").write_text(text)
    (out / "provenance.json").write_text(json.dumps({
        "started": started, "duration_s": round(time.perf_counter() - t0, 1), "model": args.model,
        "model_reported": resp.get("model"), "temperature": TEMPERATURE, "max_tokens": args.max_tokens,
        "brief_sha256": sha256(BRIEF.read_bytes()), "request_sha256": sha256(json.dumps(req, sort_keys=True).encode()),
        "response_sha256": sha256(raw), "usage": resp.get("usage"), "finish_reason":
        ((resp.get("choices") or [{}])[0]).get("finish_reason"), "cause": cause, "no_incident": none}, indent=1) + "\n")
    print(f"batch written to {out}; next: python tools/s2b_check.py {out / 'scenarios.yaml'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
