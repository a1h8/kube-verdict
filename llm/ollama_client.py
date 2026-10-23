from __future__ import annotations
import logging
import math
from typing import Iterator

import requests

import config as cfg

log = logging.getLogger(__name__)

_GENERATE_PATH = "/api/generate"
_CHAT_PATH = "/api/chat"
_TAGS_PATH = "/api/tags"

# Characters per token used to size the context window. Measured on mistral with real case
# prompts on 2026-10-17: 3.09 (h001, 7 233 chars → 2 343 tokens) and 3.07 (h016, 14 011 →
# 4 569). 2.7 over-estimates by ~12 %, so a prompt is never sized too small.
_CHARS_PER_TOKEN = 2.7
_CTX_RESERVE = 256      # tokens kept free on top of prompt + answer
_CTX_STEP = 1024        # num_ctx is rounded up to a multiple of this


class PromptTooLongError(ValueError):
    """The prompt would not fit under OLLAMA_NUM_CTX_MAX: refused instead of truncated."""


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
        num_ctx: int | None = None,
        num_predict: int | None = None,
        num_ctx_max: int | None = None,
    ) -> None:
        self.url = (url or cfg.OLLAMA_URL).rstrip("/")
        self.model = model or cfg.OLLAMA_MODEL
        self.timeout = timeout or cfg.OLLAMA_TIMEOUT
        self.num_ctx = num_ctx if num_ctx is not None else cfg.OLLAMA_NUM_CTX
        self.num_predict = num_predict if num_predict is not None else cfg.OLLAMA_NUM_PREDICT
        self.num_ctx_max = num_ctx_max if num_ctx_max is not None else cfg.OLLAMA_NUM_CTX_MAX
        # what the last call sent and what Ollama read (docs/case-audit-001.md F8)
        self.last_call: dict = {}

    # ------------------------------------------------------------------
    # Context window
    # ------------------------------------------------------------------

    def context_for(self, chars: int) -> tuple[int, int]:
        """(estimated prompt tokens, num_ctx to send) for a prompt of ``chars`` characters.

        ``num_ctx`` is the configured floor, grown to fit prompt + answer + reserve.
        Raises PromptTooLongError when that exceeds ``num_ctx_max``.
        """
        estimated = math.ceil(chars / _CHARS_PER_TOKEN)
        needed = estimated + self.num_predict + _CTX_RESERVE
        num_ctx = max(self.num_ctx, math.ceil(needed / _CTX_STEP) * _CTX_STEP)
        if num_ctx > self.num_ctx_max:
            raise PromptTooLongError(
                f"prompt needs ~{estimated} tokens (num_ctx {num_ctx}) but OLLAMA_NUM_CTX_MAX "
                f"is {self.num_ctx_max}: refused rather than truncated"
            )
        return estimated, num_ctx

    def _record(self, estimated: int, num_ctx: int, body: dict) -> None:
        read = body.get("prompt_eval_count")
        self.last_call = {
            "num_ctx": num_ctx,
            "estimated_prompt_tokens": estimated,
            "prompt_tokens_read": read,
            "answer_tokens": body.get("eval_count"),
            "prompt_eval_s": round((body.get("prompt_eval_duration") or 0) / 1e9, 1),
            "answer_s": round((body.get("eval_duration") or 0) / 1e9, 1),
        }

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
        estimated, num_ctx = self.context_for(len(prompt) + len(system or ""))
        payload: dict = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_ctx": num_ctx,
                "num_predict": self.num_predict,
            },
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
            body = resp.json()
            self._record(estimated, num_ctx, body)
            return body.get("response", "").strip()
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
        estimated, num_ctx = self.context_for(sum(len(m.get("content", "")) for m in messages))
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_ctx": num_ctx,
                "num_predict": self.num_predict,
            },
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
            body = resp.json()
            self._record(estimated, num_ctx, body)
            return body["message"]["content"].strip()
        except requests.Timeout:
            raise TimeoutError(
                f"Ollama did not respond within {self.timeout}s — "
                "increase OLLAMA_TIMEOUT or reduce context size"
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"Ollama chat failed: {exc}") from exc

    def stream_generate(self, prompt: str, system: str = "", temperature: float = 0.1) -> Iterator[str]:
        """Yields response tokens as they arrive (streaming mode), at the temperature `generate`
        uses — without it Ollama's default (~0.8) applied to the streamed path (fix plan H62)."""
        estimated, num_ctx = self.context_for(len(prompt) + len(system or ""))
        payload: dict = {
            "model": self.model,
            "prompt": prompt,
            "stream": True,
            "options": {
                "temperature": temperature,
                "num_ctx": num_ctx,
                "num_predict": self.num_predict,
            },
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
                            self._record(estimated, num_ctx, chunk)
                            break
        except requests.RequestException as exc:
            raise RuntimeError(f"Ollama stream failed: {exc}") from exc
