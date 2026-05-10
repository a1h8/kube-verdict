"""
Unit tests for OllamaClient — all HTTP calls are mocked.
"""
import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from llm.ollama_client import OllamaClient


@pytest.fixture
def client():
    return OllamaClient(url="http://localhost:11434", model="mistral", timeout=30)


def _mock_resp(json_data: dict, status: int = 200) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_data
    r.raise_for_status = MagicMock()
    return r


# ─────────────────────────────────────────────────────────────────────────────
# is_available
# ─────────────────────────────────────────────────────────────────────────────

class TestIsAvailable:
    def test_returns_true_on_200(self, client):
        with patch("requests.get", return_value=_mock_resp({}, 200)):
            assert client.is_available() is True

    def test_returns_false_on_connection_error(self, client):
        with patch("requests.get", side_effect=requests.ConnectionError()):
            assert client.is_available() is False

    def test_returns_false_on_non_200(self, client):
        with patch("requests.get", return_value=_mock_resp({}, 503)):
            assert client.is_available() is False


# ─────────────────────────────────────────────────────────────────────────────
# list_models
# ─────────────────────────────────────────────────────────────────────────────

class TestListModels:
    def test_returns_model_names(self, client):
        resp = _mock_resp({"models": [{"name": "mistral"}, {"name": "llama2"}]})
        with patch("requests.get", return_value=resp):
            models = client.list_models()
        assert "mistral" in models
        assert "llama2" in models

    def test_returns_empty_on_error(self, client):
        with patch("requests.get", side_effect=requests.RequestException()):
            assert client.list_models() == []

    def test_returns_empty_models_key_missing(self, client):
        resp = _mock_resp({})
        with patch("requests.get", return_value=resp):
            assert client.list_models() == []


# ─────────────────────────────────────────────────────────────────────────────
# model_is_pulled
# ─────────────────────────────────────────────────────────────────────────────

class TestModelIsPulled:
    def test_exact_match(self, client):
        resp = _mock_resp({"models": [{"name": "mistral"}]})
        with patch("requests.get", return_value=resp):
            assert client.model_is_pulled() is True

    def test_match_with_tag(self, client):
        resp = _mock_resp({"models": [{"name": "mistral:latest"}]})
        with patch("requests.get", return_value=resp):
            assert client.model_is_pulled() is True

    def test_not_pulled(self, client):
        resp = _mock_resp({"models": [{"name": "llama2"}]})
        with patch("requests.get", return_value=resp):
            assert client.model_is_pulled() is False

    def test_empty_list_not_pulled(self, client):
        resp = _mock_resp({"models": []})
        with patch("requests.get", return_value=resp):
            assert client.model_is_pulled() is False


# ─────────────────────────────────────────────────────────────────────────────
# generate
# ─────────────────────────────────────────────────────────────────────────────

class TestGenerate:
    def test_returns_response_text(self, client):
        resp = _mock_resp({"response": "  The root cause is X.  "})
        with patch("requests.post", return_value=resp):
            result = client.generate("diagnose this")
        assert result == "The root cause is X."

    def test_raises_timeout_error(self, client):
        with patch("requests.post", side_effect=requests.Timeout()):
            with pytest.raises(TimeoutError, match="Ollama did not respond"):
                client.generate("prompt")

    def test_raises_runtime_error_on_request_exception(self, client):
        with patch("requests.post", side_effect=requests.ConnectionError("refused")):
            with pytest.raises(RuntimeError, match="Ollama request failed"):
                client.generate("prompt")

    def test_passes_system_prompt(self, client):
        resp = _mock_resp({"response": "ok"})
        with patch("requests.post", return_value=resp) as mock_post:
            client.generate("prompt", system="You are a K8s expert")
        payload = mock_post.call_args[1]["json"]
        assert payload["system"] == "You are a K8s expert"

    def test_no_system_key_when_empty(self, client):
        resp = _mock_resp({"response": "ok"})
        with patch("requests.post", return_value=resp) as mock_post:
            client.generate("prompt")
        payload = mock_post.call_args[1]["json"]
        assert "system" not in payload


# ─────────────────────────────────────────────────────────────────────────────
# chat
# ─────────────────────────────────────────────────────────────────────────────

class TestChat:
    def test_returns_message_content(self, client):
        resp = _mock_resp({"message": {"content": "  answer  "}})
        with patch("requests.post", return_value=resp):
            result = client.chat([{"role": "user", "content": "hello"}])
        assert result == "answer"

    def test_raises_timeout_error(self, client):
        with patch("requests.post", side_effect=requests.Timeout()):
            with pytest.raises(TimeoutError):
                client.chat([{"role": "user", "content": "hello"}])

    def test_raises_runtime_on_request_error(self, client):
        with patch("requests.post", side_effect=requests.ConnectionError()):
            with pytest.raises(RuntimeError, match="Ollama chat failed"):
                client.chat([])


# ─────────────────────────────────────────────────────────────────────────────
# stream_generate
# ─────────────────────────────────────────────────────────────────────────────

class TestStreamGenerate:
    def test_yields_tokens(self, client):
        lines = [
            json.dumps({"response": "Hello", "done": False}).encode(),
            json.dumps({"response": " world", "done": True}).encode(),
        ]
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.iter_lines.return_value = iter(lines)
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("requests.post", return_value=mock_resp):
            tokens = list(client.stream_generate("prompt"))
        assert tokens == ["Hello", " world"]

    def test_raises_runtime_on_request_error(self, client):
        with patch("requests.post", side_effect=requests.ConnectionError()):
            with pytest.raises(RuntimeError, match="stream failed"):
                list(client.stream_generate("prompt"))

    def test_skips_empty_lines(self, client):
        lines = [b"", json.dumps({"response": "hi", "done": True}).encode()]
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.iter_lines.return_value = iter(lines)
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("requests.post", return_value=mock_resp):
            tokens = list(client.stream_generate("prompt"))
        assert tokens == ["hi"]
