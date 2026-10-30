"""OTLP receiver exposure — docs/fix-plan-001.md, H35: loopback by default, bearer token when
set, a body limit that also holds after gzip decompression."""
from __future__ import annotations

import gzip
import json
import socket
import time
import urllib.error
import urllib.request

import pytest

import config as cfg
from ingestion.otlp_receiver import OtlpReceiver

BODY = json.dumps({"resourceSpans": []}).encode()


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _post(port, body=BODY, headers=None) -> int:
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/traces", data=body,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


@pytest.fixture
def receiver(request):
    kwargs = getattr(request, "param", {})
    port = _port()
    r = OtlpReceiver(host="127.0.0.1", port=port, **kwargs)
    r.start()
    time.sleep(0.05)
    yield port
    r.stop()


def test_the_default_bind_is_loopback():
    assert OtlpReceiver()._host == "127.0.0.1"


@pytest.mark.skipif("OTLP_HOST" in __import__("os").environ, reason="OTLP_HOST set")
def test_the_config_default_is_loopback():
    assert cfg.OTLP_HOST == "127.0.0.1"


@pytest.mark.parametrize("receiver", [{"token": "s3cret"}], indirect=True)
def test_a_token_is_required_when_set(receiver):
    assert _post(receiver) == 401
    assert _post(receiver, headers={"Authorization": "Bearer wrong"}) == 401
    assert _post(receiver, headers={"Authorization": "Bearer s3cret"}) == 200     # control


@pytest.mark.parametrize("receiver", [{"max_body_bytes": 1024}], indirect=True)
def test_a_body_over_the_limit_is_refused(receiver):
    assert _post(receiver, body=b" " * 2048) == 413
    assert _post(receiver) == 200                                                  # control


@pytest.mark.parametrize("receiver", [{"max_body_bytes": 65536}], indirect=True)
def test_a_gzip_bomb_is_refused(receiver):
    bomb = gzip.compress(b" " * 10_000_000)                 # ~10 KB on the wire, 10 MB inflated
    assert len(bomb) < 65536
    assert _post(receiver, body=bomb, headers={"Content-Encoding": "gzip"}) == 413
    small = gzip.compress(BODY)
    assert _post(receiver, body=small, headers={"Content-Encoding": "gzip"}) == 200   # control


def test_a_gzip_body_that_inflates_past_the_limit_is_413():
    from ingestion.otlp_receiver import _TooLarge, _gunzip
    with pytest.raises(_TooLarge):
        _gunzip(gzip.compress(b" " * 100_000), 1024)
    assert _gunzip(gzip.compress(b"abc"), 1024) == b"abc"
