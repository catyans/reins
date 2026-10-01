"""Local SDK overhead with identical mocked HTTP responses, no network/model calls."""

import asyncio
import json
import statistics
import tempfile
import time
from pathlib import Path

import httpx2 as httpx
from openai import AsyncOpenAI

from reins import configure, record_outcome, trace
from reins.core.decorators import shutdown


async def main():
    async def handler(request):
        return httpx.Response(
            200,
            json={
                "id": "local",
                "object": "chat.completion",
                "created": 1,
                "model": "gemini-2.5-flash",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "{}"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
            },
        )

    client = AsyncOpenAI(
        api_key="mock-only",
        max_retries=0,
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    async def call():
        return await client.chat.completions.create(
            model="gemini-2.5-flash",
            messages=[{"role": "user", "content": "local fixture"}],
            max_tokens=100,
        )

    async def measure(fn):
        for _ in range(20):
            await fn()
        samples = []
        for _ in range(200):
            t = time.perf_counter()
            await fn()
            samples.append((time.perf_counter() - t) * 1000)
        ordered = sorted(samples)
        return {"n": len(samples), "median_ms": statistics.median(samples), "p95_ms": ordered[189]}

    result = {
        "method": "Mock HTTP; local single-writer DuckDB; excludes real network/model latency",
        "without_reins": await measure(call),
    }
    with tempfile.TemporaryDirectory() as temp:
        configure(storage_path=Path(temp) / "overhead.duckdb", mode="observe")

        @trace(task_type="local_overhead")
        async def wrapped():
            await call()
            record_outcome(success=True)

        result["with_reins"] = await measure(wrapped)
        shutdown()
    await client.close()
    result["median_overhead_ms"] = (
        result["with_reins"]["median_ms"] - result["without_reins"]["median_ms"]
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
