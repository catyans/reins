"""Small synchronous loopback client; control requests never retry implicitly."""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import hashlib
import json
import logging
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

log = logging.getLogger("reins.control")
_manual = contextvars.ContextVar("reins_manual_admission", default=False)
_active = contextvars.ContextVar("reins_workflow", default=None)


class ControlUnavailable(RuntimeError):
    pass


class ControlDenied(RuntimeError):
    pass


class Client:
    def __init__(self, url="http://127.0.0.1:8795", *, token=None, token_file=None):
        p = urlparse(url)
        if (
            p.scheme != "http"
            or p.hostname not in ("127.0.0.1", "localhost")
            or p.username
            or p.password
            or p.query
            or p.fragment
            or p.path not in ("", "/")
        ):
            raise ValueError("Control URL must be an HTTP loopback origin")
        if token is None and token_file is None:
            raise ValueError("Provide token_file or configure(control_token_file=...)")
        self.url = url.rstrip("/")
        self.token = token if token is not None else Path(token_file).read_text().strip()
        if not self.token or len(self.token) < 32:
            raise ValueError("Invalid control credential")

    def post(self, path, body):
        req = urllib.request.Request(
            self.url + "/v1/" + path,
            data=json.dumps(body, allow_nan=False).encode(),
            headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"},
        )
        try:
            # Do not consult proxy environment variables for local credentials.
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                req, timeout=5
            ) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 400:
                raise ValueError(json.load(exc).get("error", "Invalid control request")) from None
            raise ControlUnavailable("Control request rejected or failed") from None
        except (OSError, ValueError) as exc:
            raise ControlUnavailable("Local control service unavailable") from exc


