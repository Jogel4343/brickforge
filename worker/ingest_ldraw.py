"""Ingest the LDraw parts library into Supabase.

Run once after downloading the LDraw library:

    curl -L -o ldraw_complete.zip https://library.ldraw.org/library/updates/complete.zip
    unzip ldraw_complete.zip -d ../data/ldraw_complete
    python ingest_ldraw.py --library-path ../data/ldraw_complete/ldraw

The script does three things:

1. Parses `LDConfig.ldr` → seeds the `colors` table.
2. Parses `parts.lst` (or walks `parts/`) → seeds the `parts` table.
3. Best-effort enrichment by joining Rebrickable's mapping CSVs (downloadable from
   https://rebrickable.com/downloads/) to attach BrickLink + Rebrickable IDs.

The LDraw library itself is large (~250 MB unzipped). Don't commit it.
"""

from __future__ import annotations
import argparse
import csv
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

try:
    from supabase import create_client  # type: ignore
except ImportError:
    create_client = None  # allow dry-run without Supabase installed


# --------------- LDraw config (colors) ---------------

COLOR_RE = re.compile(
    r"0\s+!COLOUR\s+(\S+)\s+CODE\s+(\d+)\s+VALUE\s+(#[0-9A-Fa-f]+)\s+EDGE\s+(#[0-9A-Fa-f]+)(.*)$"
)


@dataclass
class LdrawColor:
    code: int
    name: str
    hex: str
    is_transparent: bool


def parse_colors(ldconfig_path: Path) -> list[LdrawColor]:
    out: list[LdrawColor] = []
    with ldconfig_path.open() as f:
        for line in f:
            m = COLOR_RE.match(line.strip())
            if not m:
                continue
            name, code, hex_val, _edge, rest = m.groups()
            out.append(
                LdrawColor(
                    code=int(code),
                    name=name,
                    hex=hex_val,
                    is_transparent="ALPHA" in rest.upper(),
                )
            )
    return out


# --------------- LDraw parts ---------------

@dataclass
class LdrawPart:
    ldraw_id: str
    name: str
    category: str | None


def parse_parts_lst(parts_lst: Path) -> Iterable[LdrawPart]:
    """`parts.lst` format: `<id>.dat                       <Description>`."""
    with parts_lst.open(errors="ignore") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            parts = re.split(r"\s+", line, maxsplit=1)
            if len(parts) != 2:
                continue
            ldraw_file, desc = parts
            ldraw_id = ldraw_file.replace(".dat", "")
            category = desc.split()[0] if desc else None
            yield LdrawPart(ldraw_id=ldraw_id, name=desc.strip(), category=category)


# --------------- Rebrickable mapping enrichment ---------------

def load_rebrickable_mapping(csv_path: Path | None) -> dict[str, dict]:
    """Optional. CSV expected columns: part_num,name,ldraw_id,bricklink_id."""
    if not csv_path or not csv_path.exists():
        return {}
    out = {}
    with csv_path.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            ldraw = (row.get("ldraw_id") or "").strip()
            if not ldraw:
                continue
            out[ldraw] = {
                "rebrickable_id": (row.get("part_num") or "").strip(),
                "bricklink_id": (row.get("bricklink_id") or "").strip(),
            }
    return out


# --------------- Upserts ---------------

def upsert(client, table: str, rows: list[dict], batch: int = 500):
    if not rows:
        return
    for i in range(0, len(rows), batch):
        chunk = rows[i : i + batch]
        client.table(table).upsert(chunk).execute()
        print(f"  upserted {table}: {i + len(chunk)}/{len(rows)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--library-path", required=True, type=Path, help="Path to extracted LDraw library (contains LDConfig.ldr and parts/).")
    ap.add_argument("--rebrickable-mapping", type=Path, default=None, help="Optional Rebrickable parts CSV for BrickLink ID enrichment.")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    ld = args.library_path
    ldconfig = ld / "LDConfig.ldr"
    parts_lst = ld / "parts.lst"

    if not ldconfig.exists():
        sys.exit(f"LDConfig.ldr not found at {ldconfig}")
    if not parts_lst.exists():
        sys.exit(f"parts.lst not found at {parts_lst}")

    print(f"Parsing colors from {ldconfig}…")
    colors = parse_colors(ldconfig)
    print(f"  found {len(colors)} colors")

    print(f"Parsing parts from {parts_lst}…")
    parts = list(parse_parts_lst(parts_lst))
    print(f"  found {len(parts)} parts")

    mapping = load_rebrickable_mapping(args.rebrickable_mapping)
    if mapping:
        print(f"  loaded {len(mapping)} Rebrickable mappings")

    color_rows = [
        {"ldraw_code": c.code, "name": c.name, "hex": c.hex, "is_transparent": c.is_transparent}
        for c in colors
    ]
    part_rows = []
    for p in parts:
        m = mapping.get(p.ldraw_id, {})
        part_rows.append(
            {
                "ldraw_id": p.ldraw_id,
                "name": p.name,
                "category": p.category,
                "bricklink_id": m.get("bricklink_id") or None,
                "rebrickable_id": m.get("rebrickable_id") or None,
                "is_common": False,  # populated later from purchase frequency
            }
        )

    if args.dry_run:
        print("DRY RUN — printing first 5 of each table:")
        print("colors:", color_rows[:5])
        print("parts:", part_rows[:5])
        return

    url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        sys.exit("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY env vars.")
    if create_client is None:
        sys.exit("supabase-py not installed (pip install supabase)")

    client = create_client(url, key)
    print("Upserting colors…")
    upsert(client, "colors", color_rows)
    print("Upserting parts…")
    upsert(client, "parts", part_rows)
    print("Done.")


if __name__ == "__main__":
    main()
