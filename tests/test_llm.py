import json

import httpx
import pytest
from pydantic import BaseModel
from typer.testing import CliRunner

from mai.cli import app
from mai.config import LLMSettings, TierSettings
from mai.llm import (
    LLMConnectionError,
    LLMError,
    LLMResponseError,
    OpenAICompatClient,
    StructuredOutputError,
    extract_json,
    strip_reasoning,
)


class City(BaseModel):
    name: str
    population: int


def sse(*chunks: str, usage: tuple[int, int] | None = (7, 3), reasoning: str = "") -> bytes:
    """Build a streamed chat-completions body the way local servers send it."""
    events: list[dict] = []
    if reasoning:
        events.append({"choices": [{"delta": {"reasoning": reasoning}}]})
    events += [{"choices": [{"delta": {"content": chunk}}]} for chunk in chunks]
    events.append({"choices": [{"delta": {}, "finish_reason": "stop"}]})
    if usage:
        events.append(
            {"choices": [], "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1]}}
        )
    lines = [f"data: {json.dumps(event)}\n\n" for event in events] + ["data: [DONE]\n\n"]
    return "".join(lines).encode()


class Server:
    """Scripted fake server: returns the queued responses in order, records requests."""

    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def body(self, index: int = -1) -> dict:
        return json.loads(self.requests[index].content)


def ok(*chunks: str, **kwargs) -> httpx.Response:
    return httpx.Response(200, content=sse(*chunks, **kwargs))


def client(server: Server, **settings) -> tuple[OpenAICompatClient, list[float]]:
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    config = LLMSettings(
        base_url="http://test/v1",
        main=TierSettings(model="big"),
        fast=TierSettings(model="small"),
        **settings,
    )
    return OpenAICompatClient(config, transport=httpx.MockTransport(server), sleep=sleep), sleeps


async def test_complete_assembles_text_and_reports_usage():
    server = Server(ok("Hel", "lo"))
    llm, _ = client(server)
    seen: list[str] = []

    result = await llm.complete("main", [{"role": "user", "content": "hi"}], on_text=seen.append)

    assert result.text == "Hello"
    assert seen == ["Hel", "lo"]
    assert (result.prompt_tokens, result.completion_tokens) == (7, 3)
    assert result.finish_reason == "stop"
    assert result.ttft_s is not None and result.duration_s >= result.ttft_s
    assert llm.usage.by_tier["main"].calls == 1
    assert llm.usage.by_tier["main"].completion_tokens == 3


async def test_request_carries_tier_settings_and_overrides():
    server = Server(ok("x"), ok("y"))
    llm, _ = client(server)

    await llm.complete("fast", [{"role": "user", "content": "q"}])
    request = server.requests[0]
    assert str(request.url) == "http://test/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer local"
    assert server.body()["model"] == "small"
    assert server.body()["stream"] is True

    await llm.complete("main", [{"role": "user", "content": "q"}], max_tokens=5, temperature=0.0)
    assert (server.body()["model"], server.body()["max_tokens"]) == ("big", 5)
    assert server.body()["temperature"] == 0.0


async def test_missing_usage_and_reasoning_only_first_token():
    llm, _ = client(Server(ok("answer", usage=None, reasoning="thinking")))
    result = await llm.complete("main", [])
    assert result.text == "answer"  # reasoning is not part of the answer
    assert result.prompt_tokens is None and result.tokens_per_s is None
    assert result.ttft_s is not None


async def test_retries_transient_failures_with_backoff():
    server = Server(httpx.Response(503, text="loading"), httpx.ConnectError("down"), ok("fine"))
    llm, sleeps = client(server)
    assert (await llm.complete("main", [])).text == "fine"
    assert sleeps == [0.5, 1.0]
    assert llm.usage.by_tier["main"].calls == 1  # only the success is counted


async def test_gives_up_after_max_retries():
    llm, sleeps = client(Server(*[httpx.ConnectError("down")] * 3))
    with pytest.raises(LLMConnectionError):
        await llm.complete("main", [])
    assert len(sleeps) == 2

    llm, _ = client(Server(*[httpx.Response(503, text="busy")] * 2), max_retries=1)
    with pytest.raises(LLMResponseError) as info:
        await llm.complete("main", [])
    assert info.value.status == 503


async def test_client_errors_are_not_retried():
    server = Server(httpx.Response(404, text="model not found"))
    llm, sleeps = client(server)
    with pytest.raises(LLMResponseError, match="model not found"):
        await llm.complete("main", [])
    assert sleeps == [] and len(server.requests) == 1


async def test_error_event_inside_stream_is_raised():
    body = b'data: {"error": {"message": "context too long"}}\n\n'
    llm, _ = client(Server(httpx.Response(200, content=body)), max_retries=0)
    with pytest.raises(LLMResponseError, match="context too long"):
        await llm.complete("main", [])


async def test_unconfigured_tier_is_an_error():
    llm = OpenAICompatClient(LLMSettings(), transport=httpx.MockTransport(Server()))
    with pytest.raises(LLMError, match="No model configured"):
        await llm.complete("main", [])


async def test_structured_parses_and_sends_schema():
    server = Server(ok('{"name": "Oslo", ', '"population": 700000}'))
    llm, _ = client(server)

    result = await llm.structured("main", [{"role": "user", "content": "q"}], City)

    assert result.value == City(name="Oslo", population=700000)
    assert result.attempts == 1 and result.constrained
    assert server.body()["messages"] == [
        {"role": "user", "content": server.body()["messages"][0]["content"]}
    ]  # the schema instruction is merged into the user turn, not added as a second one
    schema = server.body()["response_format"]["json_schema"]
    assert schema["name"] == "City" and "population" in schema["schema"]["properties"]


async def test_structured_falls_back_when_server_cannot_constrain():
    unavailable = httpx.Response(501, text="structured output is unavailable")
    server = Server(
        unavailable, ok('{"name": "Oslo", "population": 2}'), ok('{"name": "X", "population": 3}')
    )
    llm, sleeps = client(server)

    first = await llm.structured("main", [{"role": "user", "content": "q"}], City)
    assert first.value.population == 2
    assert (first.attempts, first.constrained) == (1, False)
    assert "response_format" in server.body(0) and "response_format" not in server.body(1)
    assert "JSON schema" in server.body(1)["messages"][-1]["content"]
    assert sleeps == []  # a 501 is an answer, not a transient failure

    await llm.structured("main", [{"role": "user", "content": "q"}], City)
    assert len(server.requests) == 3  # the backend's limitation is remembered
    assert "response_format" not in server.body(2)


async def test_reasoning_effort_is_passed_through_when_set():
    server = Server(ok("x"), ok("y"))
    config = LLMSettings(
        main=TierSettings(model="m"), fast=TierSettings(model="f", reasoning_effort="none")
    )
    llm = OpenAICompatClient(config, transport=httpx.MockTransport(server))
    await llm.complete("fast", [])
    await llm.complete("main", [])
    assert server.body(0)["reasoning_effort"] == "none"
    assert "reasoning_effort" not in server.body(1)


async def test_structured_repairs_an_invalid_first_reply():
    server = Server(ok('{"name": "Oslo"}'), ok('```json\n{"name": "Oslo", "population": 1}\n```'))
    llm, _ = client(server)

    result = await llm.structured("main", [{"role": "user", "content": "q"}], City)

    assert result.attempts == 2 and result.value.population == 1
    repair = server.body()["messages"]
    assert [m["role"] for m in repair] == ["user", "assistant", "user"]
    assert "population" in repair[-1]["content"]  # the validation error is fed back


async def test_structured_raises_after_two_invalid_replies():
    llm, _ = client(Server(ok("nonsense"), ok("still nonsense")))
    with pytest.raises(StructuredOutputError) as info:
        await llm.structured("main", [], City)
    assert info.value.raw == "still nonsense"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"a": 1}', '{"a": 1}'),
        ('Sure! {"a": 1} Hope that helps.', '{"a": 1}'),
        ('```json\n{"a": 1}\n```', '{"a": 1}'),
        ('<think>{"draft": 0}</think>\n{"a": 1}', '{"a": 1}'),
        ("no json here", "no json here"),
    ],
)
def test_extract_json(raw, expected):
    assert extract_json(raw) == expected


def test_strip_reasoning():
    assert strip_reasoning("<think>a\nb</think> answer ") == "answer"


def test_ask_without_a_configured_model_explains_itself():
    result = CliRunner().invoke(app, ["ask", "hello"])
    assert result.exit_code == 2
    assert "No model configured" in result.output
