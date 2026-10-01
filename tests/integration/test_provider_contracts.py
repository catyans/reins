"""Actual SDKs with local HTTP transports; no credentials/network/API spending."""

import json

import pytest

from reins import configure, record_outcome, trace
from reins.core.decorators import _get_runtime, shutdown
from reins.core.outcomes import compare_tasks

httpx = pytest.importorskip("httpx2")
anthropic = pytest.importorskip("anthropic")
openai = pytest.importorskip("openai")


@pytest.fixture(autouse=True)
def cleanup():
    yield
    shutdown()


def settings(tmp_path, provider, models=None):
    configure(
        storage_path=tmp_path / "sdk.db",
        mode="enforce",
        prices={provider: {"strong": ["10", "10"], "small": ["1", "1"]}},
        token_counter=lambda *args: 100,
        task_models={"extract": models or [f"{provider}/strong", f"{provider}/small"]},
    )


def payload(provider, model):
    if provider == "anthropic":
        return dict(
            id="msg_1",
            type="message",
            role="assistant",
            model=model,
            content=[{"type": "text", "text": "ok"}],
            stop_reason="end_turn",
            stop_sequence=None,
            usage={"input_tokens": 100, "output_tokens": 100},
        )
    return dict(
        id="chat_1",
        object="chat.completion",
        created=1,
        model=model,
        choices=[
            {"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
        ],
        usage={"prompt_tokens": 100, "completion_tokens": 100, "total_tokens": 200},
    )


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_real_sync_sdk_switches_actual_request(tmp_path, provider):
    sent = []

    def handler(request):
        body = json.loads(request.content)
        sent.append(body)
        return httpx.Response(200, json=payload(provider, body["model"]))

    client_cls = anthropic.Anthropic if provider == "anthropic" else openai.OpenAI
    client = client_cls(
        api_key="test-only",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    settings(tmp_path, provider)
    _get_runtime().config.budget.on_exceed = "degrade"

    @trace(budget=".0003", task_type="extract")
    def task():
        endpoint = client.messages if provider == "anthropic" else client.chat.completions
        endpoint.create(
            model="strong", messages=[{"role": "user", "content": "hello"}], max_tokens=100
        )
        record_outcome(success=True)

    try:
        task()
        assert sent[0]["model"] == "small"
        assert compare_tasks(_get_runtime().storage)[0]["cost_per_success"] == 0.0002
    finally:
        client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["anthropic", "openai"])
async def test_real_async_sdk_records(tmp_path, provider):
    async def handler(request):
        return httpx.Response(200, json=payload(provider, json.loads(request.content)["model"]))

    client_cls = anthropic.AsyncAnthropic if provider == "anthropic" else openai.AsyncOpenAI
    client = client_cls(
        api_key="test-only",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    settings(tmp_path, provider)

    @trace(budget=".01", task_type="extract")
    async def task():
        endpoint = client.messages if provider == "anthropic" else client.chat.completions
        await endpoint.create(
            model="strong", messages=[{"role": "user", "content": "hello"}], max_tokens=100
        )
        record_outcome(success=True)

    try:
        await task()
        assert compare_tasks(_get_runtime().storage)[0]["cost_per_success"] == 0.002
    finally:
        await client.close()


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_real_stream_usage(tmp_path, provider):
    def handler(request):
        if provider == "anthropic":
            message = payload(provider, "strong")
            message["content"] = []
            message["usage"]["output_tokens"] = 0
            events = [
                {"type": "message_start", "message": message},
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": 100},
                },
                {"type": "message_stop"},
            ]
            content = "".join(
                "event: " + e["type"] + "\ndata: " + json.dumps(e) + "\n\n" for e in events
            )
        else:
            event = dict(
                id="chunk",
                object="chat.completion.chunk",
                created=1,
                model="strong",
                choices=[],
                usage={"prompt_tokens": 100, "completion_tokens": 100, "total_tokens": 200},
            )
            content = "data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n"
            assert json.loads(request.content)["stream_options"]["include_usage"]
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=content)

    cls = anthropic.Anthropic if provider == "anthropic" else openai.OpenAI
    client = cls(
        api_key="test-only",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    settings(tmp_path, provider)

    @trace(budget=".01", task_type="extract")
    def task():
        endpoint = client.messages if provider == "anthropic" else client.chat.completions
        with endpoint.create(
            model="strong",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=100,
            stream=True,
        ) as stream:
            list(stream)
        record_outcome(success=True)

    try:
        task()
        row = compare_tasks(_get_runtime().storage)[0]
        assert row["cost_per_success"] == 0.002 and row["pending_requests"] == 0
    finally:
        client.close()


@pytest.mark.asyncio
async def test_google_compatibility_uses_google_prices(tmp_path):
    async def handler(request):
        return httpx.Response(200, json=payload("google", json.loads(request.content)["model"]))

    client = openai.AsyncOpenAI(
        api_key="test-only",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    configure(storage_path=tmp_path / "google.db", mode="observe")

    @trace()
    async def task():
        await client.chat.completions.create(
            model="gemini-2.5-flash",
            messages=[{"role": "user", "content": "test"}],
            max_tokens=100,
        )
        record_outcome(success=True)

    try:
        await task()
        span = _get_runtime().storage.query("SELECT provider,cost FROM spans")[0]
        assert span["provider"] == "google"
        assert float(span["cost"]) == pytest.approx(0.00028)
        assert compare_tasks(_get_runtime().storage)[0]["cost_per_success"] == pytest.approx(
            0.00028
        )
    finally:
        await client.close()
