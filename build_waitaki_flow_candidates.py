from __future__ import annotations

import csv
import json
from pathlib import Path

INDEX = Path("data/hydro/indexes/FileIndex_Flows.csv")
OUT = Path("data/model/waitaki_flow_candidates.json")

KEYWORDS = (
    "tekapo",
    "tekapo",
    "pukaki",
    "ohau",
    "ruataniwha",
    "benmore",
    "aviemore",
    "waitaki",
)
SITE_CODES = {"TKA", "TKB", "PKI", "OHA", "OHU", "OHB", "OHC", "RTH", "BEN", "AVI", "WTK", "TEK"}


def row_text(row: dict[str, str]) -> str:
    return " ".join(str(v) for v in row.values() if v).lower()


def main() -> None:
    if not INDEX.exists():
        raise FileNotFoundError(f"Missing HMD flow index: {INDEX}")

    with INDEX.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    selected = []
    for row in rows:
        text = row_text(row)
        site = (row.get("SiteCode") or "").strip()
        if site in SITE_CODES or any(k in text for k in KEYWORDS):
            selected.append(row)

    selected.sort(key=lambda r: ((r.get("SiteCode") or ""), (r.get("FileName") or "")))
    result = {
        "status": "waitaki_hmd_flow_index_candidates",
        "source": str(INDEX),
        "purpose": "Surface HMD flow-series candidates for explicit source-water routing in the Waitaki dispatcher, without inferring Pukaki or Ohau releases from downstream generation.",
        "candidate_count": len(selected),
        "selection": {
            "site_codes": sorted(SITE_CODES),
            "keywords": list(KEYWORDS),
        },
        "candidates": selected,
        "next_step": "Review descriptions and select actual source-release/canal-flow series for Tekapo, Pukaki and Ohau before adding those exact files to the curated HMD download set.",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT} with {len(selected)} candidate flow series")
    for row in selected:
        print(row.get("SiteCode", ""), row.get("FileName", ""), row.get("Description", ""))


if __name__ == "__main__":
    main()
