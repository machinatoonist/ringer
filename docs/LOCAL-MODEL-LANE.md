# Local-model lane (Ollama) — setup, install, and seamless scale-up

This fork adds a **local model** as a Ringer worker so an orchestrator can
dispatch verified work to a model running on *this* machine — no API bill,
nothing leaves the box, no dependency on any single inference provider. It is
set up as a *minimally viable smoke test* on a weak machine (2016 Intel
MacBook Pro, 8 GB RAM, CPU-only) and designed so that moving to a
higher-capacity machine (e.g. a Mac mini running a Qwen coder in the 30B class)
is a one-field change, not a rewrite.

## The two lanes

| Lane | Engine | What drives the model | Status on this machine |
|---|---|---|---|
| **LangGraph** | `langgraph` | A small self-contained LangGraph agent we own (`engines/langgraph_worker.py`) | **In use + scored.** Robust on tiny models via `direct` mode. This is the primary local lane here. |
| **OpenCode → Ollama** | `opencode` | OpenCode's full agentic harness, local model as an OpenAI-compatible provider | **Wired + ready, not yet exercised.** A 1B model can't reliably drive OpenCode's agentic tool-loop; this lane comes into its own with the larger local model on the target machine. |

Both are config-driven. Scaling up = pull a bigger model in Ollama and change
the manifest `"model"` slug. Nothing else changes.

---

## Installation (from scratch)

### 1. Ollama (the local model runtime)

Ollama serves the model on an OpenAI-compatible endpoint at
`http://localhost:11434/v1`.

```bash
brew install ollama
```

> **Note for old Intel Macs / older macOS:** Homebrew may have no prebuilt
> bottle and will **compile Ollama from source** (it builds `llama.cpp` with
> CPU-tuned variants). On a 2-core i5 this took ~15–30 min. That is expected;
> let it finish. Current prebuilt Ollama binaries may not run on this
> vintage, which is *why* brew falls back to source — don't chase a prebuilt.

Start the server and pull the MVP model (~1.3 GB):

```bash
ollama serve &                 # or: brew services start ollama
ollama pull llama3.2:1b        # Meta Llama-3.2-1B-Instruct, tool-capable, CPU-friendly
```

Sanity check:

```bash
curl -s http://127.0.0.1:11434/api/generate \
  -d '{"model":"llama3.2:1b","prompt":"What is the capital of France? One word.","stream":false}'
```

**Runtime alternatives considered:** `llama.cpp`/`llama-server` (lighter, more
manual), LM Studio (GUI, too heavy for 8 GB). Ollama was chosen for
zero-friction model management + compatibility with both lanes.

### 2. LangGraph lane — Python venv

A dedicated venv keeps the LangChain/LangGraph deps isolated from system Python:

```bash
uv venv ~/.ringer/langgraph-venv --python 3.12
uv pip install --python ~/.ringer/langgraph-venv/bin/python langgraph langchain-ollama
# verify
~/.ringer/langgraph-venv/bin/python -c "import langgraph, langchain_ollama; print('ok')"
```

