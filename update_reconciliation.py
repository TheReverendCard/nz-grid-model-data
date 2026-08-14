from __future__ import annotations

import csv
import gzip
import json
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

import requests

CONTAINER = "https://emidatasets.blob.core.windows.net/publicdata"
PREFIX = "Datasets/Wholesale/Volumes/Reconciliation/2024/"
MODEL_DIR = Path("data/wholesale/model")
MONTHLY_DIR = MODEL_DIR / "reconciliation_monthly"
OUTPUT = MODEL_DIR / "reconciled_daily.csv"
METADATA = Path("data/metadata/reconciliation_sources.json")
FILE_RE = re.compile(r"ReconciledInjectionAndOfftake_(\d{6})_(\d{8})_(\d{6})\.csv\.gz$")


def list_blobs(prefix: str) -> list[dict[str, str]]:
    url = f"{CONTAINER}?restype=container&comp=list&prefix={prefix}"
    response = requests.get(url, timeout=90)
    response.raise_for_status()
    root = ET.fromstring(response.text)
    blobs: list[dict[str, str]] = []
    for blob in root.findall(".//Blob"):
        name = blob.findtext("Name") or ""
        props = blob.find("Properties")
        blobs.append(
            {
                "name": name,
                "etag": (props.findtext("Etag") if props is not None else "") or "",
                "last_modified": (props.findtext("Last-Modified") if props is not None else "") or "",
                "content_length": (props.findtext("Content-Length") if props is not None else "") or "",
            }
        )
    return blobs


def choose_latest_by_month(blobs: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    candidates: defaultdict[str, list[tuple[str, str, dict[str, str]]]] = defaultdict(list)
    for blob in blobs:
        match = FILE_RE.search(blob["name"])
        if not match:
            continue
        month, revision_date, revision_time = match.groups()
        if month.startswith("2024"):
            candidates[month].append((revision_date, revision_time, blob))

    selected: dict[str, dict[str, str]] = {}
    for month, versions in candidates.items():
        versions.sort()
        selected[month] = versions[-1][2]
    return dict(sorted(selected.items()))


def load_metadata() -> dict[str, object]:
    if not METADATA.exists():
        return {"source": "New Zealand Electricity Authority GR-010", "months": {}}
    return json.loads(METADATA.read_text(encoding="utf-8"))


def process_month(month: str, blob: dict[str, str]) -> list[dict[str, object]]:
    url = f"{CONTAINER}/{blob['name']}"
    print(f"Downloading and aggregating {month}: {blob['name']}")
    response = requests.get(url, stream=True, timeout=300)
    response.raise_for_status()
    response.raw.decode_content = False

    totals: defaultdict[str, dict[str, float]] = defaultdict(lambda: {"Offtake": 0.0, "Injection": 0.0})
    counts: defaultdict[str, dict[str, int]] = defaultdict(lambda: {"Offtake": 0, "Injection": 0})

    with gzip.GzipFile(fileobj=response.raw, mode="rb") as gz:
        import io
        text = io.TextIOWrapper(gz, encoding="utf-8-sig", newline="")
        reader = csv.DictReader(text)
        required = {"TradingDate", "FlowDirection", "KilowattHours"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise RuntimeError(f"Missing GR-010 fields {sorted(missing)} in {blob['name']}")

        for row in reader:
            date = (row.get("TradingDate") or "").strip()
            direction = (row.get("FlowDirection") or "").strip()
            value = (row.get("KilowattHours") or "").strip()
            if not date or not value:
                continue
            if direction not in ("Offtake", "Injection"):
                continue
            totals[date][direction] += float(value) / 1000.0
            counts[date][direction] += 1

    rows = []
    for date in sorted(totals):
        rows.append(
            {
                "date": date,
                "reconciled_offtake_mwh": round(totals[date]["Offtake"], 6),
                "reconciled_injection_mwh": round(totals[date]["Injection"], 6),
                "offtake_rows": counts[date]["Offtake"],
                "injection_rows": counts[date]["Injection"],
            }
        )
    if not rows:
        raise RuntimeError(f"No reconciled rows produced for {month}")
    return rows


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["date", "reconciled_offtake_mwh", "reconciled_injection_mwh", "offtake_rows", "injection_rows"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def read_monthly(path: Path) -> list[dict[str, object]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> None:
    selected = choose_latest_by_month(list_blobs(PREFIX))
    expected = {f"2024{month:02d}" for month in range(1, 13)}
    missing = sorted(expected - set(selected))
    if missing:
        raise RuntimeError(f"Missing 2024 reconciliation months: {missing}")

    metadata = load_metadata()
    previous = metadata.get("months", {}) if isinstance(metadata.get("months"), dict) else {}
    new_months: dict[str, object] = {}

    all_rows: list[dict[str, object]] = []
    for month, blob in selected.items():
        monthly_path = MONTHLY_DIR / f"{month}.csv"
        old = previous.get(month, {}) if isinstance(previous, dict) else {}
        unchanged = (
            monthly_path.exists()
            and isinstance(old, dict)
            and old.get("etag") == blob["etag"]
            and old.get("blob_name") == blob["name"]
        )
        if unchanged:
            print(f"Unchanged reconciliation month {month}")
            rows = read_monthly(monthly_path)
        else:
            rows = process_month(month, blob)
            write_csv(monthly_path, rows)
            print(f"Wrote {monthly_path} ({len(rows)} days)")

        all_rows.extend(rows)
        new_months[month] = {
            "blob_name": blob["name"],
            "source_url": f"{CONTAINER}/{blob['name']}",
            "etag": blob["etag"],
            "last_modified": blob["last_modified"],
            "content_length": blob["content_length"],
        }

    all_rows.sort(key=lambda row: str(row["date"]))
    dates = [str(row["date"]) for row in all_rows]
    if len(dates) != 366 or len(set(dates)) != 366:
        raise RuntimeError(f"Expected 366 unique 2024 dates, got {len(set(dates))} unique / {len(dates)} rows")

    write_csv(OUTPUT, all_rows)
    METADATA.parent.mkdir(parents=True, exist_ok=True)
    METADATA.write_text(
        json.dumps(
            {
                "source": "New Zealand Electricity Authority reconciled injection and offtake (GR-010)",
                "selection_rule": "Latest revision timestamp available for each settlement month",
                "coverage": "2024-01-01 to 2024-12-31",
                "months": new_months,
            },
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {OUTPUT} ({len(all_rows)} days)")
    print("Reconciliation aggregation completed successfully.")


if __name__ == "__main__":
    main()
