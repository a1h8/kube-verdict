"""The raw API calls of the discovery and the schema reader need this client's `call_api`.

A fresh image took kubernetes 37, whose `call_api` has another signature: every investigation fell
back before reading the cluster, which the suite could not see because it mocks the discovery
(docs/fix-plan-001.md, H82).
"""
import inspect
from pathlib import Path

from kubernetes.client import ApiClient

ROOT = Path(__file__).resolve().parents[2]


def test_call_api_takes_what_the_raw_calls_send():
    params = inspect.signature(ApiClient.call_api).parameters
    for name in ("response_types_map", "auth_settings", "_return_http_data_only"):
        assert name in params, f"ApiClient.call_api has no {name}: see H82"


def test_the_client_is_bounded_where_it_is_installed_from():
    for name in ("requirements.txt", "pyproject.toml"):
        assert "kubernetes>=29.0.0,<37" in (ROOT / name).read_text(), name