> `uv venv` creates a **pip-less** environment — install with `uv pip install
> --python <venv>/bin/python ...` (not `<venv>/bin/pip`, which won't exist).

Then add the engine to `~/.config/ringer/config.toml`:

```toml
[engines.langgraph]
bin = "/Users/<you>/.ringer/langgraph-venv/bin/python"
model_default = "llama3.2:1b"
args_template = [
  "/path/to/ringer/engines/langgraph_worker.py",
  "--model", "{model}",
  "--taskdir", "{taskdir}",
  "{engine_args}",
  "{spec}",
]
sandbox_args = []
full_access_args = []
token_regex = 'TOKENS_USED:\s*([0-9]+)'
model_report_regex = '(?m)^MODEL_REPORT:[ \t]*([^ \t\r\n]+)[ \t]*\r?$'
```

The worker (`engines/langgraph_worker.py`) is a compliant Ringer engine: reads
the spec as the last argv, runs with `cwd=taskdir`, confines writes to the task
dir **in code** (no OS sandbox — a Seatbelt wrapper is the hardening
follow-up), and prints `MODEL_REPORT:`/`TOKENS_USED:` lines the engine config
parses for scoreboard attribution and token accounting.

Worker modes (per-task `engine_args`):
- **`direct`** (default): a `generate → persist` StateGraph. The model only has
  to emit content; the graph writes the file. Robust on tiny models.
- **`react`** (`["--mode","react"]`): a tool-using ReAct agent with a
  taskdir-confined `write_file` tool. Unlocks multi-file/iterative work once a
  tool-capable model is available.

### 3. OpenCode → Ollama lane (wired, optional)

`~/.config/opencode/opencode.jsonc` declares Ollama as a custom provider:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "ollama": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Ollama (local)",
      "options": { "baseURL": "http://localhost:11434/v1" },
      "models": { "llama3.2:1b": { "name": "Llama 3.2 1B (local)", "options": { "num_ctx": 16384 } } }
    }
  }
}
```

Ringer's existing `[engines.opencode]` block then routes to it via a manifest
task with `"engine": "opencode", "model": "ollama/llama3.2:1b"`. Small models
are weak at OpenCode's agentic tool-loop; the `num_ctx` bump helps, a bigger
model helps more.

### 4. Register the model on the scoreboard

So the local model is attributed like any cloud model (name + lab + harness),
add it to `registry/model-identity.toml` (see `docs/TAXONOMY.md`):

```toml
[engines.langgraph]
harness = "LangGraph"
access = "Local (Ollama)"
default_model_key = "llama3.2:1b"

[engines.langgraph.models."llama3.2:1b"]
display = "Llama 3.2 1B"
lab = "Meta"
confidence = "verified"
source = "https://ollama.com/library/llama3.2 — Ollama serves Meta's Llama-3.2-1B-Instruct"
last_verified = 2026-07-12
```

`./ringer.py models` then shows the local model on the scoreboard alongside the
cloud models, with real first-try/pass rates from executed checks.

---

## Running the smoke test

`examples/local-model-smoke.json` — one task, executed check verifying the
*answer* (not just file existence), `max_parallel: 1` (8 GB can't hold two
inferences at once).

```bash
./ringer.py lint examples/local-model-smoke.json
./ringer.py run  examples/local-model-smoke.json --identity local-mvp
./ringer.py models          # see the local model on the scoreboard
```

Watch it on Ringside → http://127.0.0.1:8700.

---

## This machine's setup in use (reference)

| Thing | Value |
|---|---|
| Hardware | 2016 MacBook Pro (Intel i5-6267U), 8 GB RAM, no GPU |
| Ollama | `0.31.1` (compiled from source via brew), served at `127.0.0.1:11434` |
| Model | `llama3.2:1b` (Meta Llama-3.2-1B-Instruct, ~1.3 GB, Q4) |
| LangGraph venv | `~/.ringer/langgraph-venv` — langgraph 1.2.9, langchain-ollama 1.1.0 |
| Ringer config | `~/.config/ringer/config.toml` (`[engines.langgraph]`) |
| OpenCode config | `~/.config/opencode/opencode.jsonc` (Ollama provider) |
| Worker | `engines/langgraph_worker.py` |
| Smoke manifest | `examples/local-model-smoke.json` |

---

## Seamless scale-up on a bigger machine

1. `ollama pull <bigger-model>` (e.g. `qwen2.5-coder:32b`, or a Qwen3 coder).
2. Change the manifest `"model"` field (and, for the OpenCode lane, add the
   model id under `provider.ollama.models` in `opencode.jsonc`), and add a
   registry entry for the new slug.
3. Raise `max_parallel` once RAM allows concurrent inferences.
4. On the bigger model, the **OpenCode agentic lane** becomes viable — real
   multi-file/tool-using work, not just the LangGraph `direct` lane.

No engine code changes. The LangGraph lane can also point at a **remote**
Ollama by exporting `OLLAMA_HOST=<host:port>` — the worker honors it.
