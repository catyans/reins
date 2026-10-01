"""Local deterministic demo; no provider calls and no measured savings claims.

First: reins control serve
Then: python examples/runtime_control.py
"""

from pathlib import Path
from uuid import uuid4

from reins.control import Client, ControlDenied, workflow

client = Client(token_file=Path.home() / ".reins/control.token")
with workflow(
    client=client,
    customer_id="demo",
    task_type="data-collection",
    workflow_id=str(uuid4()),
    budget=".10",
    mode="enforce",
) as run:
    for i in range(3):
        with run.task() as task:
            try:
                result = task.call(
                    lambda: ({"source": "simulated", "accepted": True}, ".04"),
                    model="demo/source-fetch",
                    max_cost=".04",
                )
                print("Completed simulated task", i + 1, result)
            except ControlDenied as exc:
                print("Stopped before the next paid operation:", exc)
    run.finish(accepted=False)
    print("Inspect this workflow in the dashboard:", run.context["workflow_id"])
