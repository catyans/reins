"""CrewAI adapter.

Usage:
    from reins.adapters.crewai import reins_step_callback, reins_task_callback

    # On individual agents
    agent = Agent(
        role="researcher",
        step_callback=reins_step_callback,
    )

    # On tasks
    task = Task(
        description="...",
        callback=reins_task_callback,
    )

    # Or use the helper to instrument a whole Crew
    from reins.adapters.crewai import instrument_crew
    crew = Crew(agents=[...], tasks=[...])
    instrument_crew(crew)
"""

from __future__ import annotations

import logging
from typing import Any

from reins.adapters.base import finalize_span, process_span
from reins.core.models import SpanData, _utcnow

logger = logging.getLogger("reins.adapters.crewai")

# Track active spans per agent
_active_spans: dict[str, SpanData] = {}


def reins_step_callback(step_output: Any) -> None:
    """CrewAI step_callback: called after each agent step (LLM call).

    Args:
        step_output: CrewAI AgentAction or AgentFinish object
    """
    agent_name = "crewai"
    tool_name = None
    span_type = "llm"

    # AgentAction has tool + tool_input
    if hasattr(step_output, "tool"):
        span_type = "tool"
        tool_name = step_output.tool
        agent_name = getattr(step_output, "agent", "crewai")
    elif hasattr(step_output, "return_values"):
        # AgentFinish
        agent_name = getattr(step_output, "agent", "crewai")

    # Extract from step output
    text = ""
    if hasattr(step_output, "text"):
        text = step_output.text or ""
    elif hasattr(step_output, "log"):
        text = step_output.log or ""

    span = SpanData(
        span_type=span_type,
        name=f"crewai.step.{tool_name or 'llm'}",
        tool_name=tool_name,
    )
    span.metadata["agent_name"] = str(agent_name)

    # Estimate tokens from output text
    if text:
        span.tokens_out = max(len(text) // 4, 10)

    span.ended_at = _utcnow()
    span.duration_ms = 0  # CrewAI doesn't expose per-step timing
    if tool_name:
        span.tool_status = "success"

    try:
        span = process_span(span)
    except Exception:
        logger.debug("Budget check failed in CrewAI step", exc_info=True)

    finalize_span(span)


def reins_task_callback(task_output: Any) -> None:
    """CrewAI task callback: called when a task completes.

    Args:
        task_output: CrewAI TaskOutput object
    """
    description = ""
    agent_name = "crewai"

    if hasattr(task_output, "description"):
        description = task_output.description or ""
    if hasattr(task_output, "agent"):
        agent_name = str(task_output.agent)

    raw = getattr(task_output, "raw", "") or ""

    span = SpanData(
        span_type="custom",
        name=f"crewai.task.{description[:40]}",
    )
    span.metadata["agent_name"] = agent_name
    span.ended_at = _utcnow()
    span.duration_ms = 0

    if raw:
        span.tokens_out = max(len(raw) // 4, 10)

    finalize_span(span)


def instrument_crew(crew: Any, agent_name: str = "crewai") -> None:
    """Instrument a CrewAI Crew object with Reins callbacks.

    Adds step_callback and task_callback to all agents and tasks.

    Usage:
        crew = Crew(agents=[...], tasks=[...])
        instrument_crew(crew)
        result = crew.kickoff()
    """
    # Instrument agents
    agents = getattr(crew, "agents", [])
    for agent in agents:
        existing = getattr(agent, "step_callback", None)
        if existing:
            # Chain callbacks
            original = existing

            def chained_step(output, _orig=original):
                reins_step_callback(output)
                return _orig(output)

            agent.step_callback = chained_step
        else:
            agent.step_callback = reins_step_callback

    # Instrument tasks
    tasks = getattr(crew, "tasks", [])
    for task in tasks:
        existing = getattr(task, "callback", None)
        if existing:
            original = existing

            def chained_task(output, _orig=original):
                reins_task_callback(output)
                return _orig(output)

            task.callback = chained_task
        else:
            task.callback = reins_task_callback

    logger.info("Instrumented CrewAI crew: %d agents, %d tasks", len(agents), len(tasks))
