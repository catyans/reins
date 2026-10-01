"""Offline, deterministic product demo. All prices/responses are synthetic.

python examples/extraction_pilot.py --database /tmp/reins-demo.duckdb
No API key, browser, customer data, or outbound model request is used.
"""

import argparse
import asyncio
import json
from types import SimpleNamespace

from reins import configure
from reins.core.decorators import _get_runtime, shutdown
from reins.core.outcomes import compare_tasks
from reins.evaluation import evaluate_dataset

CASES = [
    {
        "id": "simple",
        "input": {"page": "Acme | US", "ambiguous": False},
        "expected": {"name": "Acme", "country": "US"},
    },
    {
        "id": "ambiguous",
        "input": {"page": "Acme US distributor; Acme parent company Germany", "ambiguous": True},
        "expected": {"name": "Acme", "country": "DE"},
    },
]


async def main(path):
    configure(
        storage_path=path,
        mode="enforce",
        prices={"openai": {"demo-strong": ["10", "10"], "demo-small": ["1", "1"]}},
        token_counter=lambda provider, model, request: 100,
        task_models={"field_extraction": ["openai/demo-strong", "openai/demo-small"]},
    )
    runtime = _get_runtime()

    async def run_policy(policy, payload):
        # A local fake provider demonstrates the real admission/accounting path.
        async def provider(client, **kwargs):
            small = kwargs["model"] == "demo-small"
            answer = {
                "name": "Acme",
                "country": "US" if small or not payload["ambiguous"] else "DE",
            }
            return SimpleNamespace(
                answer=answer, usage=SimpleNamespace(prompt_tokens=100, completion_tokens=100)
            )

        model = "demo-small" if policy == "fixed-small" else "demo-strong"
        response = await runtime.instrumentor._wrap_async(
            provider,
            None,
            "openai",
            (),
            {
                "model": model,
                "messages": [{"role": "user", "content": payload["page"]}],
                "max_tokens": 100,
            },
        )
        return response.answer

    for policy in ["fixed-strong", "fixed-small", "budget-policy"]:
        # Deliberately simple policy: demonstration will expose quality loss, not hide it.
        await evaluate_dataset(
            CASES,
            lambda payload: run_policy(policy, payload),
            policy_version=policy,
            budget=".0003" if policy == "budget-policy" else ".01",
            repetitions=3,
            on_exceed="degrade",
        )
    print(json.dumps({"synthetic_demo": True, "cohorts": compare_tasks(runtime.storage)}, indent=2))
    shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    asyncio.run(main(parser.parse_args().database))
