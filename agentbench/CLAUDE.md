# agentbench — onboarding notes for Claude (or anyone reading the source)

Pipeline for collecting agent trajectories from real benchmarks
(currently terminal-bench-2, 89 tasks) using two harnesses (**pi-mono**,
**hermes-agent**) across 7 free OpenRouter models, isolated by
**Kata Containers** (kernel-level VMs). Each cell runs the agent inside a
per-task Docker image, then runs the bench's own `tests/test.sh` (pytest
with `pytest-json-ctrf`) to grade.

Output: `runs/<ts>/<agent>/<bench>/<model>/<task>/{trace.jsonl,
trajectory.sharegpt.json, result.json, reward.txt, ctrf.json,
verifier.log}` plus aggregated `stats.csv` and a distillation-ready
`distill_corpus.{jsonl,parquet}` (filtered to `grade_pass=True`).

## Layout

```
agentbench/
├── config.yaml               14 OR keys + 7 free models + 2 paid fallbacks; round-robin rotation (gitignored)
├── config.example.yaml       template — copy to config.yaml and fill in keys
├── run.py                    CLI: --tasks-per-bench, --agents, --models, --benches, --runtime, --task-ids
├── preflight.py              Stage A prompt-only smoke (20 cells)
├── preflight_shell_env.py    Shell-env smoke for terminal-bench tasks
├── regrade.py                Post-hoc grader: reads existing run dir, recomputes grade_pass
├── aggregate.py              per_model.md / per_task.md / per_agent.md / stats.csv
├── dataset_export.py         Filter grade_pass=True → distill_corpus.jsonl + parquet (pandas)
├── adapters/                 One per bench
│   ├── _base.py              BenchAdapter, GradeResult, helpers (grade_final_answer, grade_tool_evidence)
│   ├── terminal_bench.py     mode="shell-env"; loads from /home/alexw/.cache/harbor/tasks/.../terminal-bench/
│   ├── gaia.py / financeagent.py / theagentcompany.py / seta_env.py    prompt-only, light heuristics
│   └── (autocodebench / aider_polyglot / scienceagent / bixbench / medagent stay prompt-only)
├── runners/
│   ├── _base.py              AgentRunner, Task (mode, env_image, cwd, verifier_cmd, reward_path, timeouts)
│   ├── pi_mono/              Dockerfile (node:20-bookworm + npm pi-coding-agent), entrypoint, runner
│   ├── hermes_agent/         Dockerfile (python:3.11-slim + uv venv hermes-agent), entrypoint, runner
│   └── shell_env/runner.py   ShellEnvRunner: spawns task.env_image directly (per-task baked image)
├── infra/
│   ├── docker_pool.py        run_container(runtime="kata") auto-adds `-t` (TTY needed for pi under Kata)
│   ├── model_dispatcher.py   ModelDispatcher.acquire(): per-model round-robin across non-cooldown keys
│   ├── orchestrator.py       asyncio.Semaphore(N), 1-retry on quota error, summary.csv streaming
│   └── trace_schema.py       Event{type:meta|message|tool_call|tool_result|error}, write_jsonl, to_sharegpt
├── verifier/parser.py        Parses /work/{reward.txt,ctrf.json} → VerifierGrade(pass_, score, n_passed, n_failed)
├── images/
│   ├── Dockerfile.base-tools         debian:13-slim + python3 + node20 + uv + pi-coding-agent + hermes
│   ├── Dockerfile.task-template      FROM ${TASK_ENV_IMAGE} + apt agent deps + npm pi + uv hermes + COPY tests + entrypoint
│   ├── agentbench-entrypoint.sh      In-container: spawn agent → run /tests/test.sh → exfiltrate reward+ctrf to /work
│   ├── build_task_image.py           Builder: bench × task → agentbench/<bench>/<slug>:latest
│   ├── build_smoke_set.py            Batch builder; --all walks harbor cache; --skip-existing default
│   └── manifest.yaml                 Registry of built images
├── scripts/install_kata.sh           Downloads kata-static 3.30 + sets up containerd-shim-kata-v2 + patches /etc/docker/daemon.json
└── runs/                             Output dirs (one per run, plus preflight subdirs) — gitignored
```

