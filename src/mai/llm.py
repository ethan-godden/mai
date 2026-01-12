"""Client for local OpenAI-compatible inference servers.

Everything in Mai that needs a language model goes through the ``LLM``
protocol defined here, never through a runtime's own library (see
docs/decisions/0002-inference-backend.md). ``OpenAICompatClient`` is the real
implementation; tests substitute a fake transport or a fake ``LLM``.

Two operations are offered:

* ``complete``: free text, streamed, with timing and token accounting.
* ``structured``: a reply validated against a Pydantic model, requested with
  JSON-schema constrained output and repaired once if it does not validate.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ValidationError

from mai.config import LLMSettings, ResolvedTier, Tier

Message = dict[str, str]
"""A chat message: ``{"role": "system" | "user" | "assistant", "content": "..."}``."""

TextCallback = Callable[[str], None]

RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})
STRUCTURED_ATTEMPTS = 2

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)
_CODE_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class LLMError(Exception):
    """Base class for model-call failures."""


class LLMConnectionError(LLMError):
    """The inference server could not be reached."""


class LLMResponseError(LLMError):
    """The inference server answered with an error."""

    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"HTTP {status}: {body}")
        self.status = status
        self.body = body


class StructuredOutputError(LLMError):
    """The model did not produce output matching the requested schema."""

    def __init__(self, schema: str, raw: str, reason: str) -> None:
        super().__init__(f"Output did not match {schema}: {reason}")
        self.raw = raw


@dataclass(frozen=True)
class Completion:
    """One finished model call and its cost."""

    text: str
    tier: Tier
    model: str
    finish_reason: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    duration_s: float
    ttft_s: float | None
    """Seconds until the first token (answer or reasoning) arrived."""

    @property
    def tokens_per_s(self) -> float | None:
        """Generation speed after the first token, or None if not measurable."""
        if not self.completion_tokens or self.ttft_s is None:
            return None
        generating = self.duration_s - self.ttft_s
        return self.completion_tokens / generating if generating > 0 else None


@dataclass(frozen=True)
class StructuredResult[T: BaseModel]:
    value: T
    completion: Completion
    attempts: int
    """1 if the first reply validated, 2 if it needed the repair turn."""


@dataclass
class TierUsage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    duration_s: float = 0.0


@dataclass
class UsageLedger:
    """Running totals per tier, so a run can report what it cost."""

    by_tier: dict[Tier, TierUsage] = field(default_factory=dict)

    def record(self, completion: Completion) -> None:
        usage = self.by_tier.setdefault(completion.tier, TierUsage())
        usage.calls += 1
        usage.prompt_tokens += completion.prompt_tokens or 0
        usage.completion_tokens += completion.completion_tokens or 0
        usage.duration_s += completion.duration_s


class LLM(Protocol):
    """What the pipeline needs from a language model."""

    async def complete(
        self,
        tier: Tier,
        messages: Sequence[Message],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        on_text: TextCallback | None = None,
    ) -> Completion: ...

    async def structured[T: BaseModel](
        self,
        tier: Tier,
        messages: Sequence[Message],
        schema: type[T],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> StructuredResult[T]: ...


def strip_reasoning(text: str) -> str:
    """Remove inline ``<think>`` blocks some models emit before their answer."""
    return _THINK_BLOCK.sub("", text).strip()


def extract_json(text: str) -> str:
    """Pull the JSON object out of a reply that may wrap it in prose or a code fence."""
    text = strip_reasoning(text)
    fenced = _CODE_FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if 0 <= start < end else text


class OpenAICompatClient:
    """``LLM`` implementation for Ollama, mlx-lm server, LM Studio, llama.cpp and similar."""

    def __init__(
        self,
        settings: LLMSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._settings = settings
        self._sleep = sleep
        # No read timeout between tokens would hang forever on a stalled server;
        # the same generous limit covers slow prompt processing on large contexts.
        self._http = httpx.AsyncClient(
            transport=transport, timeout=httpx.Timeout(settings.timeout_s, connect=10.0)
        )
        self.usage = UsageLedger()

    async def __aenter__(self) -> OpenAICompatClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def complete(
        self,
        tier: Tier,
        messages: Sequence[Message],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        on_text: TextCallback | None = None,
    ) -> Completion:
        return await self._call(tier, messages, max_tokens, temperature, on_text, extra={})

    async def structured[T: BaseModel](
        self,
        tier: Tier,
        messages: Sequence[Message],
        schema: type[T],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> StructuredResult[T]:
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": schema.__name__,
                "schema": schema.model_json_schema(),
                "strict": True,
            },
        }
        conversation = list(messages)
        completion: Completion | None = None
        reason = ""
        for attempt in range(1, STRUCTURED_ATTEMPTS + 1):
            completion = await self._call(
                tier,
                conversation,
                max_tokens,
                temperature,
                on_text=None,
                extra={"response_format": response_format},
            )
            try:
                value = schema.model_validate_json(extract_json(completion.text))
            except ValidationError as error:
                reason = str(error)
                conversation += [
                    {"role": "assistant", "content": completion.text},
                    {
                        "role": "user",
                        "content": "That reply did not match the required JSON schema:\n"
                        f"{reason}\nReply again with only the corrected JSON object.",
                    },
                ]
            else:
                return StructuredResult(value=value, completion=completion, attempts=attempt)
        assert completion is not None
        raise StructuredOutputError(schema.__name__, completion.text, reason)

    async def _call(
        self,
        tier_name: Tier,
        messages: Sequence[Message],
        max_tokens: int | None,
        temperature: float | None,
        on_text: TextCallback | None,
        extra: dict[str, Any],
    ) -> Completion:
        tier = self._settings.tier(tier_name)
        if not tier.model:
            raise LLMError(f"No model configured for the '{tier_name}' tier.")
        payload = {
            "model": tier.model,
            "messages": list(messages),
            "max_tokens": max_tokens if max_tokens is not None else tier.max_tokens,
            "temperature": temperature if temperature is not None else tier.temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
            **extra,
        }
        emitted = False

        def forward(text: str) -> None:
            nonlocal emitted
            emitted = True
            if on_text:
                on_text(text)

        attempt = 0
        while True:
            try:
                completion = await self._stream(tier, payload, forward)
            except (httpx.TransportError, LLMResponseError) as error:
                retryable = not isinstance(error, LLMResponseError) or (
                    error.status in RETRYABLE_STATUS
                )
                # Never retry once text has reached the caller: it would be duplicated.
                if not retryable or emitted or attempt >= self._settings.max_retries:
                    if isinstance(error, LLMResponseError):
                        raise
                    raise LLMConnectionError(
                        f"Could not reach {tier.base_url}: {type(error).__name__}: {error}"
                    ) from error
                await self._sleep(0.5 * 2**attempt)
                attempt += 1
            else:
                self.usage.record(completion)
                return completion

    async def _stream(
        self, tier: ResolvedTier, payload: dict[str, Any], on_text: TextCallback
    ) -> Completion:
        started = time.perf_counter()
        ttft: float | None = None
        parts: list[str] = []
        usage: dict[str, Any] = {}
        finish_reason: str | None = None

        async with self._http.stream(
            "POST",
            f"{tier.base_url}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {tier.api_key}"},
        ) as response:
            if response.status_code >= 400:
                body = (await response.aread()).decode(errors="replace")
                raise LLMResponseError(response.status_code, body[:500])
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line.removeprefix("data:").strip()
                if data == "[DONE]":
                    break
                event = json.loads(data)
                if event.get("error"):
                    raise LLMResponseError(response.status_code, json.dumps(event["error"])[:500])
                usage = event.get("usage") or usage
                for choice in event.get("choices") or []:
                    delta = choice.get("delta") or {}
                    text = delta.get("content") or ""
                    reasoning = delta.get("reasoning") or delta.get("reasoning_content")
                    if ttft is None and (text or reasoning):
                        ttft = time.perf_counter() - started
                    if text:
                        parts.append(text)
                        on_text(text)
                    finish_reason = choice.get("finish_reason") or finish_reason

        return Completion(
            text="".join(parts),
            tier=tier.name,
            model=tier.model,
            finish_reason=finish_reason,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            duration_s=time.perf_counter() - started,
            ttft_s=ttft,
        )
