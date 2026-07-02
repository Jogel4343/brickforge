"""OMR (Official Model Repository) ingest and retrieval.

Turns MPD files (LDraw's multi-part format used by OMR) into a searchable
corpus of designer decompositions. This is the "how do real designers
actually build this" knowledge that supplements Claude's world knowledge
in Stage 1 of the v2.1 pipeline.

Pipeline:
  1. parse_mpd(path)         -> ParsedMPD (submodels + part usage)
  2. build_index(dir)        -> writes omr_index.json to disk
  3. retrieve(query, n)      -> top-N ParsedMPD entries most similar to query

For v1, retrieval is keyword-based (set name + theme match). Vector-search
upgrade is a v2 concern.

MPD format quick reference:
  - Files are separated by lines starting with "0 FILE <name>"
  - The first FILE block is the top-level assembly
  - Type-1 lines have shape: "1 <color> x y z m11 m12 m13 m21 m22 m23 m31 m32 m33 <file>"
    where <file> is either a submodel name (matches a FILE) or a base .dat part
  - Comments start with "0 <text>" and include header info like "0 Author:", "0 !THEME"
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------

@dataclass
class Submodel:
    """One sub-assembly inside an MPD (e.g. 'left_wing.ldr', 'cockpit.ldr')."""
    name: str
    # Parts directly used by this submodel (not counting nested submodels).
    # Maps ldraw_part_id -> count.
    part_counts: dict[str, int] = field(default_factory=dict)
    # Submodels this submodel references. Maps submodel_name -> count.
    submodel_refs: dict[str, int] = field(default_factory=dict)
    # Rough bbox derived from type-1 line x/y/z ranges (in LDU).
    bbox_ldu: tuple[float, float, float] | None = None

    def total_direct_parts(self) -> int:
        return sum(self.part_counts.values())


@dataclass
class ParsedMPD:
    """One full MPD file — a real LEGO set as designed by a human."""
    set_number: str            # e.g. "10240-1" for UCS X-wing
    set_name: str              # e.g. "Red Five X-wing Starfighter"
    theme: str | None          # e.g. "Star Wars"
    author: str | None
    submodels: list[Submodel]  # top-level submodel is submodels[0] by convention
    # Total part frequency across ALL submodels (recursively resolved).
    aggregate_part_counts: dict[str, int] = field(default_factory=dict)
    # Total submodel count (a proxy for design complexity).
    submodel_count: int = 0

    def to_index_entry(self) -> dict:
        """Compact form for on-disk index — enough for retrieval + prompt injection."""
        return {
            "set_number": self.set_number,
            "set_name": self.set_name,
            "theme": self.theme,
            "author": self.author,
            "submodel_count": self.submodel_count,
            "submodel_names": [s.name for s in self.submodels],
            "aggregate_part_counts": self.aggregate_part_counts,
            # Search haystack (lowercased for keyword match)
            "search_text": " ".join(filter(None, [
                self.set_name,
                self.theme,
                " ".join(s.name for s in self.submodels),
            ])).lower(),
        }


# ---------------------------------------------------------------------------
# MPD parser
# ---------------------------------------------------------------------------

# Type-1 line: "1 <color> x y z m11 m12 m13 m21 m22 m23 m31 m32 m33 <file>"
# We only care about x, y, z, and the file reference. Regex allows extra whitespace.
_TYPE1_RE = re.compile(
    r"^\s*1\s+"
    r"\S+\s+"                                 # color
    r"(\-?[\d\.eE+]+)\s+(\-?[\d\.eE+]+)\s+(\-?[\d\.eE+]+)\s+"   # x y z
    r"(?:\S+\s+){9}"                          # rotation matrix (skipped)
    r"(.+?)\s*$"                              # file reference (rest of line, trimmed)
)

_FILE_RE = re.compile(r"^\s*0\s+FILE\s+(.+?)\s*$", re.IGNORECASE)
_NOFILE_RE = re.compile(r"^\s*0\s+NOFILE\s*$", re.IGNORECASE)
_THEME_RE = re.compile(r"^\s*0\s+!THEME\s+(.+?)\s*$", re.IGNORECASE)
_AUTHOR_RE = re.compile(r"^\s*0\s+Author:\s+(.+?)\s*$", re.IGNORECASE)


def _normalize_file_ref(raw: str) -> str:
    """Strip whitespace, lowercase, drop leading 's\\' / '48\\' path prefixes
    that LDraw uses for sub-parts and hi-res primitives — those are library
    internals, not the .dat name we care about."""
    ref = raw.strip().lower().replace("\\", "/")
    for prefix in ("s/", "48/"):
        if ref.startswith(prefix):
            ref = ref[len(prefix):]
    return ref


def _is_base_part(ref: str, submodel_names: set[str]) -> bool:
    """A type-1 reference is a base .dat part if it doesn't match any FILE
    block in the same MPD."""
    return ref not in submodel_names


def _part_id_from_ref(ref: str) -> str:
    """Strip the .dat extension — '3001.dat' -> '3001'."""
    if ref.endswith(".dat"):
        return ref[:-4]
    return ref


def parse_mpd(path: str | Path) -> ParsedMPD:
    """Parse a single MPD file into a ParsedMPD.

    Raises ValueError if the file doesn't look like an MPD (no FILE blocks)."""
    p = Path(path)
    text = p.read_text(encoding="utf-8", errors="replace")

    # First pass: split into (submodel_name, lines) blocks.
    blocks: list[tuple[str, list[str]]] = []
    current_name: str | None = None
    current_lines: list[str] = []
    theme: str | None = None
    author: str | None = None
    set_name: str | None = None
    set_number: str | None = None

    for line in text.splitlines():
        m_file = _FILE_RE.match(line)
        m_nofile = _NOFILE_RE.match(line)

        if m_file:
            if current_name is not None:
                blocks.append((current_name, current_lines))
            current_name = _normalize_file_ref(m_file.group(1))
            current_lines = []
            continue
        if m_nofile:
            if current_name is not None:
                blocks.append((current_name, current_lines))
                current_name = None
                current_lines = []
            continue

        if current_name is not None:
            current_lines.append(line)

        # Pull OMR header metadata from the top-of-file comment lines.
        if theme is None:
            m_theme = _THEME_RE.match(line)
            if m_theme:
                theme = m_theme.group(1).strip()
        if author is None:
            m_author = _AUTHOR_RE.match(line)
            if m_author:
                author = m_author.group(1).strip()

    if current_name is not None:
        blocks.append((current_name, current_lines))

    if not blocks:
        raise ValueError(f"{p}: no FILE blocks found — not a valid MPD")

    submodel_names = {name for name, _ in blocks}

    # Try to derive set_number + set_name from the top block's OMR header.
    # OMR spec header: "0 <Lego Set Number> <Lego Set Name>"
    top_name, top_lines = blocks[0]
    for line in top_lines[:20]:
        # Skip meta lines starting with "0 !" or "0 Name:" etc.
        if re.match(r"^\s*0\s+\d[\d\-a-zA-Z]*\s+\S", line) and "!" not in line and ":" not in line[:20]:
            m = re.match(r"^\s*0\s+(\S+)\s+(.+?)\s*$", line)
            if m:
                set_number = m.group(1)
                set_name = m.group(2)
                break

    # Fall back: derive from filename ("10240-1-red-five-xwing.mpd" -> "10240-1")
    if set_number is None:
        stem = p.stem
        m = re.match(r"^(\d+(?:-\d+)?)", stem)
        if m:
            set_number = m.group(1)
        else:
            set_number = stem
    if set_name is None:
        set_name = p.stem

    # Second pass: for each block, count direct parts + direct submodel refs
    # and compute a bbox from type-1 line coordinates.
    submodels: list[Submodel] = []
    for name, lines in blocks:
        part_counts: Counter[str] = Counter()
        submodel_refs: Counter[str] = Counter()
        xs, ys, zs = [], [], []
        for line in lines:
            m = _TYPE1_RE.match(line)
            if not m:
                continue
            x, y, z, ref = m.groups()
            try:
                xs.append(float(x))
                ys.append(float(y))
                zs.append(float(z))
            except ValueError:
                pass
            norm = _normalize_file_ref(ref)
            if _is_base_part(norm, submodel_names):
                part_counts[_part_id_from_ref(norm)] += 1
            else:
                submodel_refs[norm] += 1
        bbox = None
        if xs and ys and zs:
            bbox = (
                round(max(xs) - min(xs), 2),
                round(max(ys) - min(ys), 2),
                round(max(zs) - min(zs), 2),
            )
        submodels.append(Submodel(
            name=name,
            part_counts=dict(part_counts),
            submodel_refs=dict(submodel_refs),
            bbox_ldu=bbox,
        ))

    # Aggregate part counts: walk the submodel graph from the top,
    # accumulating base-part counts weighted by submodel_ref multiplicity.
    by_name = {s.name: s for s in submodels}
    aggregate: Counter[str] = Counter()

    def walk(sub_name: str, multiplier: int) -> None:
        s = by_name.get(sub_name)
        if s is None:
            return
        for pid, cnt in s.part_counts.items():
            aggregate[pid] += cnt * multiplier
        for ref, cnt in s.submodel_refs.items():
            walk(ref, multiplier * cnt)

    walk(top_name, 1)

    return ParsedMPD(
        set_number=set_number,
        set_name=set_name,
        theme=theme,
        author=author,
        submodels=submodels,
        aggregate_part_counts=dict(aggregate),
        submodel_count=len(submodels),
    )


