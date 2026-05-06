#!/usr/bin/env bash
# Runs inside per-task container.
# Sequence: spawn agent against /instruction.md → run verifier → exfiltrate results to /work.
set -uo pipefail

: "${AGENT:?AGENT env required (pi|hermes)}"
: "${MODEL_ID:?MODEL_ID env required}"
: "${OPENROUTER_API_KEY:?OPENROUTER_API_KEY env required}"
: "${OPENROUTER_BASE_URL:=https://openrouter.ai/api/v1}"

WORK="${WORK:-/work}"
mkdir -p "$WORK"

PROMPT="${PROMPT_OVERRIDE:-$(cat /instruction.md)}"

ts() { date -u +"%Y-%m-%dT%H:%M:%S.%3NZ"; }
log() { printf '[%s] entrypoint: %s\n' "$(ts)" "$*"; }

# Persistence: write a meta event so traces always have it even on early failure
{
    printf '{"type":"meta","ts":"%s","agent":"%s","model":"%s","mode":"shell-env"}\n' \
        "$(ts)" "$AGENT" "$MODEL_ID"
} > "$WORK/trace.jsonl"

log "agent=$AGENT model=$MODEL_ID; running in $(pwd)"

case "$AGENT" in
    pi)
        # Use pi from npm-global. --mode json emits structured events to stdout;
        # we tee to /work/trace.raw.jsonl and let host-side runner.py normalize.
        pi --no-session --mode json --provider openrouter \
            --model "$MODEL_ID" \
            -p "$PROMPT" \
            > "$WORK/trace.raw.jsonl" 2> "$WORK/agent.stderr"
        AGENT_EXIT=$?
        ;;
    hermes)
        export TERMINAL_ENV=local
        mkdir -p "$WORK/hermes_out"
        cd "$WORK/hermes_out"
        python3 /opt/hermes-agent/run_agent.py \
            -q "$PROMPT" \
            --model "$MODEL_ID" \
            -a "$OPENROUTER_API_KEY" \
            -b "$OPENROUTER_BASE_URL" \
            --max_turns 10 \
            --save_sample True \
            --save_trajectories True \
            > "$WORK/agent.stdout" 2> "$WORK/agent.stderr"
        AGENT_EXIT=$?
        # Surface trajectory file
        SAMPLE=$(ls "$WORK/hermes_out"/sample_*.json 2>/dev/null | head -n1)
        [ -n "$SAMPLE" ] && cp "$SAMPLE" "$WORK/hermes_trajectory.json"
        ;;
    *)
        log "ERROR: unknown AGENT=$AGENT"
        exit 2
        ;;
esac

log "agent exited rc=$AGENT_EXIT; running verifier (bash /tests/test.sh)"

# Verifier — run from the task's WORKDIR (/app in terminal-bench) so test.sh's
# `$PWD != /` check passes.
mkdir -p /logs/verifier
cd /app 2>/dev/null || cd "$WORK"
bash /tests/test.sh > "$WORK/verifier.log" 2>&1
VERIFIER_EXIT=$?

# Exfiltrate verifier outputs (terminal-bench convention writes to /logs/verifier/)
cp /logs/verifier/reward.txt "$WORK/" 2>/dev/null || true
cp /logs/verifier/ctrf.json  "$WORK/" 2>/dev/null || true

log "verifier exited rc=$VERIFIER_EXIT; reward=$(cat $WORK/reward.txt 2>/dev/null || echo NA)"

# Make every file in /work readable+writable by the host user (we ran as root inside).
chmod -R a+rwX "$WORK" 2>/dev/null || true

# Always exit 0 — agent-failed and verifier-failed are signaled via files.
exit 0