## Key commands

```bash
# Stage A regression (prompt-only, all 10 benches)
python3 -m agentbench.run --tasks-per-bench 1 --agents pi-mono,hermes-agent \
    --models openai/gpt-oss-120b:free --runtime kata

# Full grid (1246 cells)
python3 -m agentbench.run --tasks-per-bench 89 --agents pi-mono,hermes-agent \
    --models all --benches terminal-bench-2 --runtime kata --max-parallel 16 --timeout 900

# Subset of tasks (filters after adapter loads up to 200 candidates)
python3 -m agentbench.run --tasks-per-bench 5 --agents pi-mono --models openai/gpt-oss-120b:free \
    --benches terminal-bench-2 --runtime kata --task-ids cancel-async-tasks,fix-git,gcode-to-text

# Build N task images (skip existing by default)
python3 -m agentbench.images.build_smoke_set --tasks cancel-async-tasks fix-git --parallel 2
python3 -m agentbench.images.build_smoke_set --all --parallel 4   # all 89

# Post-hoc analysis
python3 -m agentbench.aggregate runs/<ts>
python3 -m agentbench.dataset_export runs/<ts>           # only grade_pass=True
python3 -m agentbench.dataset_export runs/<ts> --include-fails

# Re-grade old run (e.g. after grader logic changes)
python3 -m agentbench.regrade runs/<ts>
```

## Models / keys

`config.yaml` accepts any number of OR keys. Each free model is listed under every
key. `ModelDispatcher.acquire(model)` does **round-robin per model** across
non-cooldown keys → with N keys × 7 models you get N × 50 RPD per model on the OR
free tier. Cooldown defaults to 1h on `429`/`402`/`User not found`/`limit_remaining":0`.

Paid fallbacks (`deepseek/deepseek-v4-pro`, `anthropic/claude-sonnet-4.5`) sit at
the bottom of `fallback_on_quota`. They activate only when ALL free model+key
combinations are on cooldown.

## Gotchas (battle-tested)

- **pi-mono hangs under Kata without TTY.** `docker_pool.run_container` adds `-t`
  whenever `runtime` is set. Don't remove this — it's why we needed the patch.
- **Container name collisions across the model grid.** Names embed an
  `md5(model|key)[:8]` suffix so 7 models × 89 tasks don't collide on shared task
  name.
- **Verifier `test.sh` checks `$PWD != "/"`.** Entrypoint does `cd /app` (the
  WORKDIR baked into terminal-bench task images) before invoking verifier. Don't
  `cd /` after the agent.
- **Container writes as root → host PermissionError.** Entrypoint ends with
  `chmod -R a+rwX /work`. If the container is killed by timeout, that line never
  runs → `shell_env/runner.py` and `run.py` both call
  `sudo -n chown -R $UID:$GID <work_dir>` after `docker run` exits. Requires
  passwordless sudo on the host.
- **`pi-coding-agent` needs Node 20+** (regex `/v` flag). Debian 13's apt nodejs
  is 18 — use NodeSource `setup_20.x` repo instead. Already wired into
  `Dockerfile.task-template`.
- **`hermes-agent` model id format.** Pass `provider/model:tag` directly (no
  `openrouter/` prefix; hermes adds it itself for litellm compat).
- **`hermes-agent` terminal backend.** Use `TERMINAL_ENV=local` for shell-env
  mode (the agent runs *inside* the task's env container, so the local shell IS
  the env). The `docker` backend would spawn fresh containers per command —
  wrong for this pattern.
- **Kata via containerd-shim, NOT old kata-runtime path.** `daemon.json` uses
  `{"runtimes": {"kata": {"runtimeType": "io.containerd.kata.v2"}}}` — the
  `containerd-shim-kata-v2` binary is symlinked to `/usr/local/bin/`.
