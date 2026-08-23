from __future__ import annotations

import csv
import gzip
import io
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
POC_MONTHLY_DIR = MODEL_DIR / "reconciliation_injection_poc_monthly"
OUTPUT = MODEL_DIR / "reconciled_daily.csv"
POC_OUTPUT = MODEL_DIR / "reconciled_injection_by_poc_2024.csv"
METADATA = Path("data/metadata/reconciliation_sources.json")
FILE_RE = re.compile(r"ReconciledInjectionAndOfftake_(\d{6})_(\d{8})_(\d{6})\.csv\.gz$")


def list_blobs(prefix: str) -> list[dict[str, str]]:
    url = f"{CONTAINER}?restype=container&comp=list&prefix={prefix}"
    response = requests.get(url, timeout=90)
    response.raise_for_status()
    root = ET.fromstring(response.text)
    blobs = []
    for blob in root.findall(".//Blob"):
        name = blob.findtext("Name") or ""
        props = blob.find("Properties")
        blobs.append({
            "name": name,
            "etag": (props.findtext("Etag") if props is not None else "") or "",
            "last_modified": (props.findtext("Last-Modified") if props is not None else "") or "",
            "content_length": (props.findtext("Content-Length") if props is not None else "") or "",
        })
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
    selected = {}
    for month, versions in candidates.items():
        versions.sort()
        selected[month] = versions[-1][2]
    return dict(sorted(selected.items()))


def load_metadata() -> dict[str, object]:
    if not METADATA.exists():
        return {"source": "New Zealand Electricity Authority GR-010", "months": {}}
    return json.loads(METADATA.read_text(encoding="utf-8"))


def metadata_payload(months: dict[str, dict[str, str]]) -> dict[str, object]:
    return {
        "source": "New Zealand Electricity Authority reconciled injection and offtake (GR-010)",
        "selection_rule": "Latest revision timestamp available for each settlement month",
        "coverage": "2024-01-01 to 2024-12-31",
        "months": months,
    }


def write_metadata_if_changed(payload: dict[str, object]) -> bool:
    text = json.dumps(payload, indent=2) + "\n"
    METADATA.parent.mkdir(parents=True, exist_ok=True)
    if METADATA.exists() and METADATA.read_text(encoding="utf-8") == text:
        return False
    METADATA.write_text(text, encoding="utf-8")
    print(f"Wrote {METADATA}")
    return True


