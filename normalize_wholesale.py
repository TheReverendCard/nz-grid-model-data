from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

RAW_DIR = Path("data/wholesale/raw")
MODEL_DIR = Path("data/wholesale/model")

GEN_OUTPUT = MODEL_DIR / "generation_daily.csv"
DEMAND_OUTPUT = MODEL_DIR / "demand_daily.csv"

TP_COLUMNS = [f"TP{i}" for i in range(1, 51)]


def read_csv(path: Path):
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def sum_tp_kwh(row: dict[str, str]) -> float:
    total = 0.0
    populated = 0
    for col in TP_COLUMNS:
        value = (row.get(col) or "").strip()
        if value == "":
            continue
        total += float(value)
        populated += 1
    if populated == 0:
        raise RuntimeError(f"No trading-period values found for row dated {row.get('Trading_Date')}")
    return total


def normalize_generation() -> list[dict[str, object]]:
    grouped: defaultdict[tuple[str, str, str, str, str, str, str], float] = defaultdict(float)

    for path in sorted((RAW_DIR / "generation").glob("*_Generation_MD.csv")):
        for row in read_csv(path):
            date = (row.get("Trading_Date") or "").strip()
            if not date:
                continue
            key = (
                date,
                (row.get("Site_Code") or "").strip(),
                (row.get("POC_Code") or "").strip(),
                (row.get("Nwk_Code") or "").strip(),
                (row.get("Gen_Code") or "").strip(),
                (row.get("Fuel_Code") or "").strip(),
                (row.get("Tech_Code") or "").strip(),
            )
            grouped[key] += sum_tp_kwh(row) / 1000.0

    rows = [
        {
            "date": key[0],
            "site_code": key[1],
            "poc_code": key[2],
            "network_code": key[3],
            "generator_code": key[4],
            "fuel_code": key[5],
            "tech_code": key[6],
            "generation_mwh": round(value, 6),
        }
        for key, value in grouped.items()
    ]
    rows.sort(key=lambda r: (str(r["date"]), str(r["generator_code"]), str(r["poc_code"]), str(r["network_code"])))
    return rows


def normalize_demand() -> list[dict[str, object]]:
    by_date: defaultdict[str, float] = defaultdict(float)
    row_counts: defaultdict[str, int] = defaultdict(int)

    for path in sorted((RAW_DIR / "grid_export").glob("*_Grid_export.csv")):
        for row in read_csv(path):
            date = (row.get("Trading_Date") or "").strip()
            if not date:
                continue

            unit = (row.get("Unit_Measure") or "").strip()
            direction = (row.get("Flow_Direction") or "").strip()
            status = (row.get("Status") or "").strip()

            if unit != "kWh":
                raise RuntimeError(f"Unexpected Grid_export unit {unit!r} in {path.name}")
            if direction != "X":
                raise RuntimeError(f"Unexpected Grid_export direction {direction!r} in {path.name}")
            if status != "F":
                raise RuntimeError(f"Unexpected Grid_export status {status!r} in {path.name}")

            by_date[date] += sum_tp_kwh(row) / 1000.0
            row_counts[date] += 1

    rows = [
        {
            "date": date,
            "grid_export_mwh": round(by_date[date], 6),
            "source_rows": row_counts[date],
        }
        for date in sorted(by_date)
    ]
    return rows


def write_rows(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {path} ({len(rows)} rows)")


def main() -> None:
    generation = normalize_generation()
    demand = normalize_demand()

    if not generation or not demand:
        raise RuntimeError("Wholesale normalization produced no rows")

    write_rows(
        GEN_OUTPUT,
        [
            "date",
            "site_code",
            "poc_code",
            "network_code",
            "generator_code",
            "fuel_code",
            "tech_code",
            "generation_mwh",
        ],
        generation,
    )
    write_rows(DEMAND_OUTPUT, ["date", "grid_export_mwh", "source_rows"], demand)

    print(
        f"Wholesale normalization completed successfully: "
        f"{generation[0]['date']} to {generation[-1]['date']} generation, "
        f"{demand[0]['date']} to {demand[-1]['date']} grid export"
    )


if __name__ == "__main__":
    main()
