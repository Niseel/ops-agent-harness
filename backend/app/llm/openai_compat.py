"""LLM replies, the OpenAI-compatible chat client and embedder (ADR 0003, ADR 0007).

Every client has a `model` and `async complete(messages, tools) -> LLMReply`.
The reply keeps the raw tool-call arguments string and `finish_reason`, so the
LLM gateway can tell a malformed reply from a valid one.
"""

from dataclasses import dataclass

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion

from app.config import cfg, settings


@dataclass(frozen=True)
class ToolCall:
    id: str | None
    name: str
    arguments: str  # raw JSON text from the model, not parsed here


@dataclass(frozen=True)
class LLMReply:
    content: str | None
    tool_calls: tuple[ToolCall, ...] = ()
    finish_reason: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0


def to_reply(completion: ChatCompletion) -> LLMReply:
    usage = completion.usage
    tokens = (usage.prompt_tokens, usage.completion_tokens) if usage else (0, 0)
    if not completion.choices:
        return LLMReply(None, (), None, *tokens)
    choice = completion.choices[0]
    calls = []
    for call in choice.message.tool_calls or ():
        if call.type == "function":
            calls.append(ToolCall(call.id, call.function.name, call.function.arguments))
        else:  # a custom tool call names no tool we offer, so the gateway treats it as unknown
            calls.append(ToolCall(call.id, f"custom:{call.custom.name}", call.custom.input))
    return LLMReply(choice.message.content, tuple(calls), choice.finish_reason, *tokens)


class OpenAICompatClient:
    """Chat completions against any OpenAI-compatible endpoint (LM Studio, OpenAI, OpenRouter, Groq)."""

    def __init__(self, *, base_url: str, api_key: str, model: str) -> None:
        self.model = model
        # The SDK would retry twice on its own; the gateway retries instead, so every attempt is traced.
        # Keyless local servers work with any key; the SDK refuses an empty one.
        self._sdk = AsyncOpenAI(base_url=base_url, api_key=api_key or "none", max_retries=0)

    @classmethod
    def from_settings(cls) -> "OpenAICompatClient":
        return cls(base_url=settings.llm_base_url, api_key=settings.llm_api_key, model=settings.llm_model)

    async def complete(self, messages: list[dict], tools: list[dict]) -> LLMReply:
        extra = {"tools": tools} if tools else {}
        completion = await self._sdk.chat.completions.create(
            model=self.model, messages=messages, temperature=cfg.llm.temperature, **extra
        )
        return to_reply(completion)


class OpenAICompatEmbedder:
    """Embeddings from any OpenAI-compatible endpoint (`EMBED_*`; an empty base URL or key reuses `LLM_*`)."""

    def __init__(self, *, base_url: str, api_key: str, model: str) -> None:
        self.model = model
        self._sdk = AsyncOpenAI(base_url=base_url, api_key=api_key or "none", max_retries=0, timeout=cfg.llm.timeout_s)

    @classmethod
    def from_settings(cls) -> "OpenAICompatEmbedder":
        return cls(
            base_url=settings.embed_base_url or settings.llm_base_url,
            api_key=settings.embed_api_key or settings.llm_api_key,
            model=settings.embed_model,
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        # "float": without it the SDK asks for base64, which some compatible servers do not support.
        response = await self._sdk.embeddings.create(model=self.model, input=texts, encoding_format="float")
        return [item.embedding for item in sorted(response.data, key=lambda item: item.index)]
