"""LLM provider abstraction.

Agent code asks for ``get_chat_model(settings)`` and receives a LangChain chat
model. Adding a provider (for example Ollama) means one new
``ChatModelProvider`` subclass registered below plus its environment variables;
no agent code changes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from langchain_core.language_models.chat_models import BaseChatModel

from app.core.config import Settings
from app.core.registry import Registry


class LLMUnavailableError(RuntimeError):
    pass


class ChatModelProvider(ABC):
    name: str

    @abstractmethod
    def build(self, settings: Settings) -> BaseChatModel: ...


class OpenAIProvider(ChatModelProvider):
    name = "openai"

    def build(self, settings: Settings) -> BaseChatModel:
        if not settings.openai_api_key:
            raise LLMUnavailableError("OPENAI_API_KEY is not set; the IT Intelligence Agent needs an LLM.")
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=settings.openai_model, api_key=settings.openai_api_key,
                          temperature=settings.llm_temperature, timeout=settings.llm_request_timeout_s,
                          max_retries=1)


PROVIDERS: Registry[ChatModelProvider] = Registry("LLM provider")
PROVIDERS.register(OpenAIProvider.name, OpenAIProvider())


def get_chat_model(settings: Settings) -> BaseChatModel:
    if settings.llm_provider not in PROVIDERS:
        raise LLMUnavailableError(f"Unknown LLM_PROVIDER '{settings.llm_provider}'. Available: {PROVIDERS.keys()}")
    return PROVIDERS.get(settings.llm_provider).build(settings)
