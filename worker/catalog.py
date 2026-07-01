"""Brickforge catalog — in-memory LDraw parts + colors database.

Built once at container start by reading the LDraw library that's already
installed at /root/ldraw in the worker image (downloaded during image build).

Why in-memory JSON instead of Supabase:
  - Zero new infrastructure dependencies for v2 Week 1
  - 17K parts + 50 colors fit easily in process memory (~10MB)
  - Lookups are O(1) hashmap; perfect for the tight loop of LLM tool calls
  - When we want shared catalog (e.g. between Modal worker and Next.js
    server), we can later sync this JSON to Supabase. Not needed yet.

Provides the building blocks for the three LLM tools:
  - lookup_part(query: str)         — fuzzy search by name/keyword/dimensions
  - find_similar_parts(ldraw_id)    — alternatives in the same shape family
  - check_assembly_validity(...)    — sanity-check a proposed sub-assembly
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, asdict
from functools import lru_cache
from pathlib import Path


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Color:
    ldraw_code: int
    name: str
    hex: str
    is_transparent: bool


@dataclass(frozen=True)
class Part:
    ldraw_id: str               # e.g. "3001"
    name: str                   # "Brick 2 x 4"
    category: str | None        # rough category derived from name
    width_studs: int | None     # parsed from name when possible
    length_studs: int | None
    is_official: bool           # True if from ldraw/parts/, False from unofficial


# ---------------------------------------------------------------------------
# Catalog (loaded once, immutable)
# ---------------------------------------------------------------------------

@dataclass
class Catalog:
    parts: dict[str, Part]                  # ldraw_id -> Part
    parts_by_name_tokens: dict[str, set[str]]  # token -> set of ldraw_ids
    colors_by_code: dict[int, Color]
    colors_by_name: dict[str, Color]        # lowercased name -> Color


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

_COLOR_RE = re.compile(
    r"0\s+!COLOUR\s+(\S+)\s+CODE\s+(\d+)\s+VALUE\s+(#[0-9A-Fa-f]+)\s+EDGE\s+(#[0-9A-Fa-f]+)(.*)$"
)

# Match dimensions in part names like "Brick 2 x 4" or "Plate 1 x 8".
_DIM_RE = re.compile(r"\b(\d+)\s*[xX]\s*(\d+)\b")

# Common LEGO part categories — heuristic from the first words of the name.
_CATEGORY_HINTS = {
    "brick": "Brick",
    "plate": "Plate",
    "tile": "Tile",
    "slope": "Slope",
    "wedge": "Wedge",
    "panel": "Panel",
    "arch": "Arch",
    "hinge": "Hinge",
    "minifig": "Minifig",
    "technic": "Technic",
    "wheel": "Wheel",
    "windscreen": "Windscreen",
    "windshield": "Windshield",
    "window": "Window",
    "door": "Door",
    "bar": "Bar",
    "antenna": "Antenna",
}


def _tokenize(name: str) -> set[str]:
    """Lowercase word tokens, useful for keyword search."""
    return {tok for tok in re.split(r"[^a-z0-9]+", name.lower()) if tok}


def _categorize(name: str) -> str | None:
    lower = name.lower()
    for hint, cat in _CATEGORY_HINTS.items():
        if hint in lower:
            return cat
    return None


def _parse_dimensions(name: str) -> tuple[int | None, int | None]:
    m = _DIM_RE.search(name)
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


def _parse_colors(ldconfig: Path) -> list[Color]:
    out: list[Color] = []
    with ldconfig.open(errors="ignore") as f:
        for line in f:
            m = _COLOR_RE.match(line.strip())
            if not m:
                continue
            name, code, hex_val, _edge, rest = m.groups()
            out.append(
                Color(
                    ldraw_code=int(code),
                    name=name,
                    hex=hex_val,
                    is_transparent="ALPHA" in rest.upper(),
                )
            )
    return out


def _parse_parts(parts_lst: Path, official: bool = True) -> list[Part]:
    out: list[Part] = []
    if not parts_lst.exists():
        return out
    with parts_lst.open(errors="ignore") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            tokens = re.split(r"\s+", line, maxsplit=1)
            if len(tokens) != 2:
                continue
            file_name, desc = tokens
            ldraw_id = file_name.replace(".dat", "")
            desc = desc.strip()
            w, l = _parse_dimensions(desc)
            out.append(
                Part(
                    ldraw_id=ldraw_id,
                    name=desc,
                    category=_categorize(desc),
                    width_studs=w,
                    length_studs=l,
                    is_official=official,
                )
            )
    return out


# ---------------------------------------------------------------------------
# Catalog factory
# ---------------------------------------------------------------------------

# All the places we might find the LDraw library. Different zip package
# layouts and different Docker install paths land it in different places, so
# we try a broad list rather than guess.
_LDRAW_ROOT_CANDIDATES = [
    Path("/root/ldraw"),                 # Modal image, direct unzip
    Path("/root/ldraw/ldraw"),           # Modal image, nested-in-zip layout
    Path("/opt/ldraw"),
    Path("/opt/legogpt/ldraw"),          # in case LegoGPT pulled it into its dir
    Path.home() / "ldraw",
    Path.home() / "ldraw" / "ldraw",
]


def _is_ldraw_root(p: Path) -> bool:
    return (p / "LDConfig.ldr").exists() and (p / "parts.lst").exists()


def find_ldraw_root() -> Path:
    # Environment override wins if set.
    env_path = os.environ.get("LDRAW_LIBRARY_PATH")
    if env_path:
        p = Path(env_path)
        if _is_ldraw_root(p):
            return p
        # Also try the nested layout under the env-provided root.
        if _is_ldraw_root(p / "ldraw"):
            return p / "ldraw"

    for cand in _LDRAW_ROOT_CANDIDATES:
        if _is_ldraw_root(cand):
            return cand

    # No luck. Dump helpful diagnostics so we can see what IS in the container.
    diag: list[str] = [
        "Couldn't find an LDraw library.",
        f"LDRAW_LIBRARY_PATH env var: {env_path!r}",
        "Tried the following candidates:",
    ]
    for cand in _LDRAW_ROOT_CANDIDATES:
        diag.append(f"  {cand}  exists={cand.exists()}")
        if cand.exists() and cand.is_dir():
            try:
                children = sorted(x.name for x in cand.iterdir())[:15]
                diag.append(f"    contents (first 15): {children}")
            except OSError as exc:
                diag.append(f"    (couldn't list: {exc})")
    # Also list /root, /opt, and $HOME so we can see what actually shipped.
    for probe in (Path("/root"), Path("/opt"), Path.home()):
        if probe.exists() and probe.is_dir():
            try:
                children = sorted(x.name for x in probe.iterdir())[:20]
                diag.append(f"  {probe} contents (first 20): {children}")
            except OSError:
                pass

    raise FileNotFoundError("\n".join(diag))


@lru_cache(maxsize=1)
def load_catalog() -> Catalog:
    """Load the catalog from /root/ldraw. Cached after first call."""
    root = find_ldraw_root()

    colors = _parse_colors(root / "LDConfig.ldr")
    parts = _parse_parts(root / "parts.lst", official=True)

    # Build secondary indexes
    parts_by_id = {p.ldraw_id: p for p in parts}
    parts_by_name_tokens: dict[str, set[str]] = {}
    for p in parts:
        for tok in _tokenize(p.name):
            parts_by_name_tokens.setdefault(tok, set()).add(p.ldraw_id)

    colors_by_code = {c.ldraw_code: c for c in colors}
    colors_by_name = {c.name.lower(): c for c in colors}

    return Catalog(
        parts=parts_by_id,
        parts_by_name_tokens=parts_by_name_tokens,
        colors_by_code=colors_by_code,
        colors_by_name=colors_by_name,
    )


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def part_to_dict(p: Part) -> dict:
    return asdict(p)


def color_to_dict(c: Color) -> dict:
    return asdict(c)
