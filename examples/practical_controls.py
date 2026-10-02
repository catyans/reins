"""Local, deterministic runtime-control demo. No provider calls or real charges.

PYTHONPATH=src python examples/practical_controls.py --serve
"""

import argparse
import secrets
import threading
from pathlib import Path

from reins.control import Client, ControlDenied, workflow
from reins.control.ledger import Ledger
from reins.control.regression import evaluate_suite, write_report
from reins.control.server import ControlServer


def seed(client):
    client.post(
        "pools",
        dict(
            pool_id="demo-month",
            customer_id="Demo customer",
            team_id="Research",
            budget="2",
            critical_reserve=".4",
        ),
    )
    client.post("tools", dict(name="fetch_source", fields={"url": "string"}, read_only=True))
    client.post("tools", dict(name="publish", fields={"record": "string"}, read_only=False))
    with workflow(
        client=client,
        workflow_id="demo-collection",
        customer_id="Demo customer",
        task_type="Simulated source collection",
        team_id="Research",
        pool_id="demo-month",
        budget="1",
        mode="enforce",
        wrapup_reserve=".1",
        repeat_limit=3,
        failure_limit=3,
        max_depth=2,
        max_tools=30,
        allowed_tools=["fetch_source"],
    ) as run:
        with run.task("demo-extract") as child:
            child.call(
                lambda: ({"name": "Public project", "verified": True}, ".06"),
                model="tool/fetch_source",
                category="tool",
                max_cost=".1",
                tool_name="fetch_source",
                tool_arguments={"url": "https://example.org"},
                read_only=True,
                validator=lambda r: r["verified"],
                validation_name="source_verified",
            )
        run.write_state("sources", {"accepted": 1}, expected_version=0, source="demo-fetch")
        for revision, components, total in [
            ("v1", {"system": 500, "tool_schema": 900, "user": 100}, 1520),
            ("v2", {"system": 200, "tool_schema": 150, "user": 100}, 470),
        ]:
            client.post(
                "context/sample",
                dict(
                    task_id=run.context["task_id"],
                    sample_id=revision,
                    revision=revision,
                    components=components,
                    provider_input_tokens=total,
                ),
            )
        run.finish(accepted=True)
    for entry in [
        dict(entry_id="review", kind="human_review", hours=".02", hourly_rate="20"),
        dict(entry_id="revenue", kind="revenue", amount="2"),
        dict(entry_id="coverage", kind="coverage_complete"),
    ]:
        client.post(
            "economics", dict(workflow_id="demo-collection", reference="Simulated amounts", **entry)
        )
    with workflow(
        client=client,
        workflow_id="demo-loop",
        customer_id="Demo customer",
        task_type="Simulated repeated search",
        team_id="Research",
        pool_id="demo-month",
        budget=".6",
        mode="enforce",
        repeat_limit=3,
        wrapup_reserve=".06",
    ) as run:
        for _ in range(4):
            try:
                run.call(
                    lambda: ({"result": "same"}, ".04"),
                    model="google/demo",
                    max_cost=".1",
                    operation_inputs={"query": "same query"},
                )
            except ControlDenied:
                break
    with workflow(
        client=client,
        workflow_id="demo-critical",
        customer_id="Demo customer",
        task_type="Simulated critical review",
        team_id="Research",
        pool_id="demo-month",
        priority="critical",
        budget=".4",
        mode="enforce",
        allowed_tools=["publish"],
    ):
        pass
    case = client.post("regression/case", {"workflow_id": "demo-collection"})
    return evaluate_suite(
        [case],
        dict(
            required_tools=["fetch_source"], required_validations=["source_verified"], max_cost=".1"
        ),
        expected_case_ids=[case["case_id"]],
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="output/practical-demo")
    parser.add_argument("--port", type=int, default=8796)
    parser.add_argument("--serve", action="store_true")
    args = parser.parse_args()
    root = Path(args.output)
    root.mkdir(parents=True, exist_ok=False)
    token, worker = secrets.token_urlsafe(48), secrets.token_urlsafe(48)
    for name, value in [("operator.token", token), ("worker.token", worker)]:
        path = root / name
        path.touch(mode=0o600)
        path.write_text(value)
    ledger = Ledger(root / "control.sqlite")
    server = ControlServer(ledger, token, args.port, worker_token=worker)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = Client(f"http://127.0.0.1:{server.server_port}", token=token)
        write_report(seed(client), root / "regression")
        print(f"Simulation only · http://127.0.0.1:{server.server_port}", flush=True)
        print(f"Operator credential file: {root / 'operator.token'}", flush=True)
        if args.serve:
            thread.join()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
        ledger.close()


if __name__ == "__main__":
    main()
