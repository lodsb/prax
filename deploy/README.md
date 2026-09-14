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
| `desktop.ps1` | the desktop as the server, until the move: the door, llama-server and the worker as logon tasks that come back after a crash, the nightly backlog pass and the nightly backup as tasks (`docs/howto.md` 4b) |

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

## What to measure once it runs there

The door's resident memory after a day (`prax status` shows the host's
RAM, `systemctl status prax-door` the service's), and the search latency
over the network. Both go into `docs/PLAN.md` under the deployment shape.
