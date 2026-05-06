# Evaluating a custom model on terminal-bench

You've trained or fine-tuned a model and want to know:

- How does it perform on terminal-bench-2 vs the free-tier baselines?
- Which tasks does it solve / fail?
- Get distill-grade trajectories from your model for a downstream pipeline?

This walkthrough takes you from a `.safetensors` checkpoint (or HF repo) to a full
89-task pass/fail report under the same Kata-isolated infrastructure that produced
the baseline grid.

## Prerequisites

You should already have:

- A trained model: HuggingFace repo id (e.g. `myorg/my-finetune`) **or** a local
  path (e.g. `/home/me/checkpoints/my-finetune`).
- Working install of agentbench: `bash agentbench/scripts/install_kata.sh` done,
  `agentbench/base-tools:latest` and at least a smoke set of per-task images built.
- `vllm`, `sglang`, `llama.cpp/llama-server`, or any other OpenAI-compatible
  inference server. **vLLM** is the recommended default — it speaks the OpenAI
  Chat Completions API out of the box and supports tool calls.

## Step 1 — Serve your model with an OpenAI-compatible API

Easiest path: vLLM in Docker. Replace `MY_MODEL` and `MY_NAME` accordingly.

```bash
docker run -d \
    --name my-model-vllm \
    --gpus all \
    -p 8000:8000 \
    -v $HOME/.cache/huggingface:/root/.cache/huggingface \
    vllm/vllm-openai:latest \
    --model MY_MODEL \
    --served-model-name MY_NAME \
    --enable-auto-tool-choice \
    --tool-call-parser hermes \
    --reasoning-parser qwen3
```

Sanity-check:

```bash
# Should return the model card
curl -s http://localhost:8000/v1/models | jq

# Should return an assistant message
curl -s http://localhost:8000/v1/chat/completions -H "Content-Type: application/json" -d '{
    "model": "MY_NAME",
    "messages": [{"role": "user", "content": "Reply with the single word OK."}],
    "temperature": 0
}' | jq -r .choices[0].message.content
```

If either fails, fix vLLM first — agentbench will surface the same error inside the
agent trace, just less obviously.

**Why `--enable-auto-tool-choice` + `--tool-call-parser`?** Both pi-mono and
hermes-agent rely on OpenAI-compatible function calling. vLLM defaults to text-mode
output; without the parser the agent will see plain text instead of structured tool
calls and either ignore them or crash.

If your model wasn't trained with the Hermes prompt format, swap `--tool-call-parser`
to one that matches (vLLM ships parsers for `hermes`, `mistral`, `granite`,
`llama3_json`, `pythonic`, etc).

## Step 2 — Add the model to `agentbench/config.yaml`

Custom models go alongside the OpenRouter chain. Each entry can supply its own
`base_url` so it doesn't share the OR rotation:

```yaml
keys:
  OR_PRIMARY:    sk-or-v1-...                       # existing OR keys
  LOCAL_VLLM:    dummy                              # vLLM ignores the key but the field is required

chain:
  # Existing free models stay here…
  - {model: "openai/gpt-oss-120b:free", key: OR_PRIMARY}
  # …and add your custom one with an explicit base_url:
  - {model: "MY_NAME",
     key: LOCAL_VLLM,
     base_url: "http://172.17.0.1:8000/v1"}         # 172.17.0.1 is the docker bridge gateway,
                                                     # i.e. how a Kata VM reaches the host
```

`base_url` on a chain entry **overrides** the global `openrouter.base_url` for that
(model, key) slot only — the OR rotation for free models keeps using OpenRouter
unchanged.

> **Note on networking.** Kata VMs see the host as `172.17.0.1` by default.
> If your vLLM is on a different machine, just paste its full URL
> (`http://10.0.0.5:8000/v1`). Make sure it's reachable from the Kata VM —
> `docker run --rm --runtime=kata curlimages/curl -sS http://YOUR_HOST:8000/v1/models`
> is the cheapest test.

## Step 3 — Run the grid against your model

Single agent + your model + all 89 tasks:

```bash
python3 -m agentbench.run \
    --tasks-per-bench 89 \
    --agents pi-mono \
    --models MY_NAME \
    --benches terminal-bench-2 \
    --runtime kata \
    --max-parallel 8 \                 # vLLM batches better with fewer concurrent requests
    --timeout 900
```

Both agents:

```bash
python3 -m agentbench.run \
    --tasks-per-bench 89 \
    --agents pi-mono,hermes-agent \
    --models MY_NAME \
    --benches terminal-bench-2 \
    --runtime kata \
    --max-parallel 8 \
    --timeout 900
```

Subset of tasks (e.g. ones the baseline already solves — useful for regression
checks):

