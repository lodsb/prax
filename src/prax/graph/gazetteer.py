"""Where a GPS position is, without a network call: the nearest town of
GeoNames' ``cities1000`` (every populated place of a thousand people or
more, some 160,000), with its first-order region and its country
(``docs/biography.md`` §7; the ``geocode`` step, ``prax.steps.photos``).

GeoNames is CC BY 4.0 (https://www.geonames.org). Its three files are
fetched once into ``<models dir>/geonames/`` (``prax.ml.fetch``; refused
under ``PRAX_OFFLINE``) and read into memory by the worker, never by the
door: some 60 MB, held while the worker runs (171,654 towns read in
three seconds, 2026-10-26).

A place's names in other languages are GeoNames' alternate names, one
file a country (``alternatenames/<CC>.zip``), fetched the first time a
picture of that country asks for a language and kept beside the others;
only the names of the towns, regions and country of that file, in the
languages asked, are held (``Gazetteer.names``). A country whose file
cannot be had answers with no names, and the place keeps its own.

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
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

BASE = "https://download.geonames.org/export/dump/"
CITIES = "cities1000"
ADMIN1 = "admin1CodesASCII.txt"
COUNTRIES = "countryInfo.txt"
# what a reading is: another source, another reach, is a new version.
# 2 (2026-10-28): placed again so each picture states where its town is,
# and a second town of a name is a node of its own (store.apply_place)
# 3 (2026-10-28): placed again for the places' names in other languages,
# which the pictures placed at 2 have none of; the same answer keeps its
# facts (store.apply_place), so only the names and the stamps are new
# 4 (2026-10-28): placed again to join the towns split in two by a region
# renamed into another language (store.graph.geo._gazetteer_ends)
VERSION = 4
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
    region_id: int = 0
    country_id: int = 0


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
        *,
        ids: dict[str, int] | None = None,
        alternates: Callable[[str], Iterable[str]] | None = None,
    ) -> None:
        # a town: (geonameid, name, lat, lon, country code, admin1 code)
        self.towns = towns
        self.regions = regions
        self.countries = countries
        # a region's (``CC.code``) or a country's (``CC``) geonameid
        self.ids = ids or {}
        # a country's alternate names file, line by line (``load``)
        self._alternates = alternates
        self._names: dict[tuple[str, frozenset[str]], dict[tuple[int, str], str]] = {}
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
            region_id=self.ids.get(f"{cc}.{admin1}", 0),
            country_id=self.ids.get(cc, 0),
        )

    def names(self, place: Place, langs: Iterable[str]) -> dict[str, dict[str, str]]:
        """The town's, region's and country's names in each of ``langs``
        that GeoNames knows (``{lang: {"town": …, "region": …, "country":
        …}}``), leaving out a name the place already has; empty when the
        country's file cannot be had."""
        wanted = frozenset(lang for lang in langs if lang)
        if not wanted or self._alternates is None or not place.cc:
            return {}
        key = (place.cc, wanted)
        if key not in self._names:
            ids = {t[0] for t in self.towns if t[4] == place.cc}
            ids |= {g for k, g in self.ids.items() if k.split(".")[0] == place.cc}
            try:
                self._names[key] = parse_alternates(
                    self._alternates(place.cc), ids, wanted
                )
            except (OSError, zipfile.BadZipFile, KeyError):
                self._names[key] = {}
        held = self._names[key]
        out: dict[str, dict[str, str]] = {}
        for lang in sorted(wanted):
            got = {
                part: held[(gid, lang)]
                for part, gid, own in (
                    ("town", place.geonameid, place.name),
                    ("region", place.region_id, place.region),
                    ("country", place.country_id, place.country),
                )
                if gid and (gid, lang) in held and held[(gid, lang)] != own
            }
            if got:
                out[lang] = got
        return out


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


def parse_alternates(
    lines: Iterable[str], ids: set[int], langs: frozenset[str]
) -> dict[tuple[int, str], str]:
    """A country's alternate names file, line by line: for each of ``ids``
    and ``langs``, the name GeoNames prefers in that language, else its
    first one, never a colloquial or historic one."""
    out: dict[tuple[int, str], str] = {}
    preferred: set[tuple[int, str]] = set()
    for line in lines:
        f = line.rstrip("\n").split("\t")
        if len(f) < 4 or not f[1].isdigit() or f[2] not in langs:
            continue
        gid = int(f[1])
        if gid not in ids or not f[3]:
            continue
        flags = f + [""] * (8 - len(f))
        if flags[6] == "1" or flags[7] == "1":  # colloquial, historic
            continue
        key = (gid, f[2])
        if flags[4] == "1" and key not in preferred:
            out[key] = f[3]
            preferred.add(key)
        elif key not in out:
            out[key] = f[3]
    return out


def parse_region_ids(text: str) -> dict[str, int]:
    """``admin1CodesASCII.txt``: ``CC.code`` to the region's geonameid."""
    out = {}
    for line in text.splitlines():
        f = line.split("\t")
        if len(f) >= 4 and "." in f[0] and f[3].strip().isdigit():
            out[f[0]] = int(f[3])
    return out


def parse_country_ids(text: str) -> dict[str, int]:
    """``countryInfo.txt``: the ISO code to the country's geonameid."""
    out = {}
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        f = line.split("\t")
        if len(f) >= 17 and len(f[0]) == 2 and f[16].strip().isdigit():
            out[f[0]] = int(f[16])
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
    try:
        with (
            zipfile.ZipFile(paths[f"{CITIES}.zip"]) as z,
            io.TextIOWrapper(z.open(f"{CITIES}.txt"), encoding="utf-8") as f,
        ):
            towns = parse_towns(f)
    except (zipfile.BadZipFile, KeyError) as exc:
        paths[f"{CITIES}.zip"].unlink(missing_ok=True)  # fetched again next time
        raise OSError(f"{CITIES}.zip is not GeoNames' file: {exc}") from exc
    regions_text = paths[ADMIN1].read_text("utf-8")
    countries_text = paths[COUNTRIES].read_text("utf-8")
    return Gazetteer(
        towns,
        parse_regions(regions_text),
        parse_countries(countries_text),
        ids={**parse_region_ids(regions_text), **parse_country_ids(countries_text)},
        alternates=_alternates_of,
    )


def _alternates_of(cc: str) -> list[str]:
    """A country's alternate names file as lines, fetched the first time."""
    from prax.ml import fetch

    dest = files_dir() / "alternatenames" / f"{cc}.zip"
    if not dest.exists():
        fetch.download(f"{BASE}alternatenames/{cc}.zip", dest)
    try:
        with zipfile.ZipFile(dest) as z:
            return z.read(f"{cc}.txt").decode("utf-8").splitlines()
    except (zipfile.BadZipFile, KeyError):
        dest.unlink(missing_ok=True)  # fetched again next time
        raise


def stamp() -> str:
    """Which reading placed it, for ``meta.place.by``."""
    return f"geonames/{CITIES}/{VERSION}"
