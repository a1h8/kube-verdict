from __future__ import annotations
import logging
from typing import Iterator

import config as cfg
from llm.base import LLMClient

log = logging.getLogger(__name__)


class OpenAIClient(LLMClient):
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout: int | None = None,
    ) -> None:
        from openai import OpenAI
        self.model = model or cfg.OPENAI_MODEL
        self._client = OpenAI(
            api_key=api_key or cfg.OPENAI_API_KEY,
            timeout=timeout or cfg.OPENAI_TIMEOUT,
        )

    def generate(self, prompt: str, system: str = "", temperature: float = 0.1) -> str:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return self.chat(messages, temperature)

    def chat(self, messages: list[dict[str, str]], temperature: float = 0.1) -> str:
        log.debug("OpenAI chat: model=%s messages=%d", self.model, len(messages))
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
        )
        return resp.choices[0].message.content.strip()

    def stream_generate(self, prompt: str, system: str = "") -> Iterator[str]:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        stream = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            stream=True,
        )
        for chunk in stream:
            token = chunk.choices[0].delta.content or ""
            if token:
                yield token