- **Practical parallelism cap is 16 on a 24-core / 256 GiB host.** 16 Kata VMs
  (qemu, default 2 GiB RAM) → load avg ~24-25, RAM ~100 GiB. To bump higher,
  switch the hypervisor to cloud-hypervisor or shrink default_memory in
  `/opt/kata/share/defaults/kata-containers/configuration.toml`.
- **Hermes timeout cap.** `task.toml` may say `agent_timeout_sec = 9000`. The
  adapter caps to 900s for smoke-scale runs (`min(toml_to, 900)`). For real
  evaluation, raise it.
- **Output is line-buffered.** Streaming progress to a log via `tee` doesn't
  surface for `python3 -m agentbench.run`. Use `summary.csv` (incrementally
  written) or a `wc -l` poll loop instead.

## Trace schema (`infra/trace_schema.py`)

```jsonc
{"ts":"...", "type":"meta",        "agent":"pi-mono", "model":"...", "bench":"...", "task_id":"..."}
{"ts":"...", "type":"message",     "role":"user|assistant|system|tool", "content":"...", "reasoning":"..."}
{"ts":"...", "type":"tool_call",   "name":"bash", "args":{...}, "id":"..."}
{"ts":"...", "type":"tool_result", "id":"...", "ok":true, "stdout":"...", "stderr":"..."}
{"ts":"...", "type":"error",       "kind":"rate_limit|crash|timeout", "detail":"..."}
```

`to_sharegpt()` converts to `[{"from": "human|gpt|tool|system", "value": "..."}]`.
Tool calls serialize as `<tool_call>{...}</tool_call>` inside an assistant turn.

## Per-cell `result.json` schema

```jsonc
{
  "ok": true,                       // container exited cleanly + ≥1 message/tool_call event
  "error": null,                    // docker error / executor crash detail
  "latency_s": 73.0,
  "model": "openai/gpt-oss-120b:free",
  "agent": "pi-mono",
  "bench": "terminal-bench-2",
  "task_id": "cancel-async-tasks",
  "mode": "shell-env",
  "grade_pass": true,               // ← from verifier.parser.parse() for shell-env, else heuristic
  "grade_score": 1.0,
  "grade_reason": "verifier: reward.txt=1",
  "verifier_raw_reward": "1",
  "verifier_n_passed": 6,
  "verifier_n_failed": 0
}
```

## Key contracts

- A task is **shell-env** iff `task.env_image` is set; the orchestrator routes it
  to `ShellEnvRunner`. Otherwise it's prompt-only and goes to `PiMonoRunner` /
  `HermesAgentRunner`.
- `BenchAdapter.load_tasks(n)` returns up to `n` tasks; the adapter is responsible
  for ordering (terminal-bench is alphabetical) and for skipping tasks whose
  env_image isn't built locally.
- `VerifierGrade` from `verifier/parser.py` is authoritative for shell-env:
  `reward.txt=1` → pass, `reward.txt=0` → fail, partial CTRF counts → fractional
  score. Only when reward.txt is missing do we fall back to adapter `_grade_impl`
  (which is just a heuristic safety net).
- `BenchAdapter.grade(...)` returns `GradeResult(pass_=None|True|False, score,
  reason)`. `None` means "no signal" — don't count it as either pass or fail.

## What's NOT done (deferred work)

- Five other shell-tool benches (autocodebench, aider-polyglot, scienceagentbench,
  bixbench-cli, medagentbench) remain prompt-only with heuristic grading. Each
  would need its own per-task image builder + verifier wrapper (~0.5–1 day each).
- HF push of `distill_corpus.parquet` is not wired (manual `huggingface-cli upload`
  for now).
- No checkpoint/resume in the orchestrator — interrupting a grid loses progress;
  restart begins a new run dir.
- Reasoning-budget cap on agents — pi-mono can spend 800+s on hard tasks, eating
  slots. Either lower `--timeout` or prune outliers.
