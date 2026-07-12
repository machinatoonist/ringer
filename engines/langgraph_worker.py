#!/usr/bin/env python3
"""Ringer engine: a local LangGraph worker driving an Ollama-served model.

This is the framework-lane counterpart to the OpenCode lane. Where OpenCode is
a full agentic harness we treat the local model as a provider for, THIS worker
is a small, self-contained LangGraph agent we own end to end — the "how would I
wire LangChain/LangGraph as a Ringer worker" example.

Contract with ringer (see build_worker_command / _run_worker in ringer.py):
  - Invoked as:  <python> langgraph_worker.py --model <slug> --taskdir <dir>
                 [--mode direct|react] [engine_args...] <SPEC>
    The SPEC is always the LAST argument.
  - cwd is already the task dir; stdin is closed; stdout+stderr -> worker.log.
  - Deliverables must be written INSIDE the task dir. We resolve every path and
    reject escapes (same discipline as engines/mock_worker.py), because a raw
    Python engine has no OS sandbox of its own — writes are confined in code.
  - Exit 0 on success; non-zero on failure. The executed CHECK in the manifest
    is what actually verifies the deliverable — this worker just produces it.
  - We print two machine-readable lines the engine config parses:
        MODEL_REPORT: <model slug>      -> model_report_regex (scoreboard attr)
        TOKENS_USED: <int>              -> token_regex          (token accounting)

Scaling story: the model is a CLI flag (--model, filled from the manifest's
"model" field). On a bigger machine, point it at a larger Ollama model
(e.g. ollama's qwen2.5-coder:14b) or any OpenAI-compatible endpoint via
OLLAMA_HOST — nothing else changes. That is the "seamless switchover".
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Annotated, TypedDict

# LangGraph / LangChain are imported lazily inside main() so that --help and
# argument errors do not require the (heavy) deps to be installed.


DEFAULT_OUTPUT = "answer.md"


def resolve_in_taskdir(raw_path: str, taskdir: Path) -> Path:
    """Resolve raw_path under taskdir, rejecting absolute paths and escapes."""
    rel = Path(raw_path)
    if rel.is_absolute():
        raise ValueError(f"output path must be relative to the task dir: {raw_path}")
    root = taskdir.resolve()
    target = (root / rel).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"output path escapes the task dir: {raw_path}")
    return target


def build_messages(spec: str, output_file: str) -> list:
    """Frame the task for a SMALL local model.

    Small models drown when role/boundary framing and the actual ask are mashed
    into one long user turn (observed: llama3.2:1b answered "France" to "capital
    of France?" when the spec was verbose). So we put the framing in a short
    SYSTEM message and pass the task as a clean USER message — the ask stays last
    and uncluttered, which is what 1-3B models handle reliably.
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    system = (
        f"You are a worker that outputs only the exact content for the file "
        f"`{output_file}`. Output the content and nothing else — no preamble, no "
        f"explanation, no markdown code fences, no quotation marks."
    )
    return [SystemMessage(content=system), HumanMessage(content=spec)]


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ringer LangGraph local worker")
    p.add_argument("--model", required=True, help="Ollama model slug, e.g. llama3.2:1b")
    p.add_argument("--taskdir", required=True, help="task working directory (== cwd)")
    p.add_argument(
        "--mode",
        choices=("direct", "react"),
        default="direct",
        help="direct: generate->persist StateGraph (robust on tiny models). "
        "react: tool-using ReAct agent (needs a tool-capable model).",
    )
    p.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help=f"deliverable filename inside the task dir (default: {DEFAULT_OUTPUT})",
    )
    p.add_argument(
        "--temperature", type=float, default=0.0, help="sampling temperature"
    )
    p.add_argument("spec", help="the task spec (passed last by ringer)")
    return p.parse_args(argv)


def run_direct(model: str, temperature: float, spec: str, output_path: Path, output_name: str) -> int:
    """A minimal LangGraph StateGraph: generate -> persist.

    Robust on small models because the model only has to emit content — the
    graph, not the model, is responsible for writing the file.
    """
    from langchain_ollama import ChatOllama
    from langgraph.graph import END, START, StateGraph

    class State(TypedDict):
        spec: str
        content: str
        tokens: int

    llm = ChatOllama(model=model, temperature=temperature)

    def generate(state: State) -> dict:
        msg = llm.invoke(build_messages(state["spec"], output_name))
        usage = getattr(msg, "usage_metadata", None) or {}
        return {
            "content": (msg.content or "").strip() + "\n",
            "tokens": int(usage.get("total_tokens", 0) or 0),
        }

    def persist(state: State) -> dict:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(state["content"], encoding="utf-8")
        return {}

    graph = StateGraph(State)
    graph.add_node("generate", generate)
    graph.add_node("persist", persist)
    graph.add_edge(START, "generate")
    graph.add_edge("generate", "persist")
    graph.add_edge("persist", END)
    app = graph.compile()

    final = app.invoke({"spec": spec, "content": "", "tokens": 0})
    print(f"MODEL_REPORT: {model}")
    print(f"TOKENS_USED: {final.get('tokens', 0)}")
    print(f"langgraph-worker: wrote {output_name} ({len(final.get('content', ''))} chars)")
    return 0


def run_react(model: str, temperature: float, spec: str, taskdir: Path) -> int:
    """A tool-using ReAct agent with a taskdir-confined write_file tool.

    This is the path that unlocks multi-file / iterative work once a
    tool-capable model is available on a bigger machine. Small models often
    fail to emit valid tool calls, which is why `direct` is the default.
    """
    from langchain_core.tools import tool
    from langchain_ollama import ChatOllama
    from langgraph.prebuilt import create_react_agent

    written: list[str] = []

    @tool
    def write_file(path: str, content: str) -> str:
        """Write `content` to `path` (relative to the task dir). Returns a status string."""
        target = resolve_in_taskdir(path, taskdir)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(path)
        return f"wrote {path} ({len(content)} chars)"

    llm = ChatOllama(model=model, temperature=temperature)
    agent = create_react_agent(llm, [write_file])

    total = 0
    result = agent.invoke(
        {"messages": [("user",
            "Complete this task by writing deliverable file(s) with the "
            "write_file tool. Do not ask questions.\n\n" + spec)]}
    )
    for m in result.get("messages", []):
        usage = getattr(m, "usage_metadata", None) or {}
        total += int(usage.get("total_tokens", 0) or 0)

    print(f"MODEL_REPORT: {model}")
    print(f"TOKENS_USED: {total}")
    if not written:
        print("langgraph-worker: react agent produced no files", file=sys.stderr)
        return 1
    print(f"langgraph-worker: wrote {len(written)} file(s): {', '.join(written)}")
    return 0


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    taskdir = Path(args.taskdir).expanduser().resolve()
    if not taskdir.is_dir():
        print(f"langgraph-worker: task dir not found: {taskdir}", file=sys.stderr)
        return 2

    # Honor OLLAMA_HOST if the daemon is remote/non-default; ChatOllama reads it.
    os.environ.setdefault("OLLAMA_HOST", os.environ.get("OLLAMA_HOST", "127.0.0.1:11434"))

    try:
        if args.mode == "react":
            return run_react(args.model, args.temperature, args.spec, taskdir)
        output_path = resolve_in_taskdir(args.output, taskdir)
        return run_direct(args.model, args.temperature, args.spec, output_path, args.output)
    except ValueError as exc:
        print(f"langgraph-worker: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — surface any driver/connection error to worker.log
        print(f"langgraph-worker: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
