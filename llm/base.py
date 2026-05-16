from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Iterator


class LLMClient(ABC):
    """Common interface for all LLM providers (Ollama, OpenAI, Anthropic)."""

    @abstractmethod
    def generate(self, prompt: str, system: str = "", temperature: float = 0.1) -> str: ...

    @abstractmethod
    def chat(self, messages: list[dict[str, str]], temperature: float = 0.1) -> str: ...

    @abstractmethod
    def stream_generate(self, prompt: str, system: str = "") -> Iterator[str]: ...

    def is_available(self) -> bool:
        """Cloud providers are always reachable — local servers override this."""
        return True
