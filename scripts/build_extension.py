#!/usr/bin/env python3
"""Pack the browser extension: one zip that Firefox and Waterfox take as an
.xpi and Chrome as a zip, written to dist/.

    python scripts/build_extension.py            # dist/prax-capture-<version>.xpi
    python scripts/build_extension.py --zip      # …and a .zip with the same content

Development does not need this: load the extension/ folder unpacked
(docs/extension.md). The .xpi is for a permanent install in a browser that
allows unsigned add-ons (Waterfox, Firefox Developer Edition, ESR) or for
signing through Mozilla's self-distribution channel.
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "extension"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--zip", action="store_true", help="also write a .zip")
    ap.add_argument("--out", type=Path, default=ROOT / "dist")
    a = ap.parse_args()
    manifest = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))
    a.out.mkdir(parents=True, exist_ok=True)
    stem = f"prax-capture-{manifest['version']}"
    files = sorted(
        p for p in EXT.rglob("*") if p.is_file() and not p.name.startswith(".")
    )
    written = []
    for suffix in [".xpi", *([".zip"] if a.zip else [])]:
        target = a.out / (stem + suffix)
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
            for f in files:
                z.write(f, f.relative_to(EXT).as_posix())
        written.append(target)
    for t in written:
        print(f"{t} ({t.stat().st_size} bytes, {len(files)} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
