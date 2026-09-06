# prax

Personal research knowledge base — successor to the zoetrope external-disk
store. Named for the praxinoscope: the zoetrope's successor, same drum,
sharper image.

SQLite canonical store (FTS5 + sqlite-vec + graph edges), content-addressed
file archive, one FastAPI door, thin FastMCP agent interface. Target: a
Raspberry Pi / N100 home server behind Tailscale.

## Quick start

    pip install -e ".[dev]"
    pytest                          # smoke tests
    uvicorn prax.api:app --reload   # HTTP door on :8000
    # Claude Code picks up .mcp.json automatically in this repo

Architecture invariants: `CLAUDE.md`. Build plan: `docs/PLAN.md`.
Research and rationale: `docs/research.md`.
