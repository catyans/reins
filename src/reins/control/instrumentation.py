"""Bridge SDK dispatch to the shared ledger, independent of worker trace storage."""

from decimal import ROUND_CEILING, Decimal

from reins.control.client import ControlDenied, ControlUnavailable, active_workflow, log
from reins.core.pricing import get_price


def enrich(span):
    from reins.control.client import _manual

    current = active_workflow()
    if current:
        span.metadata.update(
            {
                k: current.context[k]
                for k in ("workflow_id", "task_id", "customer_id", "task_type", "mode")
            }
        )

        if _manual.get():
            span.metadata["mode"] = "observe"


def admit(span, config):
    current = active_workflow()
    if current is None:
        return
    from reins.control.client import _manual

    if _manual.get():
        return
    strict = current.context["mode"] == "enforce"
    if strict and (
        not span.metadata.get("input_bound_verified")
        or not span.metadata.get("output_bound_verified")
        or span.model not in (config.prices if config else {}).get(span.provider, {})
    ):
        raise ControlDenied("Shared enforcement requires verified bounds and pinned prices")

    def reserve(model):
        incoming = span.estimated_input_tokens
        counter = getattr(span, "_bound_counter", None)
        if counter and model != span.model:
            incoming = counter(model)
        cost = get_price(
            span.provider,
            model,
            incoming,
            span.estimated_max_output_tokens,
            config.prices if config else {},
        )
        cost = str(cost.quantize(Decimal("0.000000001"), rounding=ROUND_CEILING))
        return current.client.post(
            "reserve",
            {
                "request_id": span.span_id,
                "task_id": current.context["task_id"],
                "model": span.provider + "/" + model,
                "estimated_cost": cost,
                "max_cost": cost,
                "category": "model",
            },
        ), incoming

    try:
        answer, incoming = reserve(span.model)
        if strict and answer.get("reason") == "shared_budget":
            # Only explicitly approved fallback order; never infer task compatibility.
            approved = current.context["approved_models"]
            identity = span.provider + "/" + span.model
            candidates = approved[approved.index(identity) + 1 :] if identity in approved else []
            for candidate in candidates:
                provider, model = candidate.split("/", 1)
                if provider != span.provider or model not in config.prices.get(provider, {}):
                    continue
                answer, incoming = reserve(model)
                if answer["decision"] == "continue":
                    span.metadata["control_original_model"] = span.model
                    span.model, span.degraded = model, True
                    span.estimated_input_tokens = incoming
                    break
                if answer.get("reason") != "shared_budget":
                    break
    except (ControlUnavailable, ValueError):
        if strict:
            raise
        log.warning("SDK call has no shared ledger observation")
        return
    if answer["decision"] != "continue":
        raise ControlDenied(answer.get("reason", "Shared admission denied"))
    span._control_client = current.client
    span.metadata["control_request_id"] = span.span_id


def settle(span):
    client = getattr(span, "_control_client", None)
    if client is None:
        return
    try:
        if span.metadata.get("cost_status") == "known":
            cost = str(
                Decimal(str(span.cost)).quantize(Decimal("0.000000001"), rounding=ROUND_CEILING)
            )
            client.post(
                "settle",
                {
                    "request_id": span.span_id,
                    "actual_cost": cost,
                    "usage": {"input_tokens": span.tokens_in, "output_tokens": span.tokens_out},
                },
            )
        else:
            client.post("uncertain", {"request_id": span.span_id})
    except (ControlUnavailable, ValueError):
        log.warning("Shared settlement unacknowledged; reservation held: %s", span.span_id)
