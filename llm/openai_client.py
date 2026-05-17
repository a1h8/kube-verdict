from __future__ import annotations
import logging
import os
from typing import Any

import requests

log = logging.getLogger(__name__)

_API_URL = "https://api.openai.com/v1/chat/completions"


class OpenAIClient:
    def __init__(self, model: str | None = None, timeout: int = 120) -> None:
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self.timeout = timeout
        self._api_key = os.getenv("OPENAI_API_KEY", "")
        if not self._api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def is_available(self) -> bool:
        return bool(self._api_key)

    def model_is_pulled(self) -> bool:
        return True

    def generate(self, prompt: str, system: str = "", temperature: float = 0.1) -> str:
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return self._call(messages, temperature)

    def chat(self, messages: list[dict[str, str]], temperature: float = 0.1) -> str:
        return self._call(messages, temperature)

    def _call(self, messages: list[dict[str, Any]], temperature: float) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        log.debug("OpenAI chat: model=%s messages=%d", self.model, len(messages))
        try:
            resp = requests.post(_API_URL, headers=self._headers(), json=payload, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except requests.Timeout:
            raise TimeoutError(f"OpenAI did not respond within {self.timeout}s")
        except requests.RequestException as exc:
            raise RuntimeError(f"OpenAI request failed: {exc}") from exc
