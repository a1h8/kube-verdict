from __future__ import annotations
import logging
import os
import time
from typing import Any  # noqa: F401

import requests

log = logging.getLogger(__name__)

_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
_RETRY_DELAYS = [15, 45, 65]  # seconds between retries on 429 (free tier: 15 RPM window)


class GoogleClient:
    def __init__(self, model: str | None = None, timeout: int = 120) -> None:
        self.model = model or os.getenv("GOOGLE_MODEL", "gemini-2.0-flash")
        self.timeout = timeout
        self._api_key = os.getenv("GOOGLE_API_KEY", "")
        if not self._api_key:
            raise RuntimeError("GOOGLE_API_KEY is not set")

    def _url(self, method: str = "generateContent") -> str:
        return f"{_API_BASE}/{self.model}:{method}?key={self._api_key}"

    def is_available(self) -> bool:
        return bool(self._api_key)

    def model_is_pulled(self) -> bool:
        return True

    def _post(self, payload: dict[str, Any]) -> str:
        last_exc: Exception | None = None
        for attempt, delay in enumerate([0] + _RETRY_DELAYS):
            if delay:
                log.warning("Google 429 — retrying in %ds (attempt %d)", delay, attempt + 1)
                time.sleep(delay)
            try:
                resp = requests.post(self._url(), json=payload, timeout=self.timeout)
                if resp.status_code == 429:
                    last_exc = RuntimeError(f"Google request failed: {resp.status_code} {resp.reason}")
                    continue
                resp.raise_for_status()
                return resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            except requests.Timeout:
                raise TimeoutError(f"Google did not respond within {self.timeout}s")
            except (KeyError, IndexError) as exc:
                raise RuntimeError(f"Unexpected Google response: {exc}") from exc
            except requests.RequestException as exc:
                raise RuntimeError(f"Google request failed: {exc}") from exc
        raise last_exc or RuntimeError("Google: max retries exceeded")

    def generate(self, prompt: str, system: str = "", temperature: float = 0.1) -> str:
        payload: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature},
        }
        if system:
            payload["system_instruction"] = {"parts": [{"text": system}]}
        log.debug("Google generate: model=%s prompt_len=%d", self.model, len(prompt))
        return self._post(payload)

def chat(self, messages: list[dict[str, str]], temperature: float = 0.1) -> str:
        system = ""
        contents = []
        for m in messages:
            if m["role"] == "system":
                system = m["content"]
            else:
                role = "model" if m["role"] == "assistant" else "user"
                contents.append({"role": role, "parts": [{"text": m["content"]}]})
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {"temperature": temperature},
        }
        if system:
            payload["system_instruction"] = {"parts": [{"text": system}]}
        return self._post(payload)
