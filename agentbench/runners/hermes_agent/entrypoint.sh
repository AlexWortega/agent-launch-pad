#!/bin/sh
set -e

TASK_PROMPT=$(jq -r .prompt /work/task.json)
TASK_ID=$(jq -r .id /work/task.json)
MODEL_ID="${MODEL_ID:?MODEL_ID env required}"

: "${OPENROUTER_API_KEY:?OPENROUTER_API_KEY env required}"
: "${OPENROUTER_BASE_URL:=https://openrouter.ai/api/v1}"

WORK=/work
mkdir -p "$WORK/hermes_out"
cd "$WORK/hermes_out"

# run_agent.py expects model as openrouter/<id>; --save_trajectories writes
# to ./trajectory_samples.jsonl in the CWD.
python3 /opt/hermes-agent/run_agent.py \
    -q "$TASK_PROMPT" \
    --model "$MODEL_ID" \
    -a "$OPENROUTER_API_KEY" \
    -b "$OPENROUTER_BASE_URL" \
    --max_turns 10 \
    --save_trajectories True \
    --save_sample True \
    > "$WORK/agent.stdout" 2> "$WORK/agent.stderr" \
  || echo "{\"type\":\"error\",\"kind\":\"crash\",\"detail\":\"run_agent exit=$?\"}" > "$WORK/trace.raw.jsonl"

# Surface whichever trajectory file landed in CWD into known paths.
# --save_sample writes sample_<uuid>.json (single JSON object).
# --save_trajectories appends to trajectory_samples.jsonl.
SAMPLE=$(ls "$WORK/hermes_out"/sample_*.json 2>/dev/null | head -n1)
JSONL=$(ls "$WORK/hermes_out"/*.jsonl 2>/dev/null | head -n1)
if [ -n "$SAMPLE" ]; then
    cp "$SAMPLE" "$WORK/hermes_trajectory.json"
fi
if [ -n "$JSONL" ]; then
    cp "$JSONL" "$WORK/hermes_trajectory.jsonl"
fi
