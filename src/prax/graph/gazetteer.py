"""Where a GPS position is, without a network call: the nearest town of
GeoNames' ``cities1000`` (every populated place of a thousand people or
more, some 160,000), with its first-order region and its country
(``docs/biography.md`` §7; the ``geocode`` step, ``prax.steps.photos``).

GeoNames is CC BY 4.0 (https://www.geonames.org). Its three files are
fetched once into ``<models dir>/geonames/`` (``prax.ml.fetch``; refused
under ``PRAX_OFFLINE``) and read into memory by the worker, never by the
door: some 60 MB, held while the worker runs (171,654 towns read in
three seconds, 2026-10-26).

A town is the nearest one within ``REACH_KM``. Past it (at sea, in the
mountains, on a plane) the answer is nothing rather than the nearest
town a hundred kilometres away. The nearest is not always the town a
person would name: a photo taken in a suburb names the suburb, whose
region and country are right either way.
"""

from __future__ import annotations

import io
import math
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

BASE = "https://download.geonames.org/export/dump/"
CITIES = "cities1000"
ADMIN1 = "admin1CodesASCII.txt"
COUNTRIES = "countryInfo.txt"
# what a reading is: another source, another reach, is a new version
VERSION = 1
REACH_KM = 30.0
# not a town of its own: a section of a city (Lisbon's Intendente,
# Berlin's Mitte, which were the nearest "towns" to their centres), and
# places abandoned, destroyed or historical
LEFT_OUT = frozenset({"PPLX", "PPLQ", "PPLW", "PPLH", "PPLCH"})
_KM_PER_DEGREE = 111.2
_CELL = 1.0  # degrees: a cell and its eight neighbours cover REACH_KM
_EARTH_KM = 6371.0


@dataclass(frozen=True)
class Place:
    """A town and what it lies in: ``region`` and ``country`` are names,
    ``""`` where GeoNames gives none; ``km`` is how far the position is
    from the town's own point."""

    name: str
    geonameid: int
    region: str
    country: str
    cc: str
    km: float


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """The great-circle distance between two positions (haversine)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * _EARTH_KM * math.asin(min(1.0, math.sqrt(a)))


class Gazetteer:
    """The towns in cells of ``_CELL`` degrees, and the names of the
    regions and countries they lie in."""

    def __init__(
        self,
        towns: list[tuple[int, str, float, float, str, str]],
        regions: dict[str, str],
        countries: dict[str, str],
    ) -> None:
        # a town: (geonameid, name, lat, lon, country code, admin1 code)
        self.towns = towns
        self.regions = regions
        self.countries = countries
        self._cells: dict[tuple[int, int], list[int]] = {}
        for i, t in enumerate(towns):
            self._cells.setdefault(self._cell(t[2], t[3]), []).append(i)

    @staticmethod
    def _cell(lat: float, lon: float) -> tuple[int, int]:
        return math.floor(lat / _CELL), math.floor(lon / _CELL)

    def nearest(
        self, lat: float, lon: float, *, reach_km: float = REACH_KM
    ) -> Place | None:
        """The nearest town within ``reach_km``, or None."""
        cy, cx = self._cell(lat, lon)
        best: tuple[float, int] | None = None
        # past 60 degrees a degree of longitude is under 56 km, so the
        # cells beside are searched wider to keep REACH_KM covered
        wide = 1 + int(1 / max(0.1, math.cos(math.radians(min(abs(lat), 89)))))
        for dy in (-1, 0, 1):
            for dx in range(-wide, wide + 1):
                x = (cx + dx + 180) % 360 - 180  # across the date line
                for i in self._cells.get((cy + dy, x), ()):
                    t = self.towns[i]
                    if abs(t[2] - lat) * _KM_PER_DEGREE > reach_km:
                        continue
                    km = distance_km(lat, lon, t[2], t[3])
                    if km <= reach_km and (best is None or km < best[0]):
                        best = (km, i)
        if best is None:
            return None
        km, i = best
        gid, name, _, _, cc, admin1 = self.towns[i]
        return Place(
            name=name,
            geonameid=gid,
            region=self.regions.get(f"{cc}.{admin1}", ""),
            country=self.countries.get(cc, ""),
            cc=cc,
            km=round(km, 1),
        )


def parse_towns(
    lines: Iterable[str],
) -> list[tuple[int, str, float, float, str, str]]:
    """The rows of a GeoNames cities file, line by line: id, name,
    position, country and region codes, without ``LEFT_OUT``."""
    out = []
    for line in lines:
        f = line.rstrip("\n").split("\t")
        if len(f) < 11 or not f[0].isdigit() or f[7] in LEFT_OUT:
            continue
        try:
            lat, lon = float(f[4]), float(f[5])
        except ValueError:
            continue
        out.append((int(f[0]), f[1], lat, lon, f[8], f[10]))
    return out


def parse_regions(text: str) -> dict[str, str]:
    """``admin1CodesASCII.txt``: ``CC.code`` to the region's name."""
    out = {}
    for line in text.splitlines():
        f = line.split("\t")
        if len(f) >= 2 and "." in f[0]:
            out[f[0]] = f[1]
    return out


def parse_countries(text: str) -> dict[str, str]:
    """``countryInfo.txt``: the ISO code to the country's English name."""
    out = {}
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        f = line.split("\t")
        if len(f) >= 5 and len(f[0]) == 2:
            out[f[0]] = f[4]
    return out


def files_dir() -> Path:
    from prax.ml import fetch

    return fetch.models_dir() / "geonames"


def available() -> bool:
    """Whether the three files are here, or may be fetched."""
    from prax.ml import fetch

    d = files_dir()
    have = all((d / n).exists() for n in (f"{CITIES}.zip", ADMIN1, COUNTRIES))
    return have or not fetch.offline()


def load() -> Gazetteer:
    """The gazetteer from its files, each fetched on first use."""
    from prax.ml import fetch

    d = files_dir()
    paths = {}
    for name in (f"{CITIES}.zip", ADMIN1, COUNTRIES):
        dest = d / name
        if not dest.exists():
            fetch.download(BASE + name, dest)
        paths[name] = dest
    with (
        zipfile.ZipFile(paths[f"{CITIES}.zip"]) as z,
        io.TextIOWrapper(z.open(f"{CITIES}.txt"), encoding="utf-8") as f,
    ):
        towns = parse_towns(f)
    return Gazetteer(
        towns,
        parse_regions(paths[ADMIN1].read_text("utf-8")),
        parse_countries(paths[COUNTRIES].read_text("utf-8")),
    )


def stamp() -> str:
    """Which reading placed it, for ``meta.place.by``."""
    return f"geonames/{CITIES}/{VERSION}"
