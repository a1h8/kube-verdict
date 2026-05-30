"""
Coverage-gap tests for OTLP receiver, OtelBackend._get Timeout path,
and HelmCollector constructor validation.
"""
from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from ingestion.helm_collector import HelmCollector, _safe_name
from ingestion.otel_backend import TempoBackend
from ingestion.otlp_receiver import OtlpReceiver, _str_val, _nano_to_iso


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _post_raw(port: int, body: bytes, content_type: str = "application/json") -> int:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/traces",
        data=body,
        headers={"Content-Type": content_type},
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


# ─────────────────────────────────────────────────────────────────────────────
# OtlpReceiver — HTTP edge cases
# ─────────────────────────────────────────────────────────────────────────────

class TestOtlpHttpEdgeCases:
    def _receiver(self) -> OtlpReceiver:
        port = _free_port()
        r = OtlpReceiver(host="127.0.0.1", port=port)
        r.start()
        time.sleep(0.05)
        return r

    def test_invalid_json_returns_400(self):
        r = self._receiver()
        try:
            status = _post_raw(r._port, b"not-json")
            assert status == 400
        finally:
            r.stop()

    def test_empty_body_returns_400(self):
        r = self._receiver()
        try:
            status = _post_raw(r._port, b"")
            assert status == 400
        finally:
            r.stop()

    def test_trailing_slash_path_accepted(self):
        r = self._receiver()
        try:
            payload = json.dumps({"resourceSpans": []}).encode()
            req = urllib.request.Request(
                f"http://127.0.0.1:{r._port}/v1/traces/",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                assert resp.status == 200
        finally:
            r.stop()

    def test_span_with_exception_message_event(self):
        """_merge_span must extract error from events[].attributes[exception.message]."""
        r = OtlpReceiver()
        span = {
            "traceId": "trace-evt",
            "spanId": "s1",
            "name": "op",
            "startTimeUnixNano": str(int(time.time()) * 1_000_000_000),
            "endTimeUnixNano":   str(int(time.time()) * 1_000_000_000 + 1_000_000),
            "status": {"code": 2, "message": ""},
            "events": [
                {
                    "name": "exception",
                    "attributes": [
                        {
                            "key": "exception.message",
                            "value": {"stringValue": "NullPointerException"},
                        }
                    ],
                }
            ],
        }
        payload = {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": "svc"}}
                        ]
                    },
                    "scopeSpans": [{"spans": [span]}],
                }
            ]
        }
        r._ingest(payload)
        t = r.get_trace("trace-evt")
        assert t is not None
        assert "NullPointerException" in t["error_message"]

    def test_span_already_error_stays_error_on_ok_followup(self):
        """A second OK span on the same trace must not overwrite ERROR status."""
        r = OtlpReceiver()
        now_ns = str(int(time.time()) * 1_000_000_000)

        def _make_payload(code: int) -> dict:
            return {
                "resourceSpans": [
                    {
                        "resource": {"attributes": []},
                        "scopeSpans": [{"spans": [{
                            "traceId": "trace-mixed",
                            "spanId": f"s{code}",
                            "name": "op",
                            "startTimeUnixNano": now_ns,
                            "endTimeUnixNano": now_ns,
                            "status": {"code": code},
                        }]}],
                    }
                ]
            }

        r._ingest(_make_payload(2))   # ERROR first
        r._ingest(_make_payload(1))   # then OK
        t = r.get_trace("trace-mixed")
        assert t is not None
        assert t["status"] == "ERROR"


# ─────────────────────────────────────────────────────────────────────────────
# OtlpReceiver — _str_val fallback
# ─────────────────────────────────────────────────────────────────────────────

class TestStrValFallback:
    def test_empty_dict_returns_empty_string(self):
        assert _str_val({}) == ""

    def test_unknown_key_returns_str_repr(self):
        result = _str_val({"arrayValue": [1, 2]})
        assert isinstance(result, str)
        assert result != ""


# ─────────────────────────────────────────────────────────────────────────────
# OtlpReceiver — _nano_to_iso error path
# ─────────────────────────────────────────────────────────────────────────────