class Workflow:
    def __init__(self, client, context):
        self.client, self.context = client, context

    def export_context(self):
        # No service credential is sent through task queues.
        return dict(self.context)

    @contextlib.contextmanager
    def task(self, task_id=None, *, budget=None):
        result = self.client.post(
            "tasks",
            {
                "parent_task_id": self.context["task_id"],
                "task_id": task_id or str(uuid4()),
                "budget": budget,
            },
        )
        if result.pop("decision") != "continue":
            raise ControlDenied(result.get("reason", "Branch denied"))
        with _bind(self.client, result) as task:
            yield task

    def progress(self, stage, completed, total):
        return self.client.post(
            "progress",
            {
                "task_id": self.context["task_id"],
                "stage": stage,
                "completed": completed,
                "total": total,
            },
        )

    def finish(self, *, accepted):
        if self.context["task_id"] != self.context["workflow_id"]:
            raise ValueError("Only the root workflow records business acceptance")
        return self.client.post(
            "transition",
            {"workflow_id": self.context["workflow_id"], "action": "finish", "accepted": accepted},
        )

    def call(
        self,
        execute,
        *,
        model,
        max_cost,
        estimated_cost=None,
        category="tool",
        read_only=False,
        reuse_inputs=None,
        validator=None,
        retry=False,
        usage=None,
        operation_inputs=None,
        phase="work",
        tool_name=None,
        tool_arguments=None,
        approval_id=None,
        validation_name=None,
    ):
        """execute -> (JSON result, actual USD cost). Paid tools must also use admission.

        Cached output requires explicit validation and exact inputs. A crash after
        dispatch remains uncertain; no paid action is automatically retried.
        """
        key = None
        if reuse_inputs is not None:
            if not read_only or validator is None:
                raise ValueError("Reuse requires read-only execution and a validator")
            key = hashlib.sha256(
                json.dumps(
                    [model, category, reuse_inputs], sort_keys=True, allow_nan=False
                ).encode()
            ).hexdigest()
        rid = str(uuid4())
        try:
            answer = self.client.post(
                "reserve",
                {
                    "task_id": self.context["task_id"],
                    "request_id": rid,
                    "model": model,
                    "category": category,
                    "max_cost": max_cost,
                    "estimated_cost": estimated_cost if estimated_cost is not None else max_cost,
                    "cache_key": key,
                    "read_only": read_only,
                    "retry": retry,
                    "phase": phase,
                    "operation_key": hashlib.sha256(
                        json.dumps(
                            [model, category, operation_inputs], sort_keys=True, allow_nan=False
                        ).encode()
                    ).hexdigest()
                    if operation_inputs is not None
                    else None,
                    "tool_name": tool_name,
                    "tool_arguments": tool_arguments,
                    "approval_id": approval_id,
                },
            )
        except ControlUnavailable:
            if self.context["mode"] == "enforce":
                raise
            log.warning("Control observation missing; executing without a central reservation")
            return execute()[0]
        if answer["decision"] == "reuse":
            return answer["result"]
        if answer["decision"] != "continue":
            raise ControlDenied(answer.get("reason", "Operation held"))
        try:
            token = _manual.set(True)
            try:
                result, cost = execute()
            finally:
                _manual.reset(token)
        except BaseException:
            with contextlib.suppress(ControlUnavailable):
                self.client.post("uncertain", {"request_id": rid})
            raise
        # Settlement is independent of business validation: a wrong result still costs money.
        valid = False
        validation_error = None
        try:
            valid = bool(validator(result)) if validator else False
        except Exception as exc:
            validation_error = exc
        try:
            self.client.post(
                "settle",
                {
                    "request_id": rid,
                    "actual_cost": cost,
                    "result": result,
                    "validated": valid,
                    "success": valid if validator else True,
                    "validation_name": validation_name,
                    "usage": usage or {},
                },
            )
        except ControlUnavailable:
            log.warning("Settlement not acknowledged; reservation remains held: %s", rid)
        if validation_error:
            raise validation_error
        return result

    async def acall(
        self,
        execute,
        *,
        model,
        max_cost,
        estimated_cost=None,
        category="tool",
        read_only=False,
        reuse_inputs=None,
        validator=None,
        retry=False,
        usage=None,
        operation_inputs=None,
        phase="work",
        tool_name=None,
        tool_arguments=None,
        approval_id=None,
        validation_name=None,
    ):
        """execute -> (JSON result, actual USD cost). Paid tools must also use admission.

        Cached output requires explicit validation and exact inputs. A crash after
        dispatch remains uncertain; no paid action is automatically retried.
        """
        key = None
        if reuse_inputs is not None:
            if not read_only or validator is None:
                raise ValueError("Reuse requires read-only execution and a validator")
            key = hashlib.sha256(
                json.dumps(
                    [model, category, reuse_inputs], sort_keys=True, allow_nan=False
                ).encode()
            ).hexdigest()
        rid = str(uuid4())
        try:
            answer = await asyncio.to_thread(
                self.client.post,
                "reserve",
                {
                    "task_id": self.context["task_id"],
                    "request_id": rid,
                    "model": model,
                    "category": category,
                    "max_cost": max_cost,
                    "estimated_cost": estimated_cost if estimated_cost is not None else max_cost,
                    "cache_key": key,
                    "read_only": read_only,
                    "retry": retry,
                    "phase": phase,
                    "operation_key": hashlib.sha256(
                        json.dumps(
                            [model, category, operation_inputs], sort_keys=True, allow_nan=False
                        ).encode()
                    ).hexdigest()
                    if operation_inputs is not None
                    else None,
                    "tool_name": tool_name,
                    "tool_arguments": tool_arguments,
                    "approval_id": approval_id,
                },
            )
        except ControlUnavailable:
            if self.context["mode"] == "enforce":
                raise
            log.warning("Control observation missing; executing without a central reservation")
            return (await execute())[0]
        if answer["decision"] == "reuse":
            return answer["result"]
        if answer["decision"] != "continue":
            raise ControlDenied(answer.get("reason", "Operation held"))
        try:
            token = _manual.set(True)
            try:
                result, cost = await execute()
            finally:
                _manual.reset(token)
        except BaseException:
            with contextlib.suppress(ControlUnavailable):
                await asyncio.to_thread(self.client.post, "uncertain", {"request_id": rid})
            raise
        # Settlement is independent of business validation: a wrong result still costs money.
        valid = False
        validation_error = None
        try:
            valid = bool(validator(result)) if validator else False
        except Exception as exc:
            validation_error = exc
        try:
            await asyncio.to_thread(
                self.client.post,
                "settle",
                {
                    "request_id": rid,
                    "actual_cost": cost,
                    "result": result,
                    "validated": valid,
                    "success": valid if validator else True,
                    "validation_name": validation_name,
                    "usage": usage or {},
                },
            )
        except ControlUnavailable:
            log.warning("Settlement not acknowledged; reservation remains held: %s", rid)
        if validation_error:
            raise validation_error
        return result

    def budget_state(self):
        return self.client.post("status", {"workflow_id": self.context["workflow_id"]})[
            "workflows"
        ][0]["budget_state"]

    def wrapup(self, reason="Preserve partial results within remaining budget"):
        return self.client.post(
            "transition",
            {"workflow_id": self.context["workflow_id"], "action": "wrapup", "reason": reason},
        )

    def write_state(self, name, payload, *, expected_version, source, ttl_seconds=3600):
        return self.client.post(
            "state/write",
            {
                "task_id": self.context["task_id"],
                "name": name,
                "payload": payload,
                "expected_version": expected_version,
                "source": source,
                "ttl_seconds": ttl_seconds,
            },
        )

    def read_state(self, name):
        return self.client.post("state/read", {"task_id": self.context["task_id"], "name": name})

    def handoff(
        self, receiver_task_id, *, goal, inputs, completed, state_name, version, handoff_id=None
    ):
        return self.client.post(
            "handoff",
            {
                "task_id": self.context["task_id"],
                "receiver_task_id": receiver_task_id,
                "handoff_id": handoff_id or str(uuid4()),
                "payload": {"goal": goal, "inputs": inputs, "completed": completed},
                "state_name": state_name,
                "version": version,
            },
        )

    def call_tool(
        self,
        name,
        execute,
        arguments,
        *,
        max_cost,
        approval_id=None,
        read_only=False,
        validator=None,
    ):
        return self.call(
            lambda: execute(**arguments),
            model="tool/" + name,
            max_cost=max_cost,
            category="tool",
            tool_name=name,
            tool_arguments=arguments,
            operation_inputs=arguments,
            approval_id=approval_id,
            read_only=read_only,
            validator=validator,
        )


