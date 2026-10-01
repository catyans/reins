"""Real Gemini calls through the official OpenAI compatibility API.

Uses constructed invoices and list-price cost estimates, never claims customer ROI.
No secret is written to artifacts. Run with PYTHONPATH=src and enter key at prompt.
"""

import argparse
import asyncio
import getpass
import hashlib
import json
import os
import time
from pathlib import Path

from google_workload import PROMPT, make_cases, risky, validate_evidence
from openai import AsyncOpenAI

from reins import configure
from reins.core.context import get_current_run
from reins.core.decorators import _get_runtime, shutdown
from reins.optimization import Constraints, evaluate_experiment

PRICES = {"gemini-2.5-flash": ["0.30", "2.50"], "gemini-2.5-flash-lite": ["0.10", "0.40"]}


async def main(args):
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    cases = {s: make_cases(s, n) for s, n in [("validation", args.validation), ("test", args.test)]}
    protocol = {
        "corpus": "constructed invoices; no customer data",
        "split_sizes": {s: len(c) for s, c in cases.items()},
        "prices_per_million": PRICES,
        "pricing_source": "https://ai.google.dev/gemini-api/docs/pricing",
        "price_date": "2026-09-29",
        "temperature": 0,
        "max_output_tokens": 512,
        "thinking_budget": 0,
        "retries": 0,
        "constraints": {"min_success_rate": 0.95, "max_quality_drop": 0},
        "cost_basis": "API usage multiplied by standard paid list prices; not a billing invoice",
        "total_spend_limit_usd": args.limit,
        "policy_names": [
            "fixed-flash-v1",
            "fixed-lite-v1",
            "input-rule-v1",
            "validated-cascade-v1",
        ],
    }
    (out / "protocol.json").write_text(json.dumps(protocol, indent=2) + "\n")
    (out / "cases.json").write_text(json.dumps(cases, indent=2) + "\n")
    (out / "source.sha256").write_text(
        hashlib.sha256(Path(__file__).read_bytes()).hexdigest() + "\n"
    )
    key = os.environ.get("DTA_GOOGLE_GENAI__API_KEY") or getpass.getpass(
        "Google GenAI API key (hidden): "
    )
    client = AsyncOpenAI(
        api_key=key,
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        max_retries=0,
        timeout=40,
    )
    configure(
        storage_path=out / "runs.duckdb",
        mode="observe",
        prices={"google": PRICES},
        dashboard=True,
        dashboard_port=args.port,
    )
    calls = []
    attempts = 0
    reserve_spent = 0.0
    failures = 0

    async def agent(model, payload):
        nonlocal attempts, reserve_spent, failures
        text = PROMPT + payload["source"]
        # Conservative preflight envelope for these short text-only fixtures.
        # Reserve worst-case output and byte-length input; never release on errors.
        rate_in, rate_out = map(float, PRICES[model])
        bound = ((len(text.encode()) + 1024) * rate_in + 512 * rate_out) / 1e6
        if reserve_spent + bound > args.limit or failures >= 3:
            raise RuntimeError("Benchmark spend/error guard stopped further API calls")
        reserve_spent += bound
        attempts += 1
        run = get_current_run()
        start = time.perf_counter()
        record = {
            "case_id": run.metadata["case_id"],
            "policy": run.metadata["policy_version"],
            "split": run.metadata["split"],
            "run_id": run.run_id,
            "model": model,
        }
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": text}],
                temperature=0,
                max_tokens=512,
                response_format={"type": "json_object"},
                extra_body={"extra_body": {"google": {"thinking_config": {"thinking_budget": 0}}}},
            )
            record.update(
                usage=response.usage.model_dump() if response.usage else None,
                answer=response.choices[0].message.content,
                finish_reason=response.choices[0].finish_reason,
            )
            answer = json.loads(response.choices[0].message.content or "null")
            return answer
        except Exception as e:
            record["error"] = type(e).__name__
            # Three provider/transport errors stop the paid experiment; no quota bypass.
            if not isinstance(e, json.JSONDecodeError):
                failures += 1
            raise
        finally:
            record["seconds"] = time.perf_counter() - start
            calls.append(record)
            with (out / "calls.jsonl").open("a") as f:
                f.write(json.dumps(record) + "\n")
            if attempts % 10 == 0:
                print(
                    json.dumps(
                        {
                            "calls": attempts,
                            "reserved_ceiling": round(reserve_spent, 4),
                            "errors": failures,
                        }
                    ),
                    flush=True,
                )

    async def strong(payload):
        return await agent("gemini-2.5-flash", payload)

    async def small(payload):
        return await agent("gemini-2.5-flash-lite", payload)

    async def rules(payload):
        return await (strong(payload) if risky(payload) else small(payload))

    async def cascade(payload):
        answer = await small(payload)
        accepted = validate_evidence(payload, answer)
        run = get_current_run()
        _get_runtime().monitor.event(
            run.run_id,
            "policy_validation",
            payload={"policy": "validated-cascade-v1", "stage": "primary", "accepted": accepted},
        )
        return answer if accepted else await strong(payload)

    # Freeze all policies and their gate before collecting any results.
    candidates = {
        "fixed-flash-v1": strong,
        "fixed-lite-v1": small,
        "input-rule-v1": rules,
        "validated-cascade-v1": cascade,
    }
    reports = {}
    try:
        for split in ["validation", "test"]:
            reports[split] = await evaluate_experiment(
                cases[split],
                candidates,
                baseline="fixed-flash-v1",
                split=split,
                task_type="constructed_invoice_extraction",
                constraints=Constraints(min_cases=min(30, len(cases[split]))),
            )
            (out / f"{split}.json").write_text(json.dumps(reports[split], indent=2) + "\n")
            print(
                json.dumps(
                    {
                        "split": split,
                        "status": reports[split]["status"],
                        "selected": reports[split]["recommendation"],
                    }
                ),
                flush=True,
            )
            if failures >= 3:
                break
        (out / "summary.json").write_text(
            json.dumps(
                {
                    "reports": reports,
                    "calls": len(calls),
                    "reserve_ceiling": reserve_spent,
                    "provider_errors": failures,
                },
                indent=2,
            )
            + "\n"
        )
    finally:
        await client.close()
        shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--validation", type=int, default=40)
    parser.add_argument("--test", type=int, default=60)
    parser.add_argument("--limit", type=float, default=2)
    parser.add_argument("--port", type=int, default=8767)
    asyncio.run(main(parser.parse_args()))
