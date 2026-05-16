from __future__ import annotations

from llm.base import LLMClient
from llm.ollama_client import OllamaClient


def build_llm_client() -> LLMClient:
    """Instantiate the LLM client selected by LLM_PROVIDER (default: ollama)."""
    import config as cfg
    provider = cfg.LLM_PROVIDER.lower()
    if provider == "openai":
        from llm.openai_client import OpenAIClient
        return OpenAIClient()
    if provider == "anthropic":
        from llm.anthropic_client import AnthropicClient
        return AnthropicClient()
    return OllamaClient()


__all__ = ["LLMClient", "OllamaClient", "build_llm_client"]
