# agent-launch-pad

A harness for launching coding agents (**pi-mono**, **hermes-agent**) on
real benchmarks (terminal-bench-2 today, more later) with **kernel-isolated
Kata Containers**, **multi-key OpenRouter rotation**, and **the bench's own
pytest suite as the verifier**. Output is a per-cell trajectory archive plus
a distillation-ready dataset of trajectories that actually solved the task.

If you want trajectories for SFT/distill, this gives you trajectories
filtered by `grade_pass=True` from a real grader, not heuristic proxy.

## What it does

1. **Per-task images**: For each terminal-bench task, builds an image that
   layers the task's official environment (`alexgshaw/<task>:20251031`) plus
   both agents (pi-mono, hermes-agent) plus the task's own `tests/` folder.
2. **Run grid**: For every (agent × model × task) cell, spawns a Kata VM,
   the agent solves the task, then `bash /tests/test.sh` runs and writes
   `reward.txt` (1=pass / 0=fail) and `ctrf.json` (per-test breakdown).
3. **Multi-key dispatcher**: Round-robins per model across N OpenRouter
   keys; on 429/402 marks the (model, key) on cooldown and reuses other
   keys.
4. **Trace archive**: For each cell, saves a unified
   `trace.jsonl` (meta + messages + tool_calls + tool_results, including
   reasoning), a `trajectory.sharegpt.json` for SFT, and the raw
   verifier output.
5. **Aggregate / export**: `aggregate.py` produces per-model and per-task
   pass-rate tables; `dataset_export.py` filters down to grade_pass=True
   trajectories and writes JSONL+parquet ready for HF or training.

## Quick start

```bash
git clone https://github.com/alexwortega/agent-launch-pad.git
cd agent-launch-pad/agentbench
cp config.example.yaml config.yaml
# edit config.yaml — paste your OpenRouter key(s) into keys.OR_PRIMARY etc.
```

You'll need: Linux with KVM, Docker 27+, Kata Containers 3.30 (see below).

```bash
# Install Kata Containers as a Docker runtime (writes /etc/docker/daemon.json + restarts docker)
sudo bash agentbench/scripts/install_kata.sh
docker run --rm --runtime=kata hello-world   # smoke check

# Build the agent base image (pi + hermes + uv + node20)
docker build -t agentbench/base-tools:latest -f agentbench/images/Dockerfile.base-tools agentbench/images/

# Build per-task images for a small smoke set (3 terminal-bench tasks)
python3 -m agentbench.images.build_smoke_set --tasks cancel-async-tasks fix-git gcode-to-text

# Run a 6-cell smoke (3 tasks × 2 agents × 1 model) under Kata
python3 -m agentbench.run --tasks-per-bench 3 \
    --agents pi-mono,hermes-agent \
    --models openai/gpt-oss-120b:free \
    --benches terminal-bench-2 \
    --runtime kata \
    --task-ids cancel-async-tasks,fix-git,gcode-to-text

# Aggregate + export distill corpus
python3 -m agentbench.aggregate runs/<latest>
python3 -m agentbench.dataset_export runs/<latest>
ls runs/<latest>/distill_corpus.jsonl runs/<latest>/distill_corpus.parquet
```

## Full grid (terminal-bench-2 corpus)

```bash
# Build all 89 task images (parallel buildx, ~110 min)
python3 -m agentbench.images.build_smoke_set --all --parallel 4

# Full grid: 89 tasks × 2 agents × 7 free models = 1246 cells, ~3-8 hours wall
python3 -m agentbench.run --tasks-per-bench 89 \
    --agents pi-mono,hermes-agent \
    --models all \
    --benches terminal-bench-2 \
    --runtime kata \
    --max-parallel 16 \
    --timeout 900
```

## Layout

