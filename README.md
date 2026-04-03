# Reins

**Take control of your AI agents** — debuggable, affordable, reliable.

```python
pip install reins

from reins import trace

@trace(budget="$0.50", on_exceed="degrade")
async def my_agent(task: str):
    response = await client.messages.create(model="claude-sonnet-4-20250514", ...)
    return response
```

One line of code. Budget control + cost tracking + auto-degradation.

## Quick Start

```bash
pip install reins[budget]
```

See [docs/PRD.md](docs/PRD.md) for full documentation.