```bash
python3 -m agentbench.run \
    --tasks-per-bench 5 --agents pi-mono --models MY_NAME \
    --benches terminal-bench-2 --runtime kata \
    --task-ids cancel-async-tasks,fix-git,gcode-to-text,bn-fit-modify,adaptive-rejection-sampler
```

## Step 4 — Look at the numbers

Aggregate the run dir into per-model / per-task / per-agent breakdowns:

```bash
python3 -m agentbench.aggregate runs/<latest>
cat runs/<latest>/per_model.md       # pass-rate table
cat runs/<latest>/per_task.md        # task-by-task
column -t -s, runs/<latest>/stats.csv | less -S
```

Compare against the baseline run (the OR-grid you ran before training):

```bash
diff -u <(cat runs/BASELINE/per_task.md) <(cat runs/MY_RUN/per_task.md)
```

Or in Python, if you want a confusion matrix:

```python
import json, glob, collections
def load(d):
    return [json.load(open(p)) for p in glob.glob(f"{d}/*/*/*/*/result.json")]

base = load("runs/BASELINE")
mine = load("runs/MY_RUN")

base_pass = {(r["task_id"], r["agent"]) for r in base if r.get("grade_pass")}
mine_pass = {(r["task_id"], r["agent"]) for r in mine if r.get("grade_pass")}

print("regressed (was pass, now fail):", base_pass - mine_pass)
print("new wins (was fail, now pass):", mine_pass - base_pass)
print("both pass:", base_pass & mine_pass)
```

## Step 5 — Pull out trajectories for distillation

If you want only the trajectories that actually solved the task:

```bash
python3 -m agentbench.dataset_export runs/<latest>
ls runs/<latest>/distill_corpus.jsonl runs/<latest>/distill_corpus.parquet
```

Each row has `instruction`, `conversations` (sharegpt), `model`, `task_id`,
`grade_pass`, `grade_score`, plus verifier metadata. Push to HF:

```bash
huggingface-cli upload my-org/my-finetune-distill \
    runs/<latest>/distill_corpus.parquet \
    distill_corpus.parquet
```

For a corpus that includes failed attempts (useful for DPO / preference data):

```bash
python3 -m agentbench.dataset_export runs/<latest> --include-fails
ls runs/<latest>/all_corpus.jsonl
```

## Tuning advice

- **`--max-parallel` for local serving.** OR can take 16+ concurrent calls fine, but
  one vLLM instance saturates much sooner. 8 is usually safe for a 4B–13B model on a
  single GPU. Bump only if vLLM's `running` queue stays low.
- **`--timeout 900` is for free-tier-grade models.** A faster, more capable model
  rarely needs 15 minutes per cell. Drop to 600 to abort obvious infinite loops
  earlier (saves wall clock without losing real solves).
- **Multiple seeds per cell.** No CLI flag yet, but you can wrap with a shell loop
  that bumps the run dir name and aggregate after — three runs at temperature ≈ 0.7
  each, take the union of `grade_pass=True`.
- **Pi-mono vs hermes-agent.** On the baseline, pi was about 2× hermes on
  terminal-bench. Don't read too much into the absolute number; what matters is
  whether your fine-tune **closes the gap** that an agent harness exposes (e.g.
  hermes might do worse because of its larger system prompt eating context).

## What this evaluation does *not* test

- Speed / throughput. We measure cells/min during the grid but don't normalise per
  GPU. For perf you want llmperf / sglang's own benchmarks.
- Cost. All free-tier OR models bill at 0; your custom model is free locally; but
  if you compare against paid models, look at the OR `usage` field on the result.
- Out-of-distribution domains. Terminal-bench is shell + code + Linux. If you're
  fine-tuning for something else, this will under-rate domain-specific gains.

## Troubleshooting

| symptom | probable cause | fix |
|---|---|---|
| `404 Not Found` from agent.stderr | model name mismatch | check `curl /v1/models` exposes the same string you put in `chain.model` |
| Cells time out at exactly 900s with empty trace | vLLM dropped the connection | check vLLM log; lower `--max-parallel` |
| `grade_pass=False` for everything but baseline solved them | tool-call format isn't being recognised | switch `--tool-call-parser` in vLLM, or add `--enable-auto-tool-choice` |
| Hermes-only failures, pi works | hermes ships a much heavier system prompt; small models can't follow tool spec | accept it (pi often solves what hermes can't on small models) or skip hermes via `--agents pi-mono` |
| Cells fail with `connection refused` | Kata VM can't reach `localhost` of the host | use `172.17.0.1` (docker bridge) or the host's LAN IP, not `localhost`/`127.0.0.1` |
