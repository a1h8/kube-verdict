from __future__ import annotations
import logging
from typing import Iterator

import config as cfg
from llm.base import LLMClient

log = logging.getLogger(__name__)

_MAX_TOKENS = 4096


class AnthropicClient(LLMClient):
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout: int | None = None,
    ) -> None:
        from anthropic import Anthropic
        self.model = model or cfg.ANTHROPIC_MODEL
        self._client = Anthropic(
            api_key=api_key or cfg.ANTHROPIC_API_KEY,
            timeout=timeout or cfg.ANTHROPIC_TIMEOUT,
        )

    def generate(self, prompt: str, system: str = "", temperature: float = 0.1) -> str:
        kwargs: dict = {
            "model": self.model,
            "max_tokens": _MAX_TOKENS,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
        }
        if system:
            kwargs["system"] = system
        log.debug("Anthropic generate: model=%s prompt_len=%d", self.model, len(prompt))
        resp = self._client.messages.create(**kwargs)
        return resp.content[0].text.strip()

    def chat(self, messages: list[dict[str, str]], temperature: float = 0.1) -> str:
        # Anthropic: system is a top-level param, not a message role
        system = ""
        filtered = []
        for m in messages:
            if m["role"] == "system":
                system = m["content"]
            else:
                filtered.append(m)
        if not filtered:
            filtered = [{"role": "user", "content": ""}]
        kwargs: dict = {
            "model": self.model,
            "max_tokens": _MAX_TOKENS,
            "messages": filtered,
            "temperature": temperature,
        }
        if system:
            kwargs["system"] = system
        log.debug("Anthropic chat: model=%s messages=%d", self.model, len(filtered))
        resp = self._client.messages.create(**kwargs)
        return resp.content[0].text.strip()

    def stream_generate(self, prompt: str, system: str = "") -> Iterator[str]:
        kwargs: dict = {
            "model": self.model,
            "max_tokens": _MAX_TOKENS,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        with self._client.messages.stream(**kwargs) as stream:
            yield from stream.text_stream