# ---------------------------------------------------------------------------
# Index build + retrieval
# ---------------------------------------------------------------------------

def build_index(mpd_dir: str | Path, index_out: str | Path) -> dict:
    """Parse every .mpd in a directory, write a JSON index to disk.

    Returns a summary dict: {parsed: N, failed: [(path, error), ...]}."""
    mpd_dir = Path(mpd_dir)
    entries: list[dict] = []
    failed: list[tuple[str, str]] = []

    for path in sorted(mpd_dir.rglob("*.mpd")):
        try:
            parsed = parse_mpd(path)
            entries.append(parsed.to_index_entry())
        except Exception as e:
            failed.append((str(path), str(e)))

    # Also accept .ldr top-level files that happen to be multi-file MPDs.
    for path in sorted(mpd_dir.rglob("*.ldr")):
        try:
            parsed = parse_mpd(path)
            if parsed.submodel_count >= 2:  # single-file .ldr isn't useful here
                entries.append(parsed.to_index_entry())
        except Exception as e:
            failed.append((str(path), str(e)))

    Path(index_out).write_text(json.dumps({"entries": entries}, indent=2))
    return {"parsed": len(entries), "failed": failed}


def retrieve(query: str, index_path: str | Path, n: int = 3) -> list[dict]:
    """Keyword-based retrieval: score each index entry by how many query
    tokens appear in its search_text. Ties broken by higher submodel_count
    (proxy for design richness).

    Returns list of index entries (dicts) sorted best-first.
    """
    data = json.loads(Path(index_path).read_text())
    entries: list[dict] = data.get("entries", [])
    if not entries:
        return []

    q_tokens = {t for t in re.split(r"[^a-z0-9]+", query.lower()) if t}
    if not q_tokens:
        return entries[:n]

    scored: list[tuple[float, dict]] = []
    for e in entries:
        haystack = e.get("search_text", "")
        h_tokens = set(re.split(r"[^a-z0-9]+", haystack))
        overlap = len(q_tokens & h_tokens)
        if overlap == 0:
            continue
        # Reward richer decompositions on ties.
        score = overlap + 0.001 * min(e.get("submodel_count", 0), 100)
        scored.append((score, e))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [e for _, e in scored[:n]]


# ---------------------------------------------------------------------------
# CLI entrypoint (local dev only — real Modal entrypoint is separate)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("usage: python -m worker.omr_ingest <cmd> [args]")
        print("  parse <path.mpd>              — parse one MPD, print summary")
        print("  build <dir> <index_out.json>  — build index over a dir of MPDs")
        print("  query <index.json> <query>    — retrieve top-3 for a query")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "parse":
        parsed = parse_mpd(sys.argv[2])
        print(json.dumps({
            "set_number": parsed.set_number,
            "set_name": parsed.set_name,
            "theme": parsed.theme,
            "author": parsed.author,
            "submodel_count": parsed.submodel_count,
            "submodel_names": [s.name for s in parsed.submodels],
            "top_10_parts": Counter(parsed.aggregate_part_counts).most_common(10),
        }, indent=2))
    elif cmd == "build":
        summary = build_index(sys.argv[2], sys.argv[3])
        print(json.dumps(summary, indent=2))
    elif cmd == "query":
        hits = retrieve(sys.argv[3], sys.argv[2], n=3)
        print(json.dumps(hits, indent=2))
    else:
        print(f"unknown command: {cmd}")
        sys.exit(1)
