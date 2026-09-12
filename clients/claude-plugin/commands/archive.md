---
description: Keep the raw record — this project's Claude Code sessions (what was said, not what was run) and the agent's memory files — in the prax library
argument-hint: "[--since YYYY-MM-DD] [--dry-run]"
---

Archive the raw record of working with Claude on this project. This is
the counterpart of `/prax:remember`: that keeps what was decided, this
keeps what was said, so a later "what did we say about…" is a search.

1. Say what will go, before anything is sent: the project's Claude Code
   sessions from `~/.claude/projects/<project>/` (the person's turns and
   the assistant's prose; tool calls, results, thinking and side chains
   are left out) and the memory files Claude Code keeps for the project.
   Transcripts can carry what was pasted into them; if `$ARGUMENTS` has
   no `--dry-run`, ask once whether to go ahead unless the project's
   `.prax-project` already lists `archive: [transcripts, memory]`.
2. Run, with the Bash tool (the `prax` command, or `"$PRAX_PYTHON" -m
   prax_cli.main …`):

       prax import claude . --refresh $ARGUMENTS

   and, when the memory directory exists:

       prax import project ~/.claude/projects/<mangled project path>/memory --name <project>-memory --refresh

   (`prax import claude` prints the sessions it found first; with
   `--dry-run` nothing is sent.)
3. Report the summary lines: sessions added, refreshed, already there;
   memory files likewise. Offer to add `archive: [transcripts, memory]`
   to `.prax-project` so the session-end hook does this from now on —
   and leave that choice to the user.
