"""One controlled crash using a real Gemini response, isolated from release benchmarks."""

import argparse
import asyncio
import json
import time
from pathlib import Path

from reins import configure
from reins.core.decorators import shutdown
from reins.projects.documents import atomic_json
from reins.projects.runtime import Gemini, Runner, State, load_key


async def main(root, key_file):
    root = Path(root)
    artifact = root / "recovery-report.json"
    if artifact.exists():
        print("Recovery experiment already completed; not making new requests.")
        return
    dataset = json.loads((root / "dataset.json").read_text())
    project = next(p for p in dataset["projects"] if p["project"] == "joblib/joblib")
    project = {**project, "snapshots": [project["snapshots"][-1]] * 3}
    small = {"dataset_hash": dataset["dataset_hash"] + ":recovery", "projects": [project]}
    key = load_key(key_file)
    timings, counts = [], []
    for inject in (True, False):
        state = State(root / "recovery")
        configure(storage_path=str(root / "recovery" / "traces.duckdb"))
        caller = Gemini(state, key, limit=3)
        original = caller.call
        if inject:

            async def crash_after_paid_response(*args, **kwargs):
                await original(*args, **kwargs)
                raise asyncio.CancelledError(
                    "Controlled interruption after durable provider response"
                )

            caller.call = crash_after_paid_response
        runner = Runner(state, caller, root / "recovery" / "public-status.json")
        started = time.perf_counter()
        try:
            try:
                await runner.run(small, policies=("incremental",))
            except asyncio.CancelledError:
                if not inject:
                    raise
            counts.append(
                dict(
                    state.db.execute(
                        "SELECT count(*) calls,coalesce(sum(cost),0) estimated_cost FROM calls"
                    ).fetchone()
                )
            )
            if not inject:
                complete = state.db.execute(
                    "SELECT count(*) FROM jobs WHERE state='completed'"
                ).fetchone()[0]
                duplicate = state.db.execute(
                    "SELECT count(*) FROM (SELECT request_key FROM calls GROUP BY request_key "
                    "HAVING count(*)>1)"
                ).fetchone()[0]
                first = state.db.execute(
                    "SELECT id,state,cost FROM calls ORDER BY id LIMIT 1"
                ).fetchone()
                assert complete == 4 and duplicate == 0 and first["state"] == "completed"
        finally:
            timings.append(time.perf_counter() - started)
            runner.close()
            state.close()
            shutdown()
    atomic_json(
        artifact,
        {
            "kind": "controlled fault injection with real Gemini response",
            "project": project["project"],
            "version": project["snapshots"][0]["version"],
            "interruption": "after first durable paid response, before checkpoint completion",
            "before_restart": counts[0],
            "after_resume": counts[1],
            "completed_jobs": complete,
            "duplicate_request_keys": duplicate,
            "interrupted_request_reused": True,
            "phase_seconds": timings,
            "note": "Remaining new calls finish other fields; this is not a production outage.",
        },
    )
    print(json.dumps(json.loads(artifact.read_text()), indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="output/benchmarks/projects-v2")
    parser.add_argument("--key-file", required=True)
    args = parser.parse_args()
    asyncio.run(main(args.output, args.key_file))
