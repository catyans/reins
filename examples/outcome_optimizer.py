"""Reproducible synthetic Data Agent experiment. No keys or network requests.

PYTHONPATH=src python examples/outcome_optimizer.py --database /tmp/optimizer.duckdb
Add --dashboard to keep the SDK dashboard open, --output to export the report.
"""

import argparse
import asyncio
import json
from pathlib import Path

from reins import configure, record_external_cost
from reins.core.decorators import shutdown
from reins.optimization import Constraints, ValidatedCascade, evaluate_experiment


def fixtures(prefix, n):
    return [
        {
            "id": f"{prefix}-{i}",
            "input": {
                "name": f"Supplier {i}",
                "website": {"headquarters": "DE" if i % 4 == 0 else "US", "distributor": "US"},
            },
            "expected": {"name": f"Supplier {i}", "country": "DE" if i % 4 == 0 else "US"},
        }
        for i in range(n)
    ]


async def strong(payload):
    record_external_cost("0.030", label="synthetic strong inference")
    return {"name": payload["name"], "country": payload["website"]["headquarters"]}


async def small(payload):
    record_external_cost("0.004", label="synthetic small inference")
    return {"name": payload["name"], "country": payload["website"]["distributor"]}


def validate_source(payload, answer):
    # Online evidence check: no expected answer or test-case ID is available here.
    record_external_cost("0.001", label="synthetic evidence validation")
    return (
        answer.get("name") == payload["name"]
        and answer.get("country") == payload["website"]["headquarters"]
    )


async def main(args):
    configure(
        storage_path=args.database,
        dashboard=args.dashboard,
        dashboard_port=args.port,
        mode="observe",
    )
    try:
        candidates = {
            "fixed-strong-v1": strong,
            "fixed-small-v1": small,
            "validated-cascade-v1": ValidatedCascade(small, strong, validate_source),
        }
        validation = await evaluate_experiment(
            fixtures("validation", 40),
            candidates,
            baseline="fixed-strong-v1",
            constraints=Constraints(min_success_rate=0.95, min_cases=30),
            task_type="simulated_supplier_extraction",
        )
        # Freeze the selected candidate before evaluating different IDs on test.
        selected = validation["recommendation"]
        test = None
        if selected and selected != "fixed-strong-v1":
            test = await evaluate_experiment(
                fixtures("test", 20),
                {"fixed-strong-v1": strong, selected: candidates[selected]},
                baseline="fixed-strong-v1",
                split="test",
                constraints=Constraints(min_cases=20),
                task_type="simulated_supplier_extraction",
            )
        result = {"synthetic_demo": True, "validation": validation, "test": test}
        if args.output:
            Path(args.output).write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2), flush=True)
        if args.dashboard:
            print(f"Dashboard: http://127.0.0.1:{args.port}", flush=True)
            await asyncio.Event().wait()
    finally:
        shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--output")
    parser.add_argument("--dashboard", action="store_true")
    parser.add_argument("--port", type=int, default=8766)
    try:
        asyncio.run(main(parser.parse_args()))
    except KeyboardInterrupt:
        pass
