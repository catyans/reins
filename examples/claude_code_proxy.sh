#!/bin/bash
# Example: Control Claude Code costs via Reins proxy
#
# 1. Start the proxy
# 2. Point Claude Code at it
# 3. Use Claude Code normally
# 4. Check costs with reins report

echo "Starting Reins proxy..."
echo "Budget: \$5.00/day | On exceed: degrade (sonnet → haiku)"
echo ""
echo "In another terminal, run:"
echo "  export ANTHROPIC_BASE_URL=http://localhost:8082"
echo "  claude 'your prompt here'"
echo ""

reins proxy --port 8082 --budget '$5/day' --on-exceed degrade
