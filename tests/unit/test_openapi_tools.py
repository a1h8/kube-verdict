"""
Tests for the OpenAI function-calling tool schema (openapi_tools.json) and its
generator (tools/gen_openapi_tools.py).

The file is derived from mcp_server._TOOLS; these tests pin the shape and guard
against the checked-in file drifting away from the source of truth.
"""
import json
from pathlib import Path

from tools.gen_openapi_tools import build_schema, render
from mcp_server import _TOOLS

_REPO_ROOT = Path(__file__).parent.parent.parent
_OPENAPI_FILE = _REPO_ROOT / "openapi_tools.json"


class TestSchemaShape:
    def test_one_function_per_mcp_tool(self):
        schema = build_schema()
        assert len(schema) == len(_TOOLS)
        assert all(entry["type"] == "function" for entry in schema)

    def test_names_match_mcp_tools(self):
        names = {entry["function"]["name"] for entry in build_schema()}
        assert names == {t.name for t in _TOOLS}

    def test_parameters_are_json_schema_objects(self):
        for entry in build_schema():
            params = entry["function"]["parameters"]
            assert params["type"] == "object"
            assert "properties" in params
            assert "required" in params

    def test_required_fields_carried_over(self):
        by_name = {e["function"]["name"]: e["function"] for e in build_schema()}
        assert by_name["kube_rca"]["parameters"]["required"] == ["query"]
        assert by_name["helm_drift"]["parameters"]["required"] == ["release", "namespace"]
        assert by_name["blast_radius"]["parameters"]["required"] == ["remediation_commands"]

    def test_descriptions_non_empty(self):
        for entry in build_schema():
            assert entry["function"]["description"].strip()


class TestCheckedInFile:
    def test_file_exists(self):
        assert _OPENAPI_FILE.exists(), "openapi_tools.json missing — run tools/gen_openapi_tools.py"

    def test_file_is_valid_json(self):
        json.loads(_OPENAPI_FILE.read_text())

    def test_file_matches_generator(self):
        # Anti-drift: the committed file must equal freshly generated output.
        assert _OPENAPI_FILE.read_text() == render(), (
            "openapi_tools.json is stale — run: python tools/gen_openapi_tools.py"
        )
