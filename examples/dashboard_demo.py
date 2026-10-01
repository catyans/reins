"""Synthetic, offline dashboard demo. No API key or outbound requests."""

import argparse
import asyncio
from types import SimpleNamespace

from reins import configure, record_outcome, record_retry, step, trace
from reins.core.decorators import _get_runtime, shutdown
from reins.evaluation import evaluate_dataset


@trace(agent_name="供应商资料采集", task_type="simulated_collection", policy_version="baseline")
async def collect(fail=False):
    async with step("获取网页", kind="retrieval"):
        await asyncio.sleep(2)
    async with step("提取字段"):
        runtime = _get_runtime()

        async def local_response(*args, **kwargs):
            await asyncio.sleep(3)
            return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=100, completion_tokens=60))

        await runtime.instrumentor._wrap_async(
            local_response, None, "openai", (), {"model": "demo", "max_tokens": 100}
        )
    async with step("校验结果"):
        await asyncio.sleep(1)
        if fail:
            record_retry()
            async with step("重新获取来源", kind="tool"):
                await asyncio.sleep(2)
                raise TimeoutError("synthetic source timeout")
    async with step("保存数据", kind="tool"):
        await asyncio.sleep(1)
    record_outcome(success=True, score=1)


@trace(agent_name="批量采集任务", task_type="simulated_batch", policy_version="baseline")
async def batch():
    results = await asyncio.gather(collect(), collect(True), return_exceptions=True)
    record_outcome(success=not any(isinstance(r, Exception) for r in results), score=0.5)


async def seed_comparison():
    runtime = _get_runtime()
    cases = [
        {"id": "simple", "input": {"complex": False}, "expected": {"country": "US"}},
        {"id": "ambiguous", "input": {"complex": True}, "expected": {"country": "DE"}},
    ]
    for policy in ("fixed-strong", "fixed-small", "budget-policy"):

        async def agent(payload):
            async def provider(client, **kwargs):
                small = kwargs["model"] == "demo-small"
                return SimpleNamespace(
                    answer={"country": "US" if small or not payload["complex"] else "DE"},
                    usage=SimpleNamespace(prompt_tokens=100, completion_tokens=100),
                )

            response = await runtime.instrumentor._wrap_async(
                provider,
                None,
                "openai",
                (),
                {
                    "model": "demo-small" if policy == "fixed-small" else "demo-strong",
                    "max_tokens": 100,
                },
            )
            return response.answer

        await evaluate_dataset(
            cases,
            agent,
            policy_version=policy,
            task_type="simulated_extraction",
            repetitions=3,
            mode="enforce",
            budget=".0003" if policy == "budget-policy" else ".01",
            on_exceed="degrade",
        )


async def main(args):
    configure(
        storage_path=args.database,
        dashboard=True,
        dashboard_port=args.port,
        inactivity_seconds=10,
        prices={
            "openai": {"demo": ["1", "2"], "demo-strong": ["10", "10"], "demo-small": ["1", "1"]}
        },
        token_counter=lambda *args: 100,
        task_models={"simulated_extraction": ["openai/demo-strong", "openai/demo-small"]},
    )
    print("SIMULATED DATA — " + _get_runtime().dashboard_server.url, flush=True)
    try:
        await seed_comparison()
        for _ in range(3):
            await batch()
            await asyncio.sleep(3)
        print("Demo complete; dashboard remains available. Ctrl+C to stop.", flush=True)
        await asyncio.Event().wait()
    finally:
        shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default="/tmp/reins-dashboard-demo.duckdb")
    parser.add_argument("--port", type=int, default=8765)
    asyncio.run(main(parser.parse_args()))
