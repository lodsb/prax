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
| `desktop.ps1` | the Windows desktop as the server, until the move: the door, llama-server and the worker as logon tasks under a headless console (nothing to close), the nightly backlog and maintenance passes and the nightly backup as tasks (`docs/howto.md` 4b) |
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
-ImageMaxTokens 1024`: **20.14 GB at 8 K a slot, 20.23 GB at 16 K**,
with about 0.9 GB for the desktop's own windows and 2.5 GB free.

Doubling the context cost 0.09 GB, not the 0.66 GB the obvious
arithmetic gives, and the model's metadata says why:
`full_attention_interval = 4`, with SSM parameters beside it. Only every
fourth layer keeps a cache that grows with the context — 10 of 40 — and
the other 30 hold a state of fixed size per slot. So the growing part is
`full_attention_layers × kv_heads × (key_length + value_length)` values
a token: 10 × 2 × 512 here, **10.6 KiB a token** at `q8_0` (f16 doubles
it), a quarter of what a model with attention in every layer would want.

| `-CtxPerSlot` × slots | KV cache | what `ask` may read |
|---|---|---|
| 8 K × 2 | 0.17 GB | 5,992 |
| **16 K × 2** (in use) | 0.33 GB | 14,184 |
| 32 K × 2 | 0.66 GB | 30,568 |
| 64 K × 2 | 1.33 GB | 63,336 |

Context is therefore cheap on this model and the limits are elsewhere:
prompt reading time (373 tokens a second cold, 1,207 warm — the answer
call reads everything kept, once) and how well a model with 3B active
parameters uses 30,000 tokens of assorted passages. The display shares
the card and froze once at 23.7 GB, so anything that leaves under 2 GB
free is worth testing while nothing else is open; two slots keep the
worker's passes from queueing behind a question. `n_ctx` under the model
in `prax.yaml` is what the reading budget is derived from, so it moves
with `-CtxPerSlot`; the model's own trained context (262,144) is nowhere
near any of this.

## What to measure once it runs there

The door's resident memory after a day (`prax status` shows the host's
RAM, `systemctl status prax-door` the service's), and the search latency
over the network. Both go into `docs/PLAN.md` under the deployment shape.
