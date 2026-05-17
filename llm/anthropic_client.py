from __future__ import annotations
import logging
import os
from typing import Any

import requests

log = logging.getLogger(__name__)

_API_URL = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2023-06-01"


class AnthropicClient:
    def __init__(self, model: str | None = None, timeout: int = 120) -> None:
        self.model = model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
        self.timeout = timeout
        self._api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not self._api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._api_key,
            "anthropic-version": _API_VERSION,
            "content-type": "application/json",
        }

    def is_available(self) -> bool:
        return bool(self._api_key)

    def model_is_pulled(self) -> bool:
        return True

    def generate(self, prompt: str, system: str = "", temperature: float = 0.1) -> str:
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 4096,
            "temperature": temperature,
            "messages": messages,
        }
        if system:
            payload["system"] = system
        log.debug("Anthropic generate: model=%s prompt_len=%d", self.model, len(prompt))
        try:
            resp = requests.post(_API_URL, headers=self._headers(), json=payload, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()["content"][0]["text"].strip()
        except requests.Timeout:
            raise TimeoutError(f"Anthropic did not respond within {self.timeout}s")
        except requests.RequestException as exc:
            raise RuntimeError(f"Anthropic request failed: {exc}") from exc

    def chat(self, messages: list[dict[str, str]], temperature: float = 0.1) -> str:
        system = ""
        api_messages = []
        for m in messages:
            if m["role"] == "system":
                system = m["content"]
            else:
                api_messages.append({"role": m["role"], "content": m["content"]})
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 4096,
            "temperature": temperature,
            "messages": api_messages,
        }
        if system:
            payload["system"] = system
        try:
            resp = requests.post(_API_URL, headers=self._headers(), json=payload, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()["content"][0]["text"].strip()
        except requests.RequestException as exc:
            raise RuntimeError(f"Anthropic chat failed: {exc}") from exc
