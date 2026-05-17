from __future__ import annotations
import logging
import os
from typing import Any

import requests

log = logging.getLogger(__name__)

_API_URL = "https://api.groq.com/openai/v1/chat/completions"


class GroqClient:
    def __init__(self, model: str | None = None, timeout: int = 60) -> None:
        self.model = model or os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
        self.timeout = timeout
        self._api_key = os.getenv("GROQ_API_KEY", "")
        if not self._api_key:
            raise RuntimeError("GROQ_API_KEY is not set")

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
        payload = {"model": self.model, "messages": messages, "temperature": temperature}
        log.debug("Groq chat: model=%s messages=%d", self.model, len(messages))
        try:
            resp = requests.post(
                _API_URL,
                headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except requests.Timeout:
            raise TimeoutError(f"Groq did not respond within {self.timeout}s")
        except requests.RequestException as exc:
            raise RuntimeError(f"Groq request failed: {exc}") from exc
