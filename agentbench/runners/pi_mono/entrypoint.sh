#!/bin/sh
set -e

TASK_PROMPT=$(jq -r .prompt /work/task.json)
MODEL_ID="${MODEL_ID:?MODEL_ID env required}"

: "${OPENROUTER_API_KEY:?OPENROUTER_API_KEY env required}"

cd /work

# pi-coding-agent: --mode json emits JSONL events to stdout, exits when done
# --no-session disables persistent session storage so runs are isolated.
pi \
    --mode json \
    --provider openrouter \
    --model "$MODEL_ID" \
    --no-session \
    -p "$TASK_PROMPT" \
    > /work/trace.raw.jsonl 2> /work/agent.stderr || echo "{\"type\":\"error\",\"kind\":\"crash\",\"detail\":\"exit=$?\"}" >> /work/trace.raw.jsonl