def process_month(month: str, blob: dict[str, str]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    url = f"{CONTAINER}/{blob['name']}"
    print(f"Downloading and aggregating {month}: {blob['name']}")
    response = requests.get(url, stream=True, timeout=300)
    response.raise_for_status()
    response.raw.decode_content = False

    totals: defaultdict[str, dict[str, float]] = defaultdict(lambda: {"Offtake": 0.0, "Injection": 0.0})
    counts: defaultdict[str, dict[str, int]] = defaultdict(lambda: {"Offtake": 0, "Injection": 0})
    injection_by_poc: defaultdict[tuple[str, str, str], float] = defaultdict(float)

    with gzip.GzipFile(fileobj=response.raw, mode="rb") as gz:
        text = io.TextIOWrapper(gz, encoding="utf-8-sig", newline="")
        reader = csv.DictReader(text)
        required = {"TradingDate", "FlowDirection", "KilowattHours", "PointOfConnection", "Network", "Island"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise RuntimeError(f"Missing GR-010 fields {sorted(missing)} in {blob['name']}")

        for row in reader:
            date = (row.get("TradingDate") or "").strip()
            direction = (row.get("FlowDirection") or "").strip()
            value_text = (row.get("KilowattHours") or "").strip()
            if not date or not value_text or direction not in ("Offtake", "Injection"):
                continue
            value_mwh = float(value_text) / 1000.0
            totals[date][direction] += value_mwh
            counts[date][direction] += 1
            if direction == "Injection":
                poc = (row.get("PointOfConnection") or "").strip()
                network = (row.get("Network") or "").strip()
                island = (row.get("Island") or "").strip()
                injection_by_poc[(poc, network, island)] += value_mwh

    daily_rows = [{
        "date": date,
        "reconciled_offtake_mwh": round(totals[date]["Offtake"], 6),
        "reconciled_injection_mwh": round(totals[date]["Injection"], 6),
        "offtake_rows": counts[date]["Offtake"],
        "injection_rows": counts[date]["Injection"],
    } for date in sorted(totals)]

    poc_rows = [{
        "point_of_connection": key[0],
        "network": key[1],
        "island": key[2],
        "reconciled_injection_mwh": round(value, 6),
    } for key, value in sorted(injection_by_poc.items())]

    if not daily_rows:
        raise RuntimeError(f"No reconciled rows produced for {month}")
    return daily_rows, poc_rows


def write_daily_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["date", "reconciled_offtake_mwh", "reconciled_injection_mwh", "offtake_rows", "injection_rows"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_poc_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["point_of_connection", "network", "island", "reconciled_injection_mwh"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
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
    new_months = {
        month: {
            "blob_name": blob["name"],
            "source_url": f"{CONTAINER}/{blob['name']}",
            "etag": blob["etag"],
            "last_modified": blob["last_modified"],
            "content_length": blob["content_length"],
        }
        for month, blob in selected.items()
    }

    months_changed = False
    for month, blob in selected.items():
        daily_path = MONTHLY_DIR / f"{month}.csv"
        poc_path = POC_MONTHLY_DIR / f"{month}.csv"
        old = previous.get(month, {}) if isinstance(previous, dict) else {}
        unchanged = (
            daily_path.exists() and poc_path.exists() and isinstance(old, dict)
            and old.get("etag") == blob["etag"] and old.get("blob_name") == blob["name"]
        )
        if not unchanged:
            months_changed = True
            break

    payload = metadata_payload(new_months)
    if not months_changed and OUTPUT.exists() and POC_OUTPUT.exists():
        metadata_changed = write_metadata_if_changed(payload)
        print("All reconciliation month revisions unchanged; skipped monthly reads and annual aggregation")
        print(f"Reconciliation check completed; metadata_changed={metadata_changed}")
        return

    all_daily_rows: list[dict[str, object]] = []
    annual_poc: defaultdict[tuple[str, str, str], float] = defaultdict(float)

    for month, blob in selected.items():
        daily_path = MONTHLY_DIR / f"{month}.csv"
        poc_path = POC_MONTHLY_DIR / f"{month}.csv"
        old = previous.get(month, {}) if isinstance(previous, dict) else {}
        unchanged = (
            daily_path.exists() and poc_path.exists() and isinstance(old, dict)
            and old.get("etag") == blob["etag"] and old.get("blob_name") == blob["name"]
        )
        if unchanged:
            print(f"Unchanged reconciliation month {month}")
            daily_rows = read_csv(daily_path)
            poc_rows = read_csv(poc_path)
        else:
            daily_rows, poc_rows = process_month(month, blob)
            write_daily_csv(daily_path, daily_rows)
            write_poc_csv(poc_path, poc_rows)
            print(f"Wrote {daily_path} ({len(daily_rows)} days) and {poc_path} ({len(poc_rows)} POCs)")

        all_daily_rows.extend(daily_rows)
        for row in poc_rows:
            key = (str(row["point_of_connection"]), str(row["network"]), str(row["island"]))
            annual_poc[key] += float(row["reconciled_injection_mwh"])

    all_daily_rows.sort(key=lambda row: str(row["date"]))
    dates = [str(row["date"]) for row in all_daily_rows]
    if len(dates) != 366 or len(set(dates)) != 366:
        raise RuntimeError(f"Expected 366 unique 2024 dates, got {len(set(dates))} unique / {len(dates)} rows")

    annual_poc_rows = [{
        "point_of_connection": key[0],
        "network": key[1],
        "island": key[2],
        "reconciled_injection_mwh": round(value, 6),
    } for key, value in sorted(annual_poc.items())]

    write_daily_csv(OUTPUT, all_daily_rows)
    write_poc_csv(POC_OUTPUT, annual_poc_rows)
    write_metadata_if_changed(payload)
    print(f"Wrote {OUTPUT} ({len(all_daily_rows)} days)")
    print(f"Wrote {POC_OUTPUT} ({len(annual_poc_rows)} POCs)")
    print("Reconciliation aggregation completed successfully.")


if __name__ == "__main__":
    main()
