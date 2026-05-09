from __future__ import annotations
import logging
from typing import Iterator

import requests

import config as cfg

log = logging.getLogger(__name__)

_GENERATE_PATH = "/api/generate"
_CHAT_PATH = "/api/chat"
_TAGS_PATH = "/api/tags"


class OllamaClient:
    """
    HTTP client for the Ollama local inference API.
    Supports both /api/generate (single prompt) and /api/chat (messages list).
    No data leaves the machine — all calls go to OLLAMA_URL (default: localhost).
    """

    def __init__(
        self,
        url: str | None = None,
        model: str | None = None,
        timeout: int | None = None,
    ) -> None:
        self.url = (url or cfg.OLLAMA_URL).rstrip("/")
        self.model = model or cfg.OLLAMA_MODEL
        self.timeout = timeout or cfg.OLLAMA_TIMEOUT

    # ------------------------------------------------------------------
    # Health / model availability
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        try:
            resp = requests.get(f"{self.url}{_TAGS_PATH}", timeout=5)
            return resp.status_code == 200
        except requests.RequestException:
            return False

    def list_models(self) -> list[str]:
        try:
            resp = requests.get(f"{self.url}{_TAGS_PATH}", timeout=5)
            resp.raise_for_status()
            return [m["name"] for m in resp.json().get("models", [])]
        except requests.RequestException as exc:
            log.warning("Cannot list Ollama models: %s", exc)
            return []

    def model_is_pulled(self) -> bool:
        available = self.list_models()
        # Ollama names can be "mistral" or "mistral:latest"
        return any(
            m == self.model or m.startswith(self.model + ":") or m.split(":")[0] == self.model
            for m in available
        )

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    def generate(self, prompt: str, system: str = "", temperature: float = 0.1) -> str:
        """
        Single-turn generation via /api/generate.
        temperature=0.1 keeps output deterministic enough for RCA.
        """
        payload: dict = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature},
        }
        if system:
            payload["system"] = system

        log.debug("Ollama generate: model=%s prompt_len=%d", self.model, len(prompt))
        try:
            resp = requests.post(
                f"{self.url}{_GENERATE_PATH}",
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return resp.json().get("response", "").strip()
        except requests.Timeout:
            raise TimeoutError(
                f"Ollama did not respond within {self.timeout}s — "
                "increase OLLAMA_TIMEOUT or reduce context size"
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"Ollama request failed: {exc}") from exc

    def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.1,
    ) -> str:
        """
        Multi-turn chat via /api/chat.
        messages: list of {"role": "system"|"user"|"assistant", "content": "..."}
        """
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature},
        }
        log.debug(
            "Ollama chat: model=%s messages=%d",
            self.model, len(messages),
        )
        try:
            resp = requests.post(
                f"{self.url}{_CHAT_PATH}",
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return resp.json()["message"]["content"].strip()
        except requests.Timeout:
            raise TimeoutError(
                f"Ollama did not respond within {self.timeout}s — "
                "increase OLLAMA_TIMEOUT or reduce context size"
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"Ollama chat failed: {exc}") from exc

    def stream_generate(self, prompt: str, system: str = "") -> Iterator[str]:
        """Yields response tokens as they arrive (streaming mode)."""
        payload: dict = {
            "model": self.model,
            "prompt": prompt,
            "stream": True,
        }
        if system:
            payload["system"] = system
        try:
            with requests.post(
                f"{self.url}{_GENERATE_PATH}",
                json=payload,
                stream=True,
                timeout=self.timeout,
            ) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if line:
                        import json
                        chunk = json.loads(line)
                        token = chunk.get("response", "")
                        if token:
                            yield token
                        if chunk.get("done"):
                            break
        except requests.RequestException as exc:
            raise RuntimeError(f"Ollama stream failed: {exc}") from exc
