"""The `prax` command: a client of the door, like the browser extension
beside it in `clients/` and the MCP server. It opens no database;
everything it shows came over HTTP (`prax.client`).

The command itself is `prax_cli.main:main`; nothing is exported here, so
that `prax_cli.main` always means the module.
"""

from __future__ import annotations
