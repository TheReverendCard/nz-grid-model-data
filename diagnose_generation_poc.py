from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

MODEL_DIR = Path("data/wholesale/model")
GEN_FILE = MODEL_DIR / "generation_daily.csv"
RECON_POC_FILE = MODEL_DIR / "reconciled_injection_by_poc_2024.csv"
OUTPUT = MODEL_DIR / "generation_poc_comparison_2024.csv"
SUMMARY = MODEL_DIR / "generation_poc_diagnostic_2024.json"


def read_csv(path: Path):
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def main() -> None:
    generation_by_poc: defaultdict[str, float] = defaultdict(float)
    for row in read_csv(GEN_FILE):
        if not row["date"].startswith("2024"):
            continue
        generation_by_poc[(row.get("poc_code") or "").strip()] += float(row["generation_mwh"])

    reconciled_by_poc: defaultdict[str, float] = defaultdict(float)
    poc_details: dict[str, tuple[str, str]] = {}
    for row in read_csv(RECON_POC_FILE):
        poc = (row.get("point_of_connection") or "").strip()
        reconciled_by_poc[poc] += float(row["reconciled_injection_mwh"])
        poc_details[poc] = ((row.get("network") or "").strip(), (row.get("island") or "").strip())

    all_pocs = sorted(set(generation_by_poc) | set(reconciled_by_poc))
    rows = []
    for poc in all_pocs:
        recon = reconciled_by_poc.get(poc, 0.0)
        gen = generation_by_poc.get(poc, 0.0)
        diff = recon - gen
        network, island = poc_details.get(poc, ("", ""))
        rows.append({
            "point_of_connection": poc,
            "network": network,
            "island": island,
            "reconciled_injection_mwh": round(recon, 6),
            "generation_md_mwh": round(gen, 6),
            "reconciled_minus_generation_md_mwh": round(diff, 6),
            "generation_md_share_of_reconciled_pct": round(gen / recon * 100.0, 4) if recon else None,
        })

    rows.sort(key=lambda r: float(r["reconciled_minus_generation_md_mwh"]), reverse=True)
    with OUTPUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    positive = [r for r in rows if float(r["reconciled_minus_generation_md_mwh"]) > 0]
    top_missing = positive[:20]
    total_recon = sum(reconciled_by_poc.values())
    total_gen = sum(generation_by_poc.values())
    total_gap = total_recon - total_gen
    top20_gap = sum(float(r["reconciled_minus_generation_md_mwh"]) for r in top_missing)

    summary = {
        "reconciled_injection_mwh": round(total_recon, 3),
        "generation_md_mwh": round(total_gen, 3),
        "gap_mwh": round(total_gap, 3),
        "generation_md_share_of_reconciled_pct": round(total_gen / total_recon * 100.0, 4) if total_recon else None,
        "poc_count_reconciled": len(reconciled_by_poc),
        "poc_count_generation_md": len(generation_by_poc),
        "poc_count_union": len(all_pocs),
        "top_20_positive_gap_mwh": round(top20_gap, 3),
        "top_20_share_of_total_gap_pct": round(top20_gap / total_gap * 100.0, 3) if total_gap else None,
        "top_missing_pocs": top_missing,
    }
    SUMMARY.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT} ({len(rows)} POCs)")
    print(f"Wrote {SUMMARY}")
    print(
        f"2024 reconciled injection={total_recon/1_000_000:.3f} TWh, "
        f"Generation_MD={total_gen/1_000_000:.3f} TWh, gap={total_gap/1_000_000:.3f} TWh"
    )


if __name__ == "__main__":
    main()
