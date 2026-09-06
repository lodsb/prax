#!/usr/bin/env python3
"""Build ``tests/fixtures/zotero/`` from a real Zotero library.

Copies the chosen items (plus their parents, attachments and notes) into a
reduced ``zotero.sqlite`` and copies their ``storage/<KEY>/`` folders. The
source library is opened read-only from a private copy and never modified.

    python scripts/make_zotero_fixture.py R:/Zotero tests/fixtures/zotero \\
        --keys 9QRPZL68 4L6ILMZN HW3N7956 U263HF74 MCNISPSX 6FRF9XDC \\
               EZLSQSMG 97KAI26I ZRWHFMBJ 35UKKWHM VVJITI78 \\
        --skip RGI62LBN

``--keys`` may name regular items or attachments; ``--skip`` drops
individual attachments (to keep the fixture small). See docs/sources.md.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prax.importers.zotero import open_library

# Tables copied in full (small reference data) and tables filtered by item.
FULL_TABLES = (
    "version",
    "libraries",
    "itemTypes",
    "fields",
    "creatorTypes",
    "charsets",
    "collections",
    "relationPredicates",
)
ITEM_TABLES = {  # table -> column holding the item id
    "items": "itemID",
    "itemData": "itemID",
    "itemAttachments": "itemID",
    "itemNotes": "itemID",
    "itemCreators": "itemID",
    "itemTags": "itemID",
    "collectionItems": "itemID",
    "itemRelations": "itemID",
    "deletedItems": "itemID",
}


def select_items(zc: sqlite3.Connection, keys: list[str], skip: set[str]) -> set[int]:
    ids: set[int] = set()
    for key in keys:
        row = zc.execute("SELECT itemID FROM items WHERE key = ?", (key,)).fetchone()
        if row is None:
            raise SystemExit(f"no item with key {key}")
        item_id = row[0]
        parent = zc.execute(
            "SELECT parentItemID FROM itemAttachments WHERE itemID = ?"
            " UNION SELECT parentItemID FROM itemNotes WHERE itemID = ?",
            (item_id, item_id),
        ).fetchone()
        ids.add(parent[0] if parent and parent[0] else item_id)
    parents = set(ids)
    for pid in parents:
        for (cid,) in zc.execute(
            "SELECT itemID FROM itemAttachments WHERE parentItemID = ?"
            " UNION SELECT itemID FROM itemNotes WHERE parentItemID = ?",
            (pid, pid),
        ):
            ids.add(cid)
    skipped = (
        {
            r[0]
            for r in zc.execute(
                f"SELECT itemID FROM items WHERE key IN ({','.join('?' * len(skip))})",
                tuple(skip),
            )
        }
        if skip
        else set()
    )
    return ids - skipped


def build(zotero_dir: Path, out: Path, keys: list[str], skip: set[str]) -> None:
    with tempfile.TemporaryDirectory(prefix="prax-fixture-") as tmp:
        lib = open_library(zotero_dir, Path(tmp))
        zc = lib.con
        ids = select_items(zc, keys, skip)
        if out.exists():
            shutil.rmtree(out)
        (out / "storage").mkdir(parents=True)
        dst = sqlite3.connect(out / "zotero.sqlite")
        ph = ",".join("?" * len(ids))
        args = tuple(sorted(ids))

        def copy_table(name: str, where: str = "", params: tuple = ()) -> None:
            sql = zc.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)
            ).fetchone()[0]
            dst.execute(sql)
            rows = zc.execute(f"SELECT * FROM {name} {where}", params).fetchall()
            if rows:
                cols = ",".join("?" * len(rows[0]))
                dst.executemany(
                    f"INSERT INTO {name} VALUES ({cols})", [tuple(r) for r in rows]
                )

        for name in FULL_TABLES:
            copy_table(name)
        for name, col in ITEM_TABLES.items():
            copy_table(name, f"WHERE {col} IN ({ph})", args)
        copy_table(
            "itemDataValues",
            f"WHERE valueID IN (SELECT valueID FROM itemData WHERE itemID IN ({ph}))",
            args,
        )
        copy_table(
            "creators",
            "WHERE creatorID IN"
            f" (SELECT creatorID FROM itemCreators WHERE itemID IN ({ph}))",
            args,
        )
        copy_table(
            "tags",
            f"WHERE tagID IN (SELECT tagID FROM itemTags WHERE itemID IN ({ph}))",
            args,
        )
        dst.commit()
        dst.close()

        n_files = 0
        for (key,) in zc.execute(
            f"SELECT i.key FROM itemAttachments ia JOIN items i USING (itemID)"
            f" WHERE ia.itemID IN ({ph}) AND ia.path LIKE 'storage:%'",
            args,
        ):
            src_dir = lib.storage / key
            if src_dir.is_dir():
                shutil.copytree(src_dir, out / "storage" / key)
                n_files += 1
        lib.close()
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    print(f"{len(ids)} items, {n_files} storage folders, {size / 1e6:.1f} MB -> {out}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("zotero_dir", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--keys", nargs="+", required=True, help="item or attachment keys")
    ap.add_argument(
        "--skip", nargs="*", default=[], help="attachment keys to leave out"
    )
    a = ap.parse_args()
    build(a.zotero_dir, a.out, a.keys, set(a.skip))
    return 0


if __name__ == "__main__":
    sys.exit(main())