```
agentbench/
├── run.py                  CLI: --tasks-per-bench, --agents, --models, --benches, --runtime, --max-parallel
├── adapters/               One per benchmark (terminal-bench-2 in shell-env mode; others prompt-only)
├── runners/
│   ├── pi_mono/            Standalone pi-coding-agent docker image (prompt-only mode)
│   ├── hermes_agent/       Standalone hermes-agent docker image (prompt-only mode)
│   └── shell_env/          Runner for tasks that ship their own per-task image (terminal-bench)
├── infra/
│   ├── docker_pool.py      docker run wrapper; adds `-t` automatically when runtime is set (Kata needs TTY for pi)
│   ├── model_dispatcher.py Round-robin across all (model, key) slots; cooldown on quota errors
│   ├── orchestrator.py     asyncio.Semaphore(N), 1 retry on quota error, streaming summary.csv
│   └── trace_schema.py     Unified Event types: meta / message / tool_call / tool_result / error
├── verifier/parser.py      Reads /work/{reward.txt, ctrf.json} → VerifierGrade
├── images/
│   ├── Dockerfile.task-template    FROM ${TASK_ENV_IMAGE} + apt agent deps + npm pi + uv hermes
│   ├── agentbench-entrypoint.sh    In-container: spawn agent → run /tests/test.sh → exfiltrate to /work
│   ├── build_task_image.py         Builder: bench × task → agentbench/<bench>/<slug>:latest
│   └── build_smoke_set.py          Batch builder; --all / --tasks / --parallel
├── scripts/install_kata.sh         Downloads kata-static 3.30, registers containerd-shim-kata-v2, patches daemon.json
├── aggregate.py            Generates per_model.md / per_task.md / per_agent.md / stats.csv
├── dataset_export.py       Filters grade_pass=True → distill_corpus.jsonl + distill_corpus.parquet
└── regrade.py              Re-grades an old run dir (no LLM calls)
```

## Per-cell artifacts

```
runs/<ts>/<agent>/<bench>/<model>/<task_id>/
├── trace.jsonl                Unified events: meta, message+reasoning, tool_call, tool_result
├── trajectory.sharegpt.json   {"conversations": [{"from": "...", "value": "..."}, ...]}
├── trace.raw.jsonl            (pi-mono raw stream from --mode json)  OR
├── hermes_trajectory.json     (hermes raw sample_*.json)
├── reward.txt                 1 = pass, 0 = fail (terminal-bench convention)
├── ctrf.json                  pytest CTRF report (per-test pass/fail counts)
├── verifier.log               full pytest stdout/stderr
├── result.json                {ok, error, latency_s, model, agent, grade_pass, grade_score, ...}
└── stdout.log / stderr.log
```

## Configuration

`agentbench/config.yaml` (copy from `config.example.yaml`) controls:

- **`keys`**: OpenRouter API keys (`OR_PRIMARY`, `OR_SECONDARY`, ...). The
  dispatcher accepts any number; round-robin spreads load.
- **`chain`**: which models you actually want to grid against (`--models all`
  expands to this list). Each model listed once.
- **`fallback_on_quota`**: same models on additional keys, plus paid
  fallbacks at the bottom (Anthropic, DeepSeek, etc).
- **`orchestrator.max_parallel`**: how many Kata VMs run concurrently. Each
  VM uses ~5–7 GiB RAM and ~1 CPU under default Kata config; sane cap on a
  24-core / 256 GiB host is **16**.

## Why Kata?

Pi and hermes execute arbitrary shell commands generated by an LLM. Running
those under plain `runc` (Docker default) means a one-namespace boundary
between the agent and your host. Kata gives you a **separate Linux kernel
per cell** via KVM, so even an LLM-controlled shell can't escape sandboxing
faster than a bug in QEMU itself.

Cost: **+1.0 s cold boot, +3-5 % wall clock** on real cells (LLM API time
dominates anyway). For a 1246-cell run this adds ~30 minutes total — cheap
insurance.

## Status

What works today:
- Stage A: prompt-only smoke across 10 benches × 2 agents = 20 cells.
- Stage B: shell-env mode with real pytest verifier on terminal-bench-2,
  validated end-to-end (smoke gave grade_pass=True from the bench's own
  `tests/test.sh`).
- Stage C: full 1246-cell grid runs end-to-end with multi-key rotation.

What's deferred:
- Five other shell-tool benches (autocodebench, aider-polyglot,
  scienceagentbench, bixbench-cli, medagentbench) currently stay
  prompt-only with heuristic grading. Each needs its own per-task image
  builder and verifier wrapper.
- HF push (manual `huggingface-cli upload <repo> distill_corpus.parquet`
  is fine for now).
- Checkpoint/resume in the orchestrator. Today, restarting after a kill
  starts a new run dir.

See `agentbench/CLAUDE.md` for deeper notes (gotchas, schemas, model-id
formats, the TTY-under-Kata trick, etc.).

## License

MIT — see [LICENSE](LICENSE).
