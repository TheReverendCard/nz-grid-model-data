from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

STREET = Path("data/distributed_generation/solar_installations_by_street.csv")
MONTHLY = Path("data/distributed_generation/model/national_solar_all_monthly.csv")
OUT_CSV = Path("data/distributed_generation/model/solar_size_buckets_current.csv")
OUT_JSON = Path("data/distributed_generation/model/solar_size_buckets_current.json")

BUCKETS = (
    ("lt_25_kw", 0.0, 25.0),
    ("25_to_lt_100_kw", 25.0, 100.0),
    ("100_to_lt_250_kw", 100.0, 250.0),
    ("250_to_lt_1000_kw", 250.0, 1000.0),
    ("gte_1000_kw", 1000.0, float("inf")),
)


def bucket_for(avg_kw: float) -> str:
    for name, lower, upper in BUCKETS:
        if lower <= avg_kw < upper:
            return name
    raise ValueError(f"Could not bucket average capacity {avg_kw}")


def latest_national() -> dict[str, str]:
    with MONTHLY.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise RuntimeError(f"No rows in {MONTHLY}")
    return rows[-1]


def parse_count(value: str) -> tuple[float | None, bool]:
    value = (value or "").strip()
    if not value:
        return None, True
    if value.lower() == "3 or less":
        return None, True
    return float(value), False


def main() -> None:
    latest = latest_national()
    national_icps = float(latest["icp_count"])
    national_capacity_kw = float(latest["installed_capacity_mw"]) * 1000.0

    rows: list[dict[str, object]] = []
    exact_count_total = 0.0
    suppressed_rows = 0

    with STREET.open("r", encoding="utf-8-sig", newline="") as handle:
        for source in csv.DictReader(handle):
            avg_kw = float(source["GenerationCapacityKilowattsAvg"])
            count, suppressed = parse_count(source["ICPs"])
            if suppressed:
                suppressed_rows += 1
            else:
                exact_count_total += float(count)

            capacity_text = (source.get("GenerationCapacityKilowattsSum") or "").strip()
            capacity_kw = float(capacity_text) if capacity_text else None
            rows.append(
                {
                    "bucket": bucket_for(avg_kw),
                    "market_segment": source.get("MarketSegment", ""),
                    "avg_kw": avg_kw,
                    "count": count,
                    "suppressed": suppressed,
                    "capacity_kw": capacity_kw,
                }
            )

    residual_count = max(0.0, national_icps - exact_count_total)
    suppressed_count_each = residual_count / suppressed_rows if suppressed_rows else 0.0

    # Reconcile privacy-suppressed street groups to the national monthly totals.
    # The street file gives an average kW for suppressed rows but not a count or
    # summed capacity.  Assign the observed residual ICP count evenly across those
    # rows, then use their average kW as a weight for the remaining national capacity.
    for row in rows:
        if row["suppressed"]:
            row["count"] = suppressed_count_each

    known_capacity_kw = sum(float(r["capacity_kw"]) for r in rows if r["capacity_kw"] is not None)
    missing_rows = [r for r in rows if r["capacity_kw"] is None]
    residual_capacity_kw = max(0.0, national_capacity_kw - known_capacity_kw)
    missing_weight = sum(float(r["avg_kw"]) * float(r["count"]) for r in missing_rows)

    for row in missing_rows:
        weight = float(row["avg_kw"]) * float(row["count"])
        row["capacity_kw"] = residual_capacity_kw * weight / missing_weight if missing_weight else 0.0

    totals: dict[str, dict[str, float]] = defaultdict(lambda: {"icps": 0.0, "capacity_kw": 0.0, "res_icps": 0.0, "bus_icps": 0.0})
    for row in rows:
        bucket = str(row["bucket"])
        count = float(row["count"])
        totals[bucket]["icps"] += count
        totals[bucket]["capacity_kw"] += float(row["capacity_kw"])
        if row["market_segment"] == "Res":
            totals[bucket]["res_icps"] += count
        elif row["market_segment"] == "Bus":
            totals[bucket]["bus_icps"] += count

    ordered = []
    for name, _, _ in BUCKETS:
        values = totals[name]
        ordered.append(
            {
                "bucket": name,
                "estimated_icps": round(values["icps"], 1),
                "share_of_solar_icps_pct": round(values["icps"] / national_icps * 100.0, 4) if national_icps else 0.0,
                "capacity_mw": round(values["capacity_kw"] / 1000.0, 6),
                "share_of_solar_capacity_pct": round(values["capacity_kw"] / national_capacity_kw * 100.0, 4) if national_capacity_kw else 0.0,
                "estimated_residential_icps": round(values["res_icps"], 1),
                "estimated_business_icps": round(values["bus_icps"], 1),
            }
        )

    small = totals["lt_25_kw"]
    larger_distributed_icps = sum(totals[name]["icps"] for name in ("25_to_lt_100_kw", "100_to_lt_250_kw", "250_to_lt_1000_kw"))
    larger_distributed_kw = sum(totals[name]["capacity_kw"] for name in ("25_to_lt_100_kw", "100_to_lt_250_kw", "250_to_lt_1000_kw"))
    utility = totals["gte_1000_kw"]

    summary = {
        "source_latest_month": latest["month_end"],
        "national_observed": {
            "solar_icps": int(national_icps),
            "installed_capacity_mw": round(national_capacity_kw / 1000.0, 6),
        },
        "method": {
            "classification": "Street/market-segment groups are classified by their reported average installed kW per ICP.",
            "small_scale": "<25 kW; intended population for ICP-adoption S-curves.",
            "larger_distributed": "25 kW to <1 MW; intended for capacity-growth modelling.",
            "utility_scale": ">=1 MW; exclude from distributed-adoption model and treat with project pipeline.",
            "privacy_suppression": f"{suppressed_rows} rows report '3 or less'. Their combined ICP count is reconciled to the national monthly total and distributed evenly; missing capacity is reconciled to the national capacity total in proportion to reported average kW times estimated count.",
            "edge_case_note": "Because the source is street-level aggregated data, a group average can occasionally place individual installations on the other side of a threshold. This is acceptable for the working national model.",
        },
        "buckets": ordered,
        "model_groups": {
            "small_lt_25_kw": {
                "estimated_icps": round(small["icps"], 1),
                "capacity_mw": round(small["capacity_kw"] / 1000.0, 6),
            },
            "larger_distributed_25_kw_to_lt_1_mw": {
                "estimated_icps": round(larger_distributed_icps, 1),
                "capacity_mw": round(larger_distributed_kw / 1000.0, 6),
            },
            "utility_gte_1_mw": {
                "estimated_icps": round(utility["icps"], 1),
                "capacity_mw": round(utility["capacity_kw"] / 1000.0, 6),
            },
        },
    }

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(ordered[0].keys()))
        writer.writeheader()
        writer.writerows(ordered)
    OUT_JSON.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    print(f"Wrote {OUT_CSV}")
    print(f"Wrote {OUT_JSON}")
    print(f"Source month: {latest['month_end']}; observed {int(national_icps):,} solar ICPs, {national_capacity_kw / 1000:.1f} MW")
    for row in ordered:
        print(f"{row['bucket']}: {row['estimated_icps']:,.1f} ICPs, {row['capacity_mw']:,.1f} MW")


if __name__ == "__main__":
    main()
