"""
URL guard — what the UI's "Fetch from URL" may read (docs/fix-plan-001.md, H74).

The fetched text is shown, saved under ./data/docs and indexed, so it can reach the LLM. Only
http(s) to public addresses is read: ``file://`` would read the pod's ServiceAccount token or a
mounted kubeconfig, and a private or link-local address reaches cluster services and the cloud
metadata endpoint. Each redirect is checked again, and the token entered for a host is never
sent to another one.

Known limit: the name is resolved here and again by urllib, so a DNS answer that changes in
between (rebinding) is not caught.
"""
from __future__ import annotations

import ipaddress
import socket
import urllib.error
import urllib.request
from typing import Callable
from urllib.parse import urlparse

Resolver = Callable[..., list]


def fetch_refusal(url: str, resolve: Resolver = socket.getaddrinfo) -> str:
    """Why ``url`` must not be fetched, or ``""`` when it may be."""
    parts = urlparse(url)
    if parts.scheme not in ("http", "https"):
        return f"scheme {parts.scheme or '(none)'!r} refused: http(s) only"
    host = parts.hostname
    if not host:
        return "no host in the URL"
    try:
        infos = resolve(host, parts.port or (443 if parts.scheme == "https" else 80))
    except (OSError, UnicodeError) as exc:
        return f"cannot resolve {host}: {exc}"
    for info in infos:
        addr = ipaddress.ip_address(info[4][0].split("%")[0])
        if not addr.is_global:
            return f"{host} resolves to a non-public address ({addr})"
    return ""


class _GuardedRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, origin_host: str, resolve: Resolver) -> None:
        self._origin_host = origin_host
        self._resolve = resolve

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        refusal = fetch_refusal(newurl, self._resolve)
        if refusal:
            raise urllib.error.URLError(f"redirect to {newurl} refused: {refusal}")
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urlparse(newurl).hostname != self._origin_host:
            new.remove_header("Authorization")
        return new


def guarded_opener(url: str, resolve: Resolver = socket.getaddrinfo) -> urllib.request.OpenerDirector:
    """An opener for ``url`` (already checked) whose redirects are checked too."""
    return urllib.request.build_opener(_GuardedRedirect(urlparse(url).hostname or "", resolve))
