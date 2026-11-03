"""Google client: the API key never appears in a URL or an error — docs/fix-plan-001.md, H41."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from llm.google_client import GoogleClient

KEY = "AIza-NOT-A-REAL-KEY-0123"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", KEY)
    return GoogleClient(model="gemini-test")


def test_the_key_is_sent_in_a_header_not_in_the_url(client):
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}
    with patch("llm.google_client.requests.post", return_value=resp) as post:
        assert client.generate("hi") == "ok"
    url = post.call_args.args[0]
    assert KEY not in url
    assert post.call_args.kwargs["headers"]["x-goog-api-key"] == KEY      # control


def test_a_failed_request_does_not_carry_the_key(client):
    err = requests.ConnectionError(f"Max retries exceeded with url: /v1beta/models/x?key={KEY}")
    with patch("llm.google_client.requests.post", side_effect=err):
        with pytest.raises(RuntimeError) as exc:
            client.generate("hi")
    assert KEY not in str(exc.value) and "***" in str(exc.value)
    assert exc.value.__cause__ is None and exc.value.__suppress_context__


def test_chat_is_a_method(client):
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"candidates": [{"content": {"parts": [{"text": "pong"}]}}]}
    with patch("llm.google_client.requests.post", return_value=resp):
        assert client.chat([{"role": "system", "content": "s"},
                            {"role": "user", "content": "ping"}]) == "pong"
