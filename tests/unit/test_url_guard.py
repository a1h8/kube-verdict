"""What the UI's "Fetch from URL" may read — docs/fix-plan-001.md, H74."""
from __future__ import annotations

import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path

import pytest

from knowledge.url_guard import _GuardedRedirect, fetch_refusal, guarded_opener


def _resolves_to(*addrs):
    def resolve(host, port):
        return [(None, None, None, "", (a, port)) for a in addrs]
    return resolve


PUBLIC = _resolves_to("93.184.216.34")


@pytest.mark.parametrize("url", [
    "file:///var/run/secrets/kubernetes.io/serviceaccount/token",
    "file:///etc/hostname",
    "ftp://example.com/runbook.txt",
    "gopher://example.com/",
    "/etc/hostname",
])
def test_a_non_http_scheme_is_refused(url):
    assert "http(s) only" in fetch_refusal(url, PUBLIC)


@pytest.mark.parametrize("addr", [
    "169.254.169.254",       # cloud metadata
    "127.0.0.1", "::1",      # loopback
    "10.43.0.10",            # cluster service range
    "192.168.1.5", "172.16.0.1",
    "fd00::1",               # unique local
    "0.0.0.0",
])
def test_a_non_public_address_is_refused(addr):
    assert "non-public" in fetch_refusal("http://wiki.internal/page", _resolves_to(addr))


def test_one_private_answer_among_public_ones_is_refused():
    assert fetch_refusal("https://wiki.example.com/", _resolves_to("93.184.216.34", "10.0.0.7"))


def test_a_public_https_page_is_allowed():                                         # control
    assert fetch_refusal("https://wiki.example.com/spaces/OPS/pages/1", PUBLIC) == ""


def test_an_unresolvable_host_is_refused():
    def fail(host, port):
        raise OSError("Name or service not known")
    assert "cannot resolve" in fetch_refusal("https://nowhere.invalid/", fail)


def _redirect(handler, url, newurl):
    req = urllib.request.Request(url, headers={"Authorization": "Bearer t0ken"})
    return handler.redirect_request(req, None, 302, "Found", Message(), newurl)


def test_a_redirect_to_a_private_address_is_refused():
    handler = _GuardedRedirect("wiki.example.com", _resolves_to("169.254.169.254"))
    with pytest.raises(urllib.error.URLError, match="refused"):
        _redirect(handler, "https://wiki.example.com/a", "http://metadata.internal/latest")


def test_a_redirect_to_file_is_refused():
    handler = _GuardedRedirect("wiki.example.com", PUBLIC)
    with pytest.raises(urllib.error.URLError, match="http\\(s\\) only"):
        _redirect(handler, "https://wiki.example.com/a", "file:///etc/passwd")


def test_the_token_stays_with_its_host():
    handler = _GuardedRedirect("wiki.example.com", PUBLIC)
    same = _redirect(handler, "https://wiki.example.com/a", "https://wiki.example.com/b")
    other = _redirect(handler, "https://wiki.example.com/a", "https://cdn.example.net/b")
    assert same.get_header("Authorization") == "Bearer t0ken"                     # control
    assert other.get_header("Authorization") is None


def test_the_opener_uses_the_guarded_redirect():
    opener = guarded_opener("https://wiki.example.com/a", PUBLIC)
    assert any(isinstance(h, _GuardedRedirect) for h in opener.handlers)


def test_the_ui_fetch_goes_through_the_guard():
    # read the source rather than import ui.app (Streamlit, 3 800 lines)
    src = (Path(__file__).resolve().parents[2] / "ui" / "app.py").read_text()
    fn = src[src.index("def _fetch_enterprise_url"):]
    fn = fn[:fn.index("\ndef ", 1)]
    assert fn.index("fetch_refusal(url)") < fn.index("opener.open(")
    assert "urlopen(" not in fn
