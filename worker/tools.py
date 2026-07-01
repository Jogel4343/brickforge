"""The three LLM-callable tools for the v2 pipeline.

These are the ONLY way the LLM gets to know about real parts. The system
prompt forbids the LLM from naming parts in free text — every part_id in
the output must come from a `lookup_part` result. See the v2 spec for the
three-layer hallucination defense.

Tools:
  - lookup_part(query, ...)              -> list[PartHit]
  - find_similar_parts(ldraw_id, n)      -> list[PartHit]
  - check_assembly_validity(parts)       -> ValidationResult
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from worker.catalog import Catalog, Part, load_catalog


# ---------------------------------------------------------------------------
# Output shapes (kept JSON-friendly for direct return to the LLM)
# ---------------------------------------------------------------------------

@dataclass
class PartHit:
    ldraw_id: str
    name: str
    category: str | None
    width_studs: int | None
    length_studs: int | None
    score: float           # 1.0 = exact match; 0.0 = irrelevant

    def to_dict(self) -> dict:
        return {
            "ldraw_id": self.ldraw_id,
            "name": self.name,
            "category": self.category,
            "width_studs": self.width_studs,
            "length_studs": self.length_studs,
            "score": round(self.score, 3),
        }


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "errors": self.errors, "warnings": self.warnings}


# ---------------------------------------------------------------------------
# Ranking helpers
# ---------------------------------------------------------------------------

# Name tokens that mark a part as a decorated / sticker / printed variant.
# Heavily penalized so plain functional parts float to the top when the LLM
# asks for something generic like "brick 2x4".
_DECORATION_PENALTY_TOKENS = {
    "pattern", "sticker", "printed", "print", "decorated",
    "hieroglyphs", "logo", "badge", "stripes", "emblem",
    "background", "braille", "dots",
}

# Product-line tokens that mark parts NOT compatible with regular System bricks.
# Duplo (2x scale), Quatro (4x scale), Fabuland (large chunky), etc. If the user
# didn't ask for these by name, keep them off the top of the list.
_PRODUCT_LINE_PENALTY_TOKENS = {
    "duplo", "quatro", "fabuland", "znap", "scala", "mursten", "train",
    "primo", "belville",
}

# Specialization tokens indicating a variant with extra geometry (hinges, clips,
# joints, connectors, etc). Plain flat bricks/plates/tiles should beat these
# when the user asks generically.
_SPECIALIZATION_TOKENS = {
    "hinge", "joint", "holder", "clip", "connector", "turntable",
    "corner", "round", "macaroni", "electrical", "electric", "magnet",
    "magnetic", "friction", "axlehole", "modified", "arch", "curved",
    "bow", "log", "profile", "grille", "grill",
}

# LDraw part IDs with a lowercase letter suffix (e.g. "3001p01", "3062b",
# "3001c00") are almost always decorated / regional variants of a base part.
# Pure-numeric IDs (e.g. "3001") are the canonical plain versions.
_ID_VARIANT_RE = re.compile(r"[a-z]")


def _tokenize(s: str) -> set[str]:
    return {tok for tok in re.split(r"[^a-z0-9]+", s.lower()) if tok}


def _parse_dimensions_from_query(query: str) -> tuple[int | None, int | None]:
    """Pull dimensions out of queries like '2x4', '1 x 8', '2 x 2', 'brick 4x2'.
    Returns (width, length) or (None, None) if no dimensions found."""
    m = re.search(r"(\d+)\s*[xX\u00d7]\s*(\d+)", query)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


def _dims_compatible(p: Part, want_w: int | None, want_l: int | None) -> bool:
    """A 2x4 brick and a 4x2 brick are the same physical part; be
    order-agnostic in matching."""
    pw, pl = p.width_studs, p.length_studs
    if want_w is not None and want_l is not None:
        if pw is None or pl is None:
            return False
        return {pw, pl} == {want_w, want_l}
    if want_w is not None:
        return pw == want_w or pl == want_w
    if want_l is not None:
        return pw == want_l or pl == want_l
    return True


def _alias_penalty(p: Part) -> float:
    """LDraw prefixes alias entries with '=' and obsolete/moved entries with
    '~'. These should never be recommended over the canonical part."""
    name = p.name or ""
    if name.startswith("~") or "obsolete" in name.lower() or "moved" in name.lower():
        return 1.5
    if name.startswith("="):
        return 0.5
    return 0.0


def _score(
    p: Part,
    q_tokens: set[str],
    want_w: int | None,
    want_l: int | None,
) -> float:
    name_tokens = _tokenize(p.name)
    if not name_tokens:
        return 0.0

    # 1. Base score: overlap of query tokens with part-name tokens
    overlap = len(q_tokens & name_tokens)
    base = overlap / max(len(q_tokens), 1) if q_tokens else 0.5

    # 2. Dimensional match: exact = +1.0, off by 1 = +0.2
    dim_bonus = 0.0
    if want_w is not None and want_l is not None:
        want_pair = tuple(sorted([want_w, want_l]))
        got_pair = tuple(sorted([p.width_studs or 0, p.length_studs or 0]))
        if want_pair == got_pair:
            dim_bonus += 1.0
        elif abs(want_pair[0] - got_pair[0]) + abs(want_pair[1] - got_pair[1]) <= 1:
            dim_bonus += 0.2
    else:
        if want_w is not None and p.width_studs is not None:
            diff = abs(want_w - p.width_studs)
            dim_bonus += 0.5 if diff == 0 else (0.1 if diff == 1 else 0.0)
        if want_l is not None and p.length_studs is not None:
            diff = abs(want_l - p.length_studs)
            dim_bonus += 0.5 if diff == 0 else (0.1 if diff == 1 else 0.0)

    # 3. Length penalty: prefer short, plain names over long decorated ones.
    length_penalty = max(0, len(name_tokens) - 5) * 0.1

    # 4. Decoration penalty on name tokens
    decoration_penalty = 0.0
    for tok in name_tokens:
        if tok in _DECORATION_PENALTY_TOKENS:
            decoration_penalty += 0.5

    # 5. Variant-ID penalty on IDs with letter suffixes
    id_variant_penalty = 0.3 if _ID_VARIANT_RE.search(p.ldraw_id) else 0.0

    # 6. Product-line penalty — Duplo/Quatro/Fabuland/etc. only if NOT asked for
    product_line_penalty = 0.0
    for tok in name_tokens:
        if tok in _PRODUCT_LINE_PENALTY_TOKENS and tok not in q_tokens:
            product_line_penalty += 1.5

    # 7. Specialization penalty — hinge/clip/corner/round/etc. only if NOT asked
    specialization_penalty = 0.0
    for tok in name_tokens:
        if tok in _SPECIALIZATION_TOKENS and tok not in q_tokens:
            specialization_penalty += 0.6

    # 8. Alias / obsolete penalty on name prefix
    alias_penalty = _alias_penalty(p)

    return (
        base
        + dim_bonus
        - length_penalty
        - decoration_penalty
        - id_variant_penalty
        - product_line_penalty
        - specialization_penalty
        - alias_penalty
    )


# ---------------------------------------------------------------------------
# Tool: lookup_part
# ---------------------------------------------------------------------------

def lookup_part(
    query: str,
    category: str | None = None,
    width_studs: int | None = None,
    length_studs: int | None = None,
    limit: int = 5,
    catalog: Catalog | None = None,
) -> list[PartHit]:
    """Fuzzy search the LDraw catalog by free-text query + optional filters.

    Parses dimensions out of the query itself ("2x4", "1 x 8"), uses them
    as strong filters, and heavily penalizes decorated/sticker variants,
    Duplo/Quatro product lines, and specialized (hinge/clip/corner) variants
    so the LLM gets plain functional System parts by default.
    """
    cat = catalog or load_catalog()

    # Extract dimensions from the query if the caller didn't pass them.
    q_width, q_length = _parse_dimensions_from_query(query)
    if width_studs is None and q_width is not None:
        width_studs = q_width
    if length_studs is None and q_length is not None:
        length_studs = q_length

    # Tokenize the query, but drop pure-numeric tokens and 'x' — they're
    # already handled as dimensions and just add noise to token matching.
    q_tokens = {t for t in _tokenize(query) if not t.isdigit() and t != "x"}

    # Initial candidate set: any part whose name has at least one query token.
    candidate_ids: set[str] = set()
    for tok in q_tokens:
        candidate_ids.update(cat.parts_by_name_tokens.get(tok, set()))

    if not candidate_ids:
        return []

    hits: list[PartHit] = []
    for pid in candidate_ids:
        p = cat.parts[pid]
        if category and p.category != category:
            continue
        if width_studs is not None or length_studs is not None:
            if not _dims_compatible(p, width_studs, length_studs):
                continue

        score = _score(p, q_tokens, width_studs, length_studs)
        hits.append(
            PartHit(
                ldraw_id=p.ldraw_id,
                name=p.name,
                category=p.category,
                width_studs=p.width_studs,
                length_studs=p.length_studs,
                score=score,
            )
        )

    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:limit]


# ---------------------------------------------------------------------------
# Tool: find_similar_parts
# ---------------------------------------------------------------------------

def find_similar_parts(
    ldraw_id: str,
    n: int = 5,
    catalog: Catalog | None = None,
) -> list[PartHit]:
    """Given a reference part, return others in the same category and rough
    dimensional family. Useful when the LLM wants alternatives to a part."""
    cat = catalog or load_catalog()
    base = cat.parts.get(ldraw_id)
    if base is None:
        return []

    base_name_tokens = _tokenize(base.name)

    hits: list[PartHit] = []
    for pid, p in cat.parts.items():
        if pid == ldraw_id:
            continue
        if p.category != base.category:
            continue
        w_diff = abs((p.width_studs or 0) - (base.width_studs or 0))
        l_diff = abs((p.length_studs or 0) - (base.length_studs or 0))
        # Similarity = lower diff → higher score; also prefer plain over variant.
        similarity = 1.0 / (1.0 + w_diff + l_diff)
        id_variant_penalty = 0.3 if _ID_VARIANT_RE.search(p.ldraw_id) else 0.0
        # Penalize decorated names too.
        name_tokens = _tokenize(p.name)
        decoration_penalty = sum(
            0.3 for tok in name_tokens if tok in _DECORATION_PENALTY_TOKENS
        )
        # Product-line penalty: skip Duplo/Quatro unless the base part is also
        # in that product line.
        product_line_penalty = 0.0
        for tok in name_tokens:
            if tok in _PRODUCT_LINE_PENALTY_TOKENS and tok not in base_name_tokens:
                product_line_penalty += 1.5
        # Specialization penalty: skip hinge/clip/corner variants unless the
        # base already has that specialization.
        specialization_penalty = 0.0
        for tok in name_tokens:
            if tok in _SPECIALIZATION_TOKENS and tok not in base_name_tokens:
                specialization_penalty += 0.6
        # Alias / obsolete penalty
        alias_pen = _alias_penalty(p)

        score = (
            similarity
            - id_variant_penalty
            - decoration_penalty
            - product_line_penalty
            - specialization_penalty
            - alias_pen
        )
        hits.append(
            PartHit(
                ldraw_id=p.ldraw_id,
                name=p.name,
                category=p.category,
                width_studs=p.width_studs,
                length_studs=p.length_studs,
                score=score,
            )
        )

    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:n]


# ---------------------------------------------------------------------------
# Tool: check_assembly_validity
# ---------------------------------------------------------------------------

def check_assembly_validity(
    parts: list[dict],
    catalog: Catalog | None = None,
) -> ValidationResult:
    """Sanity-check a proposed sub-assembly BEFORE the LLM commits to it.

    Cheap checks (deeper checks in solver + Gurobi):
      - Every ldraw_id must exist in the catalog (hallucination defense)
      - Every color_code must exist
      - No empty assemblies
      - No two parts at the same integer (x, y, z)

    Input shape:
      [{"ldraw_id": "3001", "color_code": 4, "x": 0, "y": 0, "z": 0}, ...]
    """
    cat = catalog or load_catalog()
    errors: list[str] = []
    warnings: list[str] = []

    if not parts:
        errors.append("Assembly has no parts.")
        return ValidationResult(ok=False, errors=errors)

    seen_positions: dict[tuple[int, int, int], str] = {}
    for i, p in enumerate(parts):
        if not isinstance(p, dict):
            errors.append(f"Part {i}: not a dict")
            continue

        pid = p.get("ldraw_id")
        if not pid or pid not in cat.parts:
            errors.append(f"Part {i}: unknown ldraw_id {pid!r}")
            continue

        cc = p.get("color_code")
        if cc is not None and cc not in cat.colors_by_code:
            errors.append(f"Part {i} ({pid}): unknown color_code {cc!r}")

        try:
            pos = (int(p["x"]), int(p["y"]), int(p["z"]))
        except (KeyError, TypeError, ValueError):
            errors.append(f"Part {i} ({pid}): missing/invalid x/y/z position")
            continue

        if pos in seen_positions:
            errors.append(
                f"Part {i} ({pid}): collides with part at same position "
                f"as {seen_positions[pos]}"
            )
        else:
            seen_positions[pos] = pid

    return ValidationResult(ok=not errors, errors=errors, warnings=warnings)


# ---------------------------------------------------------------------------
# JSON Schema definitions for Anthropic Claude tool-use
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS = [
    {
        "name": "lookup_part",
        "description": (
            "Search the LDraw catalog of real LEGO parts. Returns up to 5 "
            "candidate parts matching your query. You MUST call this for "
            "every part you reference in your assembly plan — never invent "
            "part IDs from memory."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Free-text like '2x4 brick' or 'wedge plate' or 'minifig head'. Include dimensions in the query when known (they'll be parsed automatically).",
                },
                "category": {
                    "type": "string",
                    "description": "Optional category filter: 'Brick', 'Plate', 'Slope', 'Tile', 'Technic', etc.",
                },
                "width_studs": {
                    "type": "integer",
                    "description": "Optional exact width in studs (order-independent — 2x4 == 4x2).",
                },
                "length_studs": {
                    "type": "integer",
                    "description": "Optional exact length in studs.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "find_similar_parts",
        "description": (
            "Given an ldraw_id you've already found, get alternatives in the "
            "same category and dimensional family. Useful when you want to "
            "swap for a smaller/larger part."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ldraw_id": {
                    "type": "string",
                    "description": "The ldraw_id to find alternatives for (must be one you previously got from lookup_part).",
                },
                "n": {
                    "type": "integer",
                    "description": "Number of alternatives to return (default 5).",
                    "default": 5,
                },
            },
            "required": ["ldraw_id"],
        },
    },
    {
        "name": "check_assembly_validity",
        "description": (
            "Sanity-check a proposed sub-assembly. Call BEFORE committing to "
            "an assembly plan to catch unknown part IDs, invalid colors, or "
            "position collisions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "parts": {
                    "type": "array",
                    "description": "List of bricks with ldraw_id, color_code, and (x, y, z) position.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "ldraw_id": {"type": "string"},
                            "color_code": {"type": "integer"},
                            "x": {"type": "integer"},
                            "y": {"type": "integer"},
                            "z": {"type": "integer"},
                        },
                        "required": ["ldraw_id", "x", "y", "z"],
                    },
                },
            },
            "required": ["parts"],
        },
    },
]


# Map tool name -> callable for dispatch.
TOOL_DISPATCH = {
    "lookup_part": lambda **kw: [hit.to_dict() for hit in lookup_part(**kw)],
    "find_similar_parts": lambda **kw: [hit.to_dict() for hit in find_similar_parts(**kw)],
    "check_assembly_validity": lambda **kw: check_assembly_validity(**kw).to_dict(),
}
