"""`prax token`: the named API tokens beside the administrator's (stage U).

A named token sees only the modules it is given and, unless told, no
document marked or suspected personal; it may call only the routes the
MCP tools use. The secret is printed once, when the token is made, and is
kept nowhere: the door stores its hash."""

from __future__ import annotations

import json
from typing import Any

from prax.client import Door

from . import out

ACTIONS = ("add", "list", "remove")


def token(door: Door, a: Any) -> int:
    if a.action == "list":
        rows = door.get_json("/tokens")["tokens"]
        if a.json:
            print(json.dumps(rows, indent=1))
            return 0
        if not rows:
            out.say("no named tokens: only the administrator's (PRAX_TOKEN)")
            return 0
        for r in rows:
            sees = ", ".join(r["domains"]) if r["domains"] else "every module"
            personal = "personal too" if r["personal"] else "no personal documents"
            out.say(
                f"{out.bold(r['name'])}   {sees} · {personal}"
                + out.dim(f" · made {r['created_at']}")
            )
        return 0
    if not a.name:
        out.fail("which token?", f"prax token {a.action} NAME")
        return 2
    if a.action == "remove":
        door.delete_json(f"/tokens/{a.name}")
        out.say(f"removed {a.name}: its secret opens nothing any more")
        return 0
    got = door.post_json(
        "/tokens",
        {"name": a.name, "domains": a.domain or None, "personal": a.personal},
    )
    if a.json:
        print(json.dumps(got, indent=1))
        return 0
    out.say(f"token {out.bold(got['name'])} made. Its secret, shown this once:")
    print(got["secret"])
    out.say(
        out.dim(
            "Give it to the client as PRAX_TOKEN (the MCP server: in its env)."
            " Lost, it cannot be shown again: remove the token and make another."
        )
    )
    return 0
