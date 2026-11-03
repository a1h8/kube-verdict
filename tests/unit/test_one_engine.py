"""One engine — docs/structured-in-product.md, rule 7. The old RemediationEngine makes no
hypothesis, no LOW fallback and no command in the product; it stays a measurement baseline
(`veracity_replay --solver rules`) and the Streamlit UI's bonus panel."""
from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[2]
PRODUCT = ["workflow", "api", "services", "rca/analyzer.py", "rca/context_builder.py",
           "main.py", "mcp_server.py", "decision", "remediation"]


def _imports_old_engine(path: Path) -> bool:
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "rca.remediation_engine":
            return True
        if isinstance(node, ast.Import) and any(a.name == "rca.remediation_engine" for a in node.names):
            return True
    return False


def test_no_product_module_imports_the_old_engine():
    files = [f for p in PRODUCT for f in ([ROOT / p] if p.endswith(".py") else (ROOT / p).rglob("*.py"))]
    assert files
    assert [str(f.relative_to(ROOT)) for f in files if _imports_old_engine(f)] == []


def test_a_low_llm_answer_gets_no_rule_made_remediation():
    from ontology.entities import Pod
    from ontology.graph import OntologyGraph
    from rca.analyzer import RCAAnalyzer
    g = OntologyGraph()
    g.add_entity(Pod(uid="p", name="api-1", namespace="prod", phase="Pending"))   # old engine fired here
    llm = MagicMock()
    llm.is_available.return_value = True
    llm.model_is_pulled.return_value = True
    llm.generate.return_value = "## 6. Confidence\nLOW\n"
    store = MagicMock()
    store.hybrid_search.return_value = []
    store.search.return_value = []
    report = RCAAnalyzer(graph=g, store=store, llm=llm).analyze("why")
    assert report.remediation == []
    assert not any("[rule:" in line for line in report.remediation)        # control is the old file
