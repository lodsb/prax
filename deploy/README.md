# Deploying the door on the board

The board holds the store and runs the door; the machine with the GPU
does the model work through it. This folder is everything the board
needs, in the order it needs it (the reasoning: `docs/howto.md` 6).

| file | what |
|---|---|
| `install.sh` | on the board, as root: a `prax` user, a venv with `prax[serve]`, the data directory, the service |
| `prax-door.service` | the systemd unit: the door on the private address, restarted on failure, capped at 1.5 GB (invariant 7 says under 1) |
| `door.env.example` | the two secrets-and-addresses the unit reads: `PRAX_BIND`, `PRAX_TOKEN` |
| `prax.board.yaml` | the board's `prax.yaml`: an int8 index, no model steps of its own |
| `worker.ps1` | on the Windows desktop: start or stop the worker that drains the board's queue, as a detached process |
| `desktop.ps1` | the Windows desktop as the server, until the move: the door, llama-server and the worker as logon tasks that come back after a crash, the nightly backlog and maintenance passes and the nightly backup as tasks (`docs/howto.md` 4b) |
| `desktop.sh` | the same on Linux (systemd user units and timers) and macOS (launchd agents): `install`, `start`, `stop`, `status`, `uninstall`; `scripts/llama_server.sh` is the launcher it uses |

## The move, step by step

1. **Copy the store** to the board's SSD: `prax.db` (with SQLite's online
   backup so the WAL is folded in, `docs/howto.md` 7), the `archive/`
   tree, and the `vectors-*.usearch` files if you keep the desktop's
   `f16` index. Nothing else in the data directory is needed.
2. **On the board**, from a checkout of this repository:

       sudo bash deploy/install.sh /srv/prax

   which makes the user, the venv, copies `prax.board.yaml` to
   `/srv/prax/prax.yaml`, writes `/etc/prax/door.env` from the example
   (edit it: the address the board has on your private network, and a
   token from `python -c "import secrets; print(secrets.token_urlsafe(32))"`),
   installs the unit and starts it.
3. **Check it**: `journalctl -u prax-door -f`, then from the desktop

       prax --door http://<board>:8000 --token <token> doctor

4. **The worker** on the desktop, pointed at the board:

       deploy\worker.ps1 -Door http://<board>:8000 -Token <token>

   It parses, titles, extracts and embeds whatever the board took in,
   with the desktop's models, and uploads the desktop's `Downloads/prax-inbox`.
5. **Claude Code** on the desktop: `PRAX_DOOR` and `PRAX_TOKEN` in the
   shell that launches it (`.mcp.json` passes them through).
6. **The browser extension**: the board's address as the server and the
   token; the board's `prax.yaml` lists the extension's origin under
   `door.cors_origins` (the example shows where).

## The index on a board

A `f16` index of 878,000 vectors is 768 MB and is memory-mapped, so it
costs little RAM until it is searched, and then only the pages a search
touches. The `i8` index is half that at recall 0.93. `prax.board.yaml`
asks for `i8`, which takes effect when vectors are written: either copy
the desktop's `f16` files and accept their size, or copy none and let the
worker embed everything into a fresh `i8` index (878,000 chunks at 80
per second on the desktop's GPU is about three hours, sent to the board
in batches of 200).

## The card on the desktop, while it is the server

llama-server's share of a 24 GB 4090, measured 2026-09-15 with
Qwen3.6-35B-A3B UD-Q4_K_S, its projector, `-CpuMoe 2 -UBatch 256
-ImageMaxTokens 1024`: **20.1 GB at 8 K a slot**, of which the KV cache
is 0.66 GB, with about 0.9 GB left for the desktop's own windows and
2.5 GB free. The cache is the part that grows with what `ask` may read,
and its cost is arithmetic: `layers × kv_heads × (key_length +
value_length)` values a token, 42.5 KiB here at `q8_0` (f16 doubles it).

| `-CtxPerSlot` × slots | KV cache | the card | what `ask` may read |
|---|---|---|---|
| 8 K × 2 | 0.66 GB | 20.1 GB | 5,992 |
| **16 K × 2** (in use) | 1.33 GB | 20.8 GB | 14,184 |
| 24 K × 2 | 2.0 GB | 21.5 GB | 21,992 |
| 32 K × 2 | 2.66 GB | 22.1 GB | 30,568 |
| 32 K × 1 | 1.33 GB | 20.8 GB | 30,568 |

The display shares the card: it froze once at 23.7 GB, so a configuration
that leaves under 2 GB is one to test while nothing else is open. One
slot is enough only if the worker's passes may queue behind a question.
`n_ctx` under the model in `prax.yaml` is what the reading budget is
derived from, so it moves with `-CtxPerSlot`, and the model's own trained
context (262,144 here) is nowhere near the limit — the card is.

## What to measure once it runs there

The door's resident memory after a day (`prax status` shows the host's
RAM, `systemctl status prax-door` the service's), and the search latency
over the network. Both go into `docs/PLAN.md` under the deployment shape.
