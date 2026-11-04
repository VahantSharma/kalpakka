"""One provider-agnostic client for every LLM call (borrower turns, tripwire checks, judge).

Any OpenAI-compatible `/v1/chat/completions` server works: base URL, key and model name come from
`Settings`. Anything a particular server needs beyond the standard request (e.g. vLLM/SGLang's
`chat_template_kwargs.enable_thinking`) lives in a `ServerAdapter`, so app logic never depends on
which server hosts Evon. Which adapter production uses is a hosting decision (pending ADR).

Retries: none here. A live turn has a 2 s first-token budget, so the caller decides; errors say
whether a retry could help (429 and network errors only).
"""

from collections.abc import AsyncGenerator, Sequence
from typing import Any, Protocol, cast

import openai
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from kalpakka.config import Settings

Message = dict[str, str]


class ServerAdapter(Protocol):
    def extra_body(self, *, thinking: bool) -> dict[str, Any]:
        """Server-specific request fields, merged into the JSON body."""
        ...


class PlainAdapter:
    """Standard OpenAI request only; thinking cannot be toggled through the request."""

    def extra_body(self, *, thinking: bool) -> dict[str, Any]:
        return {}


class ChatTemplateThinkingAdapter:
    """Servers that pass `chat_template_kwargs` to the model's chat template (vLLM, SGLang).
    The Evon model card documents `{"enable_thinking": false}` for vLLM."""

    def extra_body(self, *, thinking: bool) -> dict[str, Any]:
        return {"chat_template_kwargs": {"enable_thinking": thinking}}


class LLMError(Exception):
    def __init__(self, message: str, *, status: int | None, retryable: bool) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class LLMClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        adapter: ServerAdapter,
        timeout_s: float = 30.0,
    ) -> None:
        self._model = model
        self._adapter = adapter
        self._client = AsyncOpenAI(
            base_url=base_url, api_key=api_key, max_retries=0, timeout=timeout_s
        )

    @classmethod
    def from_settings(cls, settings: Settings, adapter: ServerAdapter) -> "LLMClient":
        return cls(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key.get_secret_value(),
            model=settings.llm_model,
            adapter=adapter,
        )

    async def stream_chat(
        self,
        messages: Sequence[Message],
        *,
        max_tokens: int,
        temperature: float = 0.8,
        thinking: bool = False,
    ) -> AsyncGenerator[str, None]:
        """Yield content deltas. Closing the iterator closes the HTTP stream (barge-in)."""
        extra = self._adapter.extra_body(thinking=thinking)
        try:
            stream = await self._client.chat.completions.create(
                model=self._model,
                messages=cast(list[ChatCompletionMessageParam], list(messages)),
                stream=True,
                max_tokens=max_tokens,
                temperature=temperature,
                extra_body=extra or None,
            )
        except openai.APIStatusError as exc:
            raise LLMError(
                f"LLM server returned {exc.status_code}",
                status=exc.status_code,
                retryable=exc.status_code == 429,
            ) from exc
        except (openai.APIConnectionError, openai.APITimeoutError) as exc:
            raise LLMError(f"LLM server unreachable: {exc}", status=None, retryable=True) from exc

        try:
            async for chunk in stream:
                for choice in chunk.choices:
                    if choice.delta.content:
                        yield choice.delta.content
        except (openai.APIConnectionError, openai.APITimeoutError) as exc:
            raise LLMError(f"LLM stream broke: {exc}", status=None, retryable=True) from exc
        finally:
            await stream.close()