@contextlib.contextmanager
def _bind(client, context):
    value = Workflow(client, context)
    token = _active.set(value)
    try:
        yield value
    finally:
        _active.reset(token)


@contextlib.contextmanager
def workflow(
    *,
    customer_id,
    task_type,
    budget,
    workflow_id=None,
    policy_version="baseline",
    mode=None,
    client=None,
    **limits,
):
    if client is None:
        from reins.core.decorators import _get_runtime

        cfg = _get_runtime().config
        client = Client(cfg.control_url, token_file=cfg.control_token_file)
        mode = mode or cfg.mode
    mode = mode or "observe"
    body = {
        "workflow_id": workflow_id or str(uuid4()),
        "customer_id": customer_id,
        "task_type": task_type,
        "budget": budget,
        "policy_version": policy_version,
        "mode": mode,
        **limits,
    }
    try:
        context = client.post("workflows", body)
    except ControlUnavailable:
        if mode == "enforce":
            raise
        log.warning("Workflow observation missing; continuing in observe mode")
        context = {
            **body,
            "task_id": body["workflow_id"],
            "approved_models": body.get("approved_models", []),
        }
    with _bind(client, context) as value:
        yield value
    # Exiting Python scope is not proof of business success. Explicit finish required.


@contextlib.contextmanager
def bind_context(context, *, client=None):
    if client is None:
        from reins.core.decorators import _get_runtime

        cfg = _get_runtime().config
        client = Client(cfg.control_url, token_file=cfg.control_token_file)
    verified = client.post("context", {"task_id": context["task_id"]})
    if any(context.get(k) != verified[k] for k in ("workflow_id", "customer_id", "mode")):
        raise ValueError("Handoff context does not match registered ownership")
    with _bind(client, verified) as value:
        yield value


def active_workflow():
    return _active.get()


def export_context():
    current = active_workflow()
    if not current:
        raise ValueError("No active workflow")
    return current.export_context()


def report_progress(stage, completed, total):
    current = active_workflow()
    if not current:
        raise ValueError("No active workflow")
    return current.progress(stage, completed, total)
