from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

RAW_DIR = Path("data/hydro/raw")
INDEX_DIR = Path("data/hydro/indexes")
MODEL_DIR = Path("data/hydro/model")

STORAGE_OUTPUT = MODEL_DIR / "storage_daily.csv"
FLOWS_OUTPUT = MODEL_DIR / "flows_daily.csv"

MM3_PER_DAY_PER_CUMECS = 86400 / 1_000_000  # 0.0864 Mm3/day per m3/s


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def to_float(value: str | None) -> float | None:
    if value is None or value.strip() == "":
        return None
    return float(value)


def build_lookup(index_path: Path) -> dict[str, dict[str, str]]:
    return {row["FileName"]: row for row in read_csv(index_path)}


def write_rows(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {path} ({len(rows)} rows)")


def normalize_storage() -> list[dict[str, object]]:
    lookup = build_lookup(INDEX_DIR / "FileIndex_Storage.csv")
    rows: list[dict[str, object]] = []

    for path in sorted((RAW_DIR / "storage").glob("*.csv")):
        meta = lookup.get(path.name)
        if meta is None:
            raise RuntimeError(f"Storage file missing from index: {path.name}")

        for raw in read_csv(path):
            active = to_float(raw.get("Active storage (Mm³)"))
            contingent = to_float(raw.get("Active contingent storage (Mm³)"))
            total = None
            if active is not None or contingent is not None:
                total = (active or 0.0) + (contingent or 0.0)

            rows.append(
                {
                    "date": raw["Date"],
                    "plant_group": meta.get("PlantGroup", ""),
                    "site_code": meta.get("SiteCode", ""),
                    "reservoir": meta.get("Description", ""),
                    "island_code": meta.get("IslandCode", ""),
                    "lake_level_m": to_float(raw.get("Lake level (m)")),
                    "active_storage_mm3": active,
                    "contingent_storage_mm3": contingent,
                    "total_storage_mm3": total,
                    "quality_code": raw.get("QualityCode", ""),
                    "source_file": path.name,
                }
            )

    rows.sort(key=lambda row: (str(row["date"]), str(row["site_code"])))
    return rows


def normalize_flow_folder(
    folder: str,
    series_type: str,
    lookup: dict[str, dict[str, str]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []

    for path in sorted((RAW_DIR / folder).glob("*.csv")):
        meta = lookup.get(path.name)
        if meta is None:
            raise RuntimeError(f"{series_type} file missing from index: {path.name}")

        for raw in read_csv(path):
            flow = to_float(raw.get("Flow (m³/s)"))
            if flow is None:
                raise RuntimeError(f"Missing Flow (m³/s) in {path.name}")

            rows.append(
                {
                    "date": raw["Date"],
                    "series_type": series_type,
                    "plant_group": meta.get("PlantGroup", ""),
                    "site_code": meta.get("SiteCode", ""),
                    "site": meta.get("Site", meta.get("Description", "")),
                    "island_code": meta.get("IslandCode", ""),
                    "flow_type": meta.get("FlowType", "Derived tributary flow" if series_type == "tributary_flow" else ""),
                    "flow_m3s": flow,
                    "volume_mm3_day": flow * MM3_PER_DAY_PER_CUMECS,
                    "quality_code": raw.get("QualityCode", ""),
                    "source_file": path.name,
                }
            )

    return rows


def normalize_spill() -> list[dict[str, object]]:
    lookup = build_lookup(INDEX_DIR / "FileIndex_Spill.csv")
    rows: list[dict[str, object]] = []

    for path in sorted((RAW_DIR / "spill").glob("*.csv")):
        meta = lookup.get(path.name)
        if meta is None:
            raise RuntimeError(f"Spill file missing from index: {path.name}")

        for raw in read_csv(path):
            volume = to_float(raw.get("Spill (Mm³)"))
            if volume is None:
                raise RuntimeError(f"Missing Spill (Mm³) in {path.name}")

            rows.append(
                {
                    "date": raw["Date"],
                    "series_type": "spill",
                    "plant_group": meta.get("PlantGroup", ""),
                    "site_code": meta.get("SiteCode", ""),
                    "site": meta.get("Description", ""),
                    "island_code": meta.get("IslandCode", ""),
                    "flow_type": "Spill/release",
                    "flow_m3s": volume / MM3_PER_DAY_PER_CUMECS,
                    "volume_mm3_day": volume,
                    "quality_code": raw.get("QualityCode", ""),
                    "source_file": path.name,
                }
            )

    return rows


def check_unique(rows: list[dict[str, object]], keys: tuple[str, ...], label: str) -> None:
    counts: defaultdict[tuple[object, ...], int] = defaultdict(int)
    for row in rows:
        counts[tuple(row[key] for key in keys)] += 1
    duplicates = [key for key, count in counts.items() if count > 1]
    if duplicates:
        example = duplicates[:5]
        raise RuntimeError(f"Duplicate {label} rows detected for {example}")


def main() -> None:
    storage_rows = normalize_storage()

    flow_lookup = build_lookup(INDEX_DIR / "FileIndex_Flows.csv")
    tributary_lookup = build_lookup(INDEX_DIR / "FileIndex_DerivedTributaryFlows.csv")
    flow_rows = normalize_flow_folder("inflows", "inflow", flow_lookup)
    flow_rows += normalize_flow_folder("tributary_flows", "tributary_flow", tributary_lookup)
    flow_rows += normalize_spill()
    flow_rows.sort(key=lambda row: (str(row["date"]), str(row["series_type"]), str(row["site_code"]), str(row["source_file"])))

    check_unique(storage_rows, ("date", "site_code"), "storage")
    check_unique(flow_rows, ("date", "series_type", "source_file"), "flow")

    write_rows(
        STORAGE_OUTPUT,
        [
            "date",
            "plant_group",
            "site_code",
            "reservoir",
            "island_code",
            "lake_level_m",
            "active_storage_mm3",
            "contingent_storage_mm3",
            "total_storage_mm3",
            "quality_code",
            "source_file",
        ],
        storage_rows,
    )
    write_rows(
        FLOWS_OUTPUT,
        [
            "date",
            "series_type",
            "plant_group",
            "site_code",
            "site",
            "island_code",
            "flow_type",
            "flow_m3s",
            "volume_mm3_day",
            "quality_code",
            "source_file",
        ],
        flow_rows,
    )

    print("Hydro normalization completed successfully.")


if __name__ == "__main__":
    main()
