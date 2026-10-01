"""Packs: each domain of knowledge in one package (docs/packs.md).

``PACKS`` lists the manifests in the order their knowledge is composed:
craft's sameness cases before studio's, as they stood in one file until
2026-10-01, so the rule every judge of a pair reads did not change. The
lookups below are what the core's lists read: the knowledge of every
pack, the capability of the packs a host names in ``packs:``.

Nothing here imports anything of prax but the manifests, which are data:
the thin client, the config and the store read it at no cost. A pack's
code is imported when one of its parts is first used.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from prax.packs.base import Pack
from prax.packs.computing import MANIFEST as COMPUTING
from prax.packs.craft import MANIFEST as CRAFT
from prax.packs.maths import MANIFEST as MATHS
from prax.packs.research import MANIFEST as RESEARCH
from prax.packs.society import MANIFEST as SOCIETY
from prax.packs.studio import MANIFEST as STUDIO

ROOT = Path(__file__).parent
PACKS: tuple[Pack, ...] = (RESEARCH, CRAFT, STUDIO, COMPUTING, SOCIETY, MATHS)


def by_name(name: str) -> Pack:
    for pack in PACKS:
        if pack.name == name:
            return pack
    raise KeyError(f"no pack named {name!r}; the packs are {names()}")


def names() -> list[str]:
    return [p.name for p in PACKS]


def home(pack: Pack) -> Path:
    return ROOT / pack.name


# ------------------------------------------------------------ knowledge


def ontology_files() -> list[Path]:
    """Every pack's module files: composed on every host (invariant 9)."""
    return [home(p) / f for p in PACKS for f in p.ontology]


def sameness_files() -> list[Path]:
    """Every pack's cases of "the same thing", in the order of ``PACKS``."""
    return [home(p) / p.sameness for p in PACKS if p.sameness]


def rules_file(name: str) -> Path | None:
    """The domain rules a pack suggests; None when it suggests none."""
    pack = by_name(name)
    return home(pack) / pack.rules if pack.rules else None


# ----------------------------------------------------------- capability
# names first: read by the thin client and the store, so no pack code


def step_homes() -> dict[str, str]:
    """Each pack step and the module that holds it."""
    return {s: m for p in PACKS for s, m in p.steps.items()}


def watched() -> tuple[str, ...]:
    return tuple(s for p in PACKS for s in p.watched)


def readings() -> dict[str, str]:
    """Each pack reading and the step whose model it runs."""
    return {r: s for p in PACKS for r, s in p.readings.items()}


def kinds() -> tuple[str, ...]:
    return tuple(k for p in PACKS for k in p.kinds)


def aside() -> tuple[str, ...]:
    return tuple(k for p in PACKS for k in p.aside)


def setting_sections() -> tuple[str, ...]:
    return tuple(p.settings for p in PACKS if p.settings)


def running(chosen: Iterable[str]) -> list[Pack]:
    """The packs a host names (``packs:`` in prax.yaml), checked."""
    out = []
    for name in chosen:
        out.append(by_name(str(name)))
    return out


def _resolve(path: str) -> Any:
    module, _, attr = path.partition(":")
    return getattr(importlib.import_module(module), attr)


def extractors(chosen: Iterable[str]) -> list[Any]:
    """The extractors of the packs a host runs, imported now."""
    return [x for p in running(chosen) for path in p.extractors for x in _resolve(path)]


def tools(chosen: Iterable[str]) -> dict[str, Any]:
    """The surfer's tools of the packs a host runs, imported now."""
    return {
        name: _resolve(path) for p in running(chosen) for name, path in p.tools.items()
    }


def tool_help(chosen: Iterable[str]) -> list[str]:
    """The prompt lines of the surfer's tools of the packs a host runs."""
    return [
        p.tool_help[n] for p in running(chosen) for n in p.tools if n in p.tool_help
    ]


def duplicates(core: dict[str, Iterable[str]]) -> list[str]:
    """Names a pack repeats: of the core (``core``, by what they name) or
    of another pack. The registries refuse a duplicate at start."""
    seen: dict[str, set[str]] = {k: set(v) for k, v in core.items()}
    out = []
    for p in PACKS:
        for what, names_ in (
            ("steps", p.steps),
            ("readings", p.readings),
            ("kinds", p.kinds),
            ("tools", p.tools),
        ):
            have = seen.setdefault(what, set())
            for n in names_:
                if n in have:
                    out.append(f"{p.name}: {what} {n!r}")
                have.add(n)
    return out
