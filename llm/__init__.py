from __future__ import annotations
import os

from .ollama_client import OllamaClient
from .anthropic_client import AnthropicClient
from .openai_client import OpenAIClient
from .google_client import GoogleClient
from .demo_client import DemoClient
from .groq_client import GroqClient


def build_llm_client():
    provider = os.getenv("LLM_PROVIDER", "ollama").lower()
    if provider == "anthropic":
        return AnthropicClient()
    if provider == "openai":
        return OpenAIClient()
    if provider == "google":
        return GoogleClient()
    if provider == "groq":
        return GroqClient()
    if provider == "demo":
        return DemoClient()
    return OllamaClient()


__all__ = ["OllamaClient", "AnthropicClient", "OpenAIClient", "GoogleClient", "GroqClient", "DemoClient", "build_llm_client"]
