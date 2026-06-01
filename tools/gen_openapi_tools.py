"""
Generate openapi_tools.json — the OpenAI function-calling tool schema —
from the single source of truth in mcp_server._TOOLS.

The MCP server already declares each tool's name, description and JSON-Schema
parameters. OpenAI function-calling uses the same shape under a thin wrapper:

    {"type": "function", "function": {name, description, parameters}}

Deriving the file from _TOOLS keeps the two agent surfaces (MCP + OpenAI /
third-party frameworks) from drifting apart.

Usage:
    python tools/gen_openapi_tools.py [--output openapi_tools.json]
    python tools/gen_openapi_tools.py --check   # exit 1 if file is stale
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent

# Importable when run as `python tools/gen_openapi_tools.py` from the repo root.
sys.path.insert(0, str(ROOT))

from mcp_server import _TOOLS  # noqa: E402


def build_schema() -> list[dict]:
    """Map mcp_server._TOOLS to OpenAI function-calling tool definitions."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.inputSchema,
            },
        }
        for tool in _TOOLS
    ]


def render() -> str:
    return json.dumps(build_schema(), indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="openapi_tools.json")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify the on-disk file matches the generated schema (CI guard).",
    )
    args = parser.parse_args()

    out = ROOT / args.output
    rendered = render()

    if args.check:
        if not out.exists() or out.read_text() != rendered:
            print(f"{args.output} is stale — run: python tools/gen_openapi_tools.py", file=sys.stderr)
            return 1
        print(f"{args.output} is up to date")
        return 0

    out.write_text(rendered)
    print(f"wrote {args.output} ({len(_TOOLS)} tools)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
