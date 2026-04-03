"""Basic example: trace an agent with budget control.

Usage:
    pip install reins[budget]
    export ANTHROPIC_API_KEY=your-key
    python examples/basic_trace.py
"""

import asyncio

import anthropic

from reins import trace


@trace(budget="$0.50", on_exceed="degrade", agent_name="demo_agent")
async def my_agent(task: str) -> str:
    client = anthropic.AsyncAnthropic()
    response = await client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=200,
        messages=[{"role": "user", "content": task}],
    )
    return response.content[0].text


async def main():
    result = await my_agent("What is 2+2? Answer in one word.")
    print(f"Result: {result}")
    print()
    print("Run 'reins report' to see cost breakdown.")


if __name__ == "__main__":
    asyncio.run(main())
