#!/usr/bin/env bash
# A stranger's first hour, as a script: a fresh virtual environment, the
# README's quick start, a document in, a search out. Nothing of yours is
# touched — the venv, the store and the door's port are throwaway — and
# the exit code says whether the quick start still works. CI runs it on
# every push (.github/workflows/ci.yml); run it yourself after touching
# the install path:
#
#     bash scripts/smoke.sh            # ~3 minutes, mostly pip
#     PRAX_SMOKE_PORT=8199 bash scripts/smoke.sh
#
# What it checks, in the README's order: pip install -e ".[serve,work,dev]"
# into a new venv; `prax --version`; `prax serve` on an empty data
# directory answers /health; `prax status`, `prax` alone; `prax add` of a
# Markdown note is searchable at once (`prax search`, hybrid — the first
# search fetches the embedder's model, so the network is needed once);
# `prax models`; `prax doctor`; the door stops cleanly. The worker's steps
# and the models are not run: those are the tests' and the howto's.
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
# Git Bash on Windows: pip and python want a Windows path, bash a POSIX one
native() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf %s "$1"; fi; }
PORT="${PRAX_SMOKE_PORT:-8177}"
PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || PY=python
TMP="$(mktemp -d)"
DOOR="http://127.0.0.1:$PORT"
trap 'cleanup' EXIT

step() { printf '\n== %s\n' "$*"; }
fail() { printf '\n!! %s\n' "$*" >&2; exit 1; }
cleanup() {
  if [ -n "${DOOR_PID:-}" ]; then kill "$DOOR_PID" 2>/dev/null || true; wait "$DOOR_PID" 2>/dev/null || true; fi
  rm -rf "$TMP" 2>/dev/null || true  # Windows may hold a file a moment longer
}

step "a fresh venv in $TMP"
"$PY" -m venv "$TMP/venv"
if [ -x "$TMP/venv/bin/python" ]; then VBIN="$TMP/venv/bin"; else VBIN="$TMP/venv/Scripts"; fi
"$VBIN/python" -m pip install --quiet --upgrade pip
step "pip install -e \".[serve,work,dev]\"   (the README's line)"
"$VBIN/python" -m pip install --quiet -e "$(native "$HERE")[serve,work,dev]"
PRAX="$VBIN/prax"
[ -x "$PRAX" ] || [ -x "$PRAX.exe" ] || fail "no prax command after the install"

step "prax --version"
"$PRAX" --version

export PRAX_DATA_DIR="$(native "$TMP/store")"
unset PRAX_TOKEN PRAX_DOOR
step "prax serve on an empty store, port $PORT"
"$PRAX" serve --port "$PORT" > "$TMP/door.log" 2>&1 &
DOOR_PID=$!
for i in $(seq 1 60); do
  if curl -s --max-time 2 "$DOOR/health" >/dev/null 2>&1; then break; fi
  if ! kill -0 "$DOOR_PID" 2>/dev/null; then cat "$TMP/door.log"; fail "the door died on start"; fi
  sleep 1
done
curl -s --max-time 2 "$DOOR/health" >/dev/null || { cat "$TMP/door.log"; fail "the door did not answer in 60 s"; }
echo "answering"

export PRAX_DOOR="$DOOR"
step "prax status, and prax alone"
"$PRAX" status
"$PRAX"

step "prax add: a note, indexed at once"
cat > "$TMP/note.md" <<'EOF'
# A feedback delay network

A feedback delay network (FDN) is a reverberator built from parallel
delay lines whose outputs are mixed back into the inputs through an
orthogonal matrix, so energy is preserved and the response is dense.
EOF
"$PRAX" add "$(native "$TMP/note.md")" --title "A feedback delay network"

step "prax search finds it (hybrid: the first search fetches the embedder)"
"$PRAX" search feedback delay network | tee "$TMP/search.txt"
grep -qi "feedback delay network" "$TMP/search.txt" || fail "the note is not in the hits"

step "prax show, prax models, prax doctor"
"$PRAX" search feedback delay network --json | "$VBIN/python" -c '
import json, sys
hits = json.load(sys.stdin)
print(hits[0]["doc_id"])' > "$TMP/id.txt"
"$PRAX" show "$(cat "$TMP/id.txt")" | head -5
"$PRAX" models
"$PRAX" doctor

step "the door stops"
kill "$DOOR_PID"; wait "$DOOR_PID" 2>/dev/null || true
DOOR_PID=""
printf '\nquick start: ok\n'
