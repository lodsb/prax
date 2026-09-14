#!/usr/bin/env bash
# Start llama.cpp's llama-server as the OpenAI-compatible backend prax.yaml
# names as an `openai` model (docs/howto.md 3k) — the shell twin of
# scripts/llama_server.ps1, same options, same command line, for Linux and
# macOS (Metal or CUDA builds of llama.cpp; Homebrew's `llama.cpp` puts
# llama-server on the PATH).
#
#   scripts/llama_server.sh --model ~/models/Qwen3.6-35B-A3B-UD-Q4_K_S.gguf \
#       --mmproj mmproj-F16.gguf --slots 2 --cpu-moe 2 --ubatch 256 \
#       --image-max-tokens 1024 --no-thinking
#   scripts/llama_server.sh --model bge-reranker-v2-m3-Q8_0.gguf --reranker --port 8081
#
# Every slot gets --ctx-per-slot tokens (the server splits -c evenly); the
# KV cache is stored at 8 bits; --mmproj loads the model's multimodal
# projector (a name without a directory is looked for next to the model);
# --cpu-moe N keeps the expert weights of the first N layers in RAM (the
# VRAM dial that matters for a MoE model, see the .ps1 for the numbers);
# --no-thinking turns a Qwen3.x model's reasoning off, which extraction
# under a grammar needs. --power-limit sets the card's cap with nvidia-smi
# first (root, Linux only).
set -euo pipefail

MODEL=""; PORT=8080; SLOTS=2; CTX_PER_SLOT=8192; BIN=""; ALIAS="local-server"
MMPROJ=""; CPU_MOE=0; PROJECTOR_ON_CPU=0; IMAGE_MAX_TOKENS=0; UBATCH=512
RERANKER=0; METRICS=1; NO_THINKING=0; POWER_LIMIT=0; THREADS=8

while [ $# -gt 0 ]; do
  case "$1" in
    --model) MODEL="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --slots) SLOTS="$2"; shift 2 ;;
    --ctx-per-slot) CTX_PER_SLOT="$2"; shift 2 ;;
    --bin) BIN="$2"; shift 2 ;;
    --alias) ALIAS="$2"; shift 2 ;;
    --mmproj) MMPROJ="$2"; shift 2 ;;
    --cpu-moe) CPU_MOE="$2"; shift 2 ;;
    --projector-on-cpu) PROJECTOR_ON_CPU=1; shift ;;
    --image-max-tokens) IMAGE_MAX_TOKENS="$2"; shift 2 ;;
    --ubatch) UBATCH="$2"; shift 2 ;;
    --threads) THREADS="$2"; shift 2 ;;
    --reranker) RERANKER=1; shift ;;
    --no-metrics) METRICS=0; shift ;;
    --no-thinking) NO_THINKING=1; shift ;;
    --power-limit) POWER_LIMIT="$2"; shift 2 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

[ -n "$MODEL" ] || { echo "--model is required" >&2; exit 2; }
[ -f "$MODEL" ] || { echo "model not found: $MODEL" >&2; exit 2; }
if [ -z "$BIN" ]; then
  if command -v llama-server >/dev/null 2>&1; then BIN="$(command -v llama-server)"
  else BIN="${XDG_DATA_HOME:-$HOME/.local/share}/prax/llama.cpp/llama-server"; fi
fi
[ -x "$BIN" ] || { echo "llama-server not found at $BIN (docs/howto.md 3k)" >&2; exit 2; }
if [ "$POWER_LIMIT" -gt 0 ]; then nvidia-smi -pl "$POWER_LIMIT" >/dev/null; fi

CTX=$((SLOTS * CTX_PER_SLOT))
EXTRA=()
if [ "$NO_THINKING" = 1 ]; then EXTRA+=(--reasoning off --reasoning-budget 0); fi
if [ -n "$MMPROJ" ]; then
  [ -f "$MMPROJ" ] || MMPROJ="$(dirname "$MODEL")/$MMPROJ"
  [ -f "$MMPROJ" ] || { echo "projector not found: $MMPROJ" >&2; exit 2; }
  EXTRA+=(--mmproj "$MMPROJ")
  if [ "$PROJECTOR_ON_CPU" = 1 ]; then EXTRA+=(--no-mmproj-offload); fi
  if [ "$IMAGE_MAX_TOKENS" -gt 0 ]; then EXTRA+=(--image-max-tokens "$IMAGE_MAX_TOKENS"); fi
fi
if [ "$CPU_MOE" -gt 0 ]; then EXTRA+=(--n-cpu-moe "$CPU_MOE"); fi
if [ "$METRICS" = 1 ]; then EXTRA+=(--metrics); fi

if [ "$RERANKER" = 1 ]; then
  # a cross-encoder: the query and a candidate in one sequence, one score
  # out; the batch is the context, and 4096 tokens covers any chunk prax sends
  exec "$BIN" "${EXTRA[@]}" --model "$MODEL" --alias "$ALIAS" --host 127.0.0.1 --port "$PORT" \
    --reranking --n-gpu-layers 999 --ctx-size 4096 --parallel 1 \
    --batch-size 4096 --ubatch-size 4096 --threads "$THREADS" --no-webui
fi
exec "$BIN" "${EXTRA[@]}" \
  --model "$MODEL" --alias "$ALIAS" --host 127.0.0.1 --port "$PORT" \
  --n-gpu-layers 999 --load-mode mmap \
  --ctx-size "$CTX" --parallel "$SLOTS" \
  --flash-attn on --cache-type-k q8_0 --cache-type-v q8_0 \
  --batch-size 2048 --ubatch-size "$UBATCH" --threads "$THREADS" --no-webui
