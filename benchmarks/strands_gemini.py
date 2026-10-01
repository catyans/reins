"""Pinned Strands reflection methods with Gemini and a bounded file-tool adapter.

Not AgentCore managed-service measurements. The upstream optimization loops,
sampling and templates are retained; Bedrock and unrestricted shell/swarm tools
are replaced explicitly. Every reflector/sub-reflector model call is recorded.
"""

import argparse
import hashlib
import json
import os
import random
import shlex
import threading
import time
from pathlib import Path

from strands import Agent, tool
from strands.models.gemini import GeminiModel
from strands_harness_optimizer.datamodels import Reward, Rollout
from strands_harness_optimizer.formulas import SystemPromptFormula
from strands_harness_optimizer.optimizers.system_prompt.contrastive_reflection import (
    ContrastiveReflectionOptimizer,
)
from strands_harness_optimizer.optimizers.system_prompt.multi_agent import MultiAgentOptimizer
from strands_harness_optimizer.utils.templates import load_builtin_template

COMMIT = "da3dd085e987a2105382f9a883b145b26b06608a"


def main(args):
    random.seed(927)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"{args.workload}-{args.kind}.json"
    if target.exists():
        raise RuntimeError("Refusing to replace existing reflection result")
    protocol = json.loads((Path(args.development) / "protocol.json").read_text())
    records = json.loads(
        (
            Path(args.development) / f"{args.workload}-development-fixed-lite-burst-c4.json"
        ).read_text()
    )["records"]
    dataset = json.loads(Path(args.dataset).read_text())
    cases = {
        c["id"]: c
        for c in dataset["cases"]
        if c["split"] == "development" and c["workload"] == args.workload
    }
    # Read the exact starting prompt from a real development request, not a hand-tuned replacement.
    call = next(
        json.loads(x)
        for x in (Path(args.development) / "calls.jsonl").read_text().splitlines()
        if json.loads(x)["workload"] == args.workload
    )
    original = call["input"].split("\nReturn {request_id:")[0]
    key = os.environ.get("DTA_GOOGLE_GENAI__API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not key:
        for line in Path(args.key_file).read_text().splitlines():
            if line.startswith(("GEMINI_API_KEY=", "DTA_GOOGLE_GENAI__API_KEY=")):
                key = line.split("=", 1)[1].strip().strip("\"'")
    if not key:
        raise RuntimeError("Missing Gemini credentials")
    calls = []
    guard = threading.Lock()

    class MeteredGemini(GeminiModel):
        async def stream(self, messages, tool_specs=None, system_prompt=None, **kw):
            with guard:
                if len(calls) >= 200:
                    raise RuntimeError("Frozen 200-call optimization limit reached")
                row = {
                    "id": len(calls) + 1,
                    "started_at": time.time(),
                    "messages": messages,
                    "system_prompt": system_prompt,
                    "usage": None,
                    "complete": False,
                }
                calls.append(row)
            started = time.perf_counter()
            try:
                async for event in super().stream(messages, tool_specs, system_prompt, **kw):
                    if "metadata" in event:
                        row["usage"] = event["metadata"].get("usage")
                        row["complete"] = row["usage"] is not None
                    yield event
            except Exception as e:
                row["error"] = type(e).__name__
                raise
            finally:
                row["seconds"] = time.perf_counter() - started
                with guard:
                    with (out / f"{args.workload}-{args.kind}-calls.jsonl").open("a") as f:
                        f.write(json.dumps(row, default=str) + "\n")
                        f.flush()
                        os.fsync(f.fileno())

    def make_model():
        return MeteredGemini(
            model_id="gemini-2.5-flash",
            client_args={
                "api_key": key,
                "http_options": {"timeout": 60000, "retry_options": {"attempts": 1}},
            },
            params={
                "temperature": 0,
                "max_output_tokens": 4096,
                "thinking_config": {"thinking_budget": 0},
            },
        )

    base = ContrastiveReflectionOptimizer if args.kind == "single" else MultiAgentOptimizer

    class Adapted(base):
        def _create_agent(self, system_prompt):
            folder = Path(self._temp_dir).resolve()
            optimizer = self

            def safe(path):
                p = Path(path)
                if not p.is_absolute():
                    p = folder / p
                p = p.resolve()
                if not p.is_relative_to(folder):
                    raise ValueError("Only the current trace directory is accessible")
                return p

            @tool
            def shell(command: str) -> str:
                """Read trace files. Supports ls, cat, head and tail only; use write_file to write parameters."""
                try:
                    words = shlex.split(command)
                    if not words:
                        return "Empty command"
                    op = words[0]
                    paths = [w for w in words[1:] if not w.startswith("-") and not w.isdigit()]
                    p = safe(paths[-1] if paths else str(folder))
                    if op == "ls":
                        return "\n".join(
                            f"{x.stat().st_size} {x}"
                            for x in (sorted(p.iterdir()) if p.is_dir() else [p])
                        )
                    if op not in ("cat", "head", "tail"):
                        return "Use ls/cat/head/tail, or write_file(path,content). Shell execution is disabled."
                    text = p.read_text()
                    if op == "head":
                        text = "\n".join(text.splitlines()[:60])
                    if op == "tail":
                        text = "\n".join(text.splitlines()[-60:])
                    return text[:50000]
                except Exception as e:
                    return type(e).__name__ + ": " + str(e)

            @tool
            def write_file(path: str, content: str) -> str:
                """Write proposed parameter text within the current trace directory."""
                p = safe(path)
                p.write_text(content)
                return str(p)

            @tool
            def submit_optimized_params(file_path_dict: dict) -> str:
                """Submit parameter names mapped to text files within the trace directory."""
                proposed = {k: safe(v).read_text().strip() for k, v in file_path_dict.items()}
                prompt = proposed.get("system_prompt", "")
                if not prompt.startswith(original):
                    return "Rejected: preserve the original prompt exactly, then append concise guidance."
                if len(prompt) > len(original) * 1.2:
                    return f"Rejected: maximum total length is {int(len(original) * 1.2)} characters. Tighten the addition and resubmit."
                if any(
                    isinstance(v, str) and len(v) > 20 and v not in original and v in prompt
                    for c in cases.values()
                    for v in c["expected"].values()
                ):
                    return "Rejected: remove literal example answers from the development set."
                optimizer._submitted_params = proposed
                return "Parameters submitted"

            @tool
            def read_traces() -> str:
                """Read all sampled trace JSON files, bounded to 100,000 characters."""
                return "\n".join(p.read_text() for p in sorted(folder.glob("*.json")))[:100000]

            @tool
            def swarm(task: str) -> str:
                """Ask an independent Gemini sub-agent to analyze a specific trace or failure pattern."""
                child = Agent(
                    model=make_model(),
                    tools=[shell, read_traces],
                    system_prompt="Analyze the assigned trace evidence. Return concise findings; do not invent failures. Trace directory: "
                    + str(folder),
                    callback_handler=None,
                    retry_strategy=None,
                )
                return str(child(task))

            tools = [shell, read_traces, write_file, submit_optimized_params]
            if args.kind == "multi":
                tools.append(swarm)
            return Agent(
                model=make_model(),
                tools=tools,
                system_prompt=system_prompt
                + self.system_prompt_suffix
                + "\nTool adaptation: read_traces reads the complete sampled set. Shell is read-only and does not execute pipelines. "
                "Write parameter files with write_file in "
                + str(folder)
                + ". The maximum prompt length is "
                + str(int(len(original) * 1.2))
                + " characters. Preserve the original task and safety constraints. Do not stop until submit_optimized_params succeeds.",
                callback_handler=None,
                retry_strategy=None,
            )

    formula = SystemPromptFormula(system_prompt=original)
    optimizer = Adapted(
        formula,
        system_prompt_template=load_builtin_template("contrastive_reflection/system_prompt.jinja"),
        task_message_template=load_builtin_template(
            "contrastive_reflection/task_message_system_prompt.jinja"
        ),
        n_sample_traces=20,
    )
    optimizer.add_rollouts(
        [
            Rollout(
                data_sample={
                    "input": cases[r["id"]]["input"],
                    "reference": cases[r["id"]]["expected"],
                },
                messages=[{"role": "assistant", "content": [{"text": json.dumps(r["answer"])}]}],
            )
            for r in records
        ]
    )
    optimizer.add_rewards([Reward(reward=float(r["success"])) for r in records])
    result = {
        "upstream_commit": COMMIT,
        "kind": args.kind,
        "workload": args.workload,
        "model": "gemini-2.5-flash",
        "epochs": 1,
        "n_sample_traces": 20,
        "max_optimizer_calls": 200,
        "development_protocol": protocol["dataset_sha256"],
        "adaptations": [
            "Gemini replaces Bedrock for all agents",
            "read-only shell subset + write_file replaces unrestricted shell",
            "Gemini-backed analysis tool replaces default swarm",
            "SDK retries disabled; every model stream metered",
        ],
        "scope": "Adapted public method; not managed AgentCore or exact AWS benchmark reproduction",
    }
    started = time.perf_counter()
    try:
        optimizer.step()
        prompt = formula.get_tunable_params()["system_prompt"]
        result["candidate_prompt"] = prompt
        if len(prompt) > len(original) * 1.2:
            result.update(
                status="guardrail_rejected", reason="prompt growth exceeds 20%", prompt=original
            )
        elif not prompt.startswith(original):
            result.update(
                status="guardrail_rejected",
                reason="original task and source-safety constraints not preserved",
                prompt=original,
            )
        elif any(
            isinstance(v, str) and len(v) > 20 and v not in original and v in prompt
            for c in cases.values()
            for v in c["expected"].values()
        ):
            result.update(
                status="guardrail_rejected",
                reason="candidate copies development-specific answer text",
                prompt=original,
            )
        else:
            result.update(status="complete", prompt=prompt)
    except Exception as e:
        result.update(
            status="failed", error_type=type(e).__name__, error=str(e)[:500], prompt=original
        )
    result["wall_seconds"] = time.perf_counter() - started
    result["model_calls"] = len(calls)
    result["cost_complete"] = bool(calls) and all(
        r["complete"] and not r["usage"].get("cacheReadInputTokens") for r in calls
    )
    result["known_cost"] = sum(
        (
            (r["usage"] or {}).get("inputTokens", 0) * 0.3
            + (r["usage"] or {}).get("outputTokens", 0) * 2.5
        )
        / 1e6
        for r in calls
    )
    result["source_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    target.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {
                k: v
                for k, v in result.items()
                if k not in ("prompt", "candidate_prompt", "adaptations")
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", required=True)
    p.add_argument("--development", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--workload", required=True)
    p.add_argument("--kind", choices=["single", "multi"], required=True)
    p.add_argument("--key-file")
    main(p.parse_args())