class TestNanoToIsoEdges:
    def test_overflow_returns_empty(self):
        assert _nano_to_iso(10 ** 30) == ""

    def test_zero_returns_something(self):
        result = _nano_to_iso(0)
        assert isinstance(result, str)


# ─────────────────────────────────────────────────────────────────────────────
# OtelBackend._get — Timeout and RequestException paths
# ─────────────────────────────────────────────────────────────────────────────

class TestOtelBackendGetEdgeCases:
    def _backend(self) -> TempoBackend:
        return TempoBackend(url="http://localhost:9999", token="tok")

    def test_get_returns_none_on_timeout(self):
        b = self._backend()
        with patch("requests.get", side_effect=requests.Timeout()):
            result = b._get("/api/search")
        assert result is None

    def test_get_returns_none_on_request_exception(self):
        b = self._backend()
        with patch("requests.get", side_effect=requests.ConnectionError("refused")):
            result = b._get("/api/search", params={"k": "v"})
        assert result is None

    def test_get_no_params_passes_empty_dict(self):
        b = self._backend()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"ok": True}
        with patch("requests.get", return_value=mock_resp) as mock_get:
            result = b._get("/api/health")
        assert result == {"ok": True}
        # params kwarg must be passed (empty dict, not None)
        _, kwargs = mock_get.call_args
        assert "params" in kwargs


# ─────────────────────────────────────────────────────────────────────────────
# HelmCollector constructor — new validation paths
# ─────────────────────────────────────────────────────────────────────────────

class TestHelmCollectorConstructor:
    def test_valid_kubeconfig(self, tmp_path):
        kube = tmp_path / "config"
        kube.write_text("apiVersion: v1")
        c = HelmCollector(kubeconfig=str(kube))
        assert str(kube.resolve()) in c._env_flags

    def test_kubeconfig_not_found_raises(self):
        with pytest.raises(ValueError, match="kubeconfig not found"):
            HelmCollector(kubeconfig="/nonexistent/path/config")

    def test_valid_kube_context(self, tmp_path):
        kube = tmp_path / "config"
        kube.write_text("apiVersion: v1")
        c = HelmCollector(kubeconfig=str(kube), kube_context="k3s-prod")
        assert "k3s-prod" in c._env_flags

    def test_invalid_kube_context_raises(self, tmp_path):
        kube = tmp_path / "config"
        kube.write_text("apiVersion: v1")
        with pytest.raises(ValueError, match="unsafe kube_context"):
            HelmCollector(kubeconfig=str(kube), kube_context="../../../etc/passwd")

    def test_kube_context_with_shell_chars_raises(self, tmp_path):
        kube = tmp_path / "config"
        kube.write_text("apiVersion: v1")
        with pytest.raises(ValueError, match="unsafe kube_context"):
            HelmCollector(kubeconfig=str(kube), kube_context="ctx; rm -rf /")

    def test_no_args_produces_empty_flags(self):
        c = HelmCollector()
        assert c._env_flags == []

    def test_kubeconfig_path_is_resolved(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        kube = sub / "config"
        kube.write_text("apiVersion: v1")
        c = HelmCollector(kubeconfig=str(kube))
        # Must be absolute resolved path, no "../" components
        assert Path(c._env_flags[1]).is_absolute()


# ─────────────────────────────────────────────────────────────────────────────
# _safe_name standalone
# ─────────────────────────────────────────────────────────────────────────────

class TestSafeName:
    def test_valid_name(self):
        assert _safe_name("my-app", "name") == "my-app"

    def test_valid_name_with_dots(self):
        assert _safe_name("my.app.v2", "name") == "my.app.v2"

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            _safe_name("", "name")

    def test_uppercase_raises(self):
        with pytest.raises(ValueError):
            _safe_name("MyApp", "name")

    def test_slash_raises(self):
        with pytest.raises(ValueError):
            _safe_name("a/b", "name")

    def test_space_raises(self):
        with pytest.raises(ValueError):
            _safe_name("a b", "name")
