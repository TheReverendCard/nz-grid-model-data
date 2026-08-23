from __future__ import annotations

import argparse
import csv
import io
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree as ET

import requests

CONTAINER = "https://emidatasets.blob.core.windows.net/publicdata"
PREFIX = "Datasets/Wholesale/DispatchAndPricing/FinalEnergyPrices/"
MONTHLY_PREFIX = PREFIX + "ByMonth/"
START_YYYYMM = "202401"
NODES = ("OTA2201", "HAY2201", "BEN2201")
DATA_DIR = Path("data/prices")
MONTHLY_DIR = DATA_DIR / "monthly"
OUT = Path("data/public/wholesale_prices_daily.csv")
META = Path("data/metadata/price_sources.json")


def list_blobs(prefix: str) -> list[dict[str, str]]:
    blobs: list[dict[str, str]] = []
    marker = ""
    while True:
        params = {"restype": "container", "comp": "list", "prefix": prefix}
        if marker:
            params["marker"] = marker
        response = requests.get(CONTAINER, params=params, timeout=120)
        response.raise_for_status()
        root = ET.fromstring(response.content)
        for blob in root.findall("./Blobs/Blob"):
            name = blob.findtext("Name") or ""
            props = blob.find("Properties")
            blobs.append(
                {
                    "name": name,
                    "etag": ((props.findtext("Etag") or "").strip('"') if props is not None else ""),
                }
            )
        marker = root.findtext("NextMarker") or ""
        if not marker:
            break
    return blobs


def parse(content: bytes) -> list[dict[str, object]]:
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    by: defaultdict[tuple[str, str], list[float]] = defaultdict(list)
    for row in reader:
        date = (row.get("TradingDate") or row.get("Trading_date") or "").strip()
        node = (row.get("PointOfConnection") or row.get("Node") or "").strip()
        price = (row.get("DollarsPerMegawattHour") or row.get("Price") or "").strip()
        if date and node in NODES and price != "":
            by[(date, node)].append(float(price))

    rows: list[dict[str, object]] = []
    for date in sorted({value for value, _ in by}):
        values = {
            node: (sum(by[(date, node)]) / len(by[(date, node)]) if by[(date, node)] else None)
            for node in NODES
        }
        present = [value for value in values.values() if value is not None]
        rows.append(
            {
                "date": date,
                "otahuhu_nzd_mwh": round(values["OTA2201"], 4) if values["OTA2201"] is not None else "",
                "haywards_nzd_mwh": round(values["HAY2201"], 4) if values["HAY2201"] is not None else "",
                "benmore_nzd_mwh": round(values["BEN2201"], 4) if values["BEN2201"] is not None else "",
                "reference_mean_nzd_mwh": round(sum(present) / len(present), 4) if present else "",
            }
        )
    return rows


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_rows(path: Path, rows: list[dict[str, object]]) -> bool:
    if not rows:
        return False
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    text = buffer.getvalue()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == text:
        print(f"Unchanged {path}")
        return False
    path.write_text(text, encoding="utf-8")
    print(f"Wrote {path} ({len(rows)} rows)")
    return True


def load_metadata() -> dict[str, object]:
    if not META.exists():
        return {}
    try:
        return json.loads(META.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def year_from_blob_name(name: str) -> str | None:
    base = Path(name).name
    prefix = base[:4]
    return prefix if len(prefix) == 4 and prefix.isdigit() else None


def fast_scan_years(old_etags: dict[str, str]) -> list[str]:
    years = {str(datetime.now(timezone.utc).year)}
    known = [year_from_blob_name(name) for name in old_etags]
    known = [year for year in known if year]
    if known:
        years.add(max(known))
    return sorted(years)


def workflow_requests_full_scan() -> bool:
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    if event_name == "schedule" and datetime.now(timezone.utc).weekday() == 6:
        return True
    if event_name != "workflow_dispatch":
        return False
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    if not event_path:
        return False
    try:
        payload = json.loads(Path(event_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    inputs = payload.get("inputs", {}) if isinstance(payload, dict) else {}
    value = inputs.get("force_rebuild", False) if isinstance(inputs, dict) else False
    return str(value).lower() == "true"


def download(blob_name: str) -> bytes:
    response = requests.get(f"{CONTAINER}/{quote(blob_name, safe='/')}", timeout=180)
    response.raise_for_status()
    return response.content


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full-scan", action="store_true", help="Verify all monthly price files from 2024 onward.")
    args = parser.parse_args()
    full_scan = args.full_scan or workflow_requests_full_scan()

    MONTHLY_DIR.mkdir(parents=True, exist_ok=True)
    previous = load_metadata()
    old_etags_raw = previous.get("etags", {}) if isinstance(previous, dict) else {}
    old_etags = {str(key): str(value) for key, value in old_etags_raw.items()} if isinstance(old_etags_raw, dict) else {}
    years = fast_scan_years(old_etags)

    if full_scan:
        monthly_prefixes = [MONTHLY_PREFIX]
        print("Prices: full monthly history verification")
    else:
        monthly_prefixes = [MONTHLY_PREFIX + year for year in years]
        print(f"Prices: fast monthly listing for {', '.join(years)}")

    monthly_discovered: dict[str, dict[str, str]] = {}
    for prefix in monthly_prefixes:
        for blob in list_blobs(prefix):
            base = Path(blob["name"]).name
            yyyymm = base[:6]
            if yyyymm.isdigit() and yyyymm >= START_YYYYMM and blob["name"].endswith("_FinalEnergyPrices.csv"):
                monthly_discovered[blob["name"]] = blob

    etags: dict[str, str] = {}
    if not full_scan:
        scanned_years = set(years)
        for name, etag in old_etags.items():
            if "/ByMonth/" in name:
                year = year_from_blob_name(name)
                if year and year not in scanned_years:
                    etags[name] = etag

    for name, blob in sorted(monthly_discovered.items()):
        yyyymm = Path(name).name[:6]
        part = MONTHLY_DIR / f"{yyyymm}.csv"
        etags[name] = blob["etag"]
        if part.exists() and old_etags.get(name) == blob["etag"]:
            print(f"Unchanged {part}")
            continue
        rows = parse(download(name))
        if not rows:
            raise RuntimeError(f"No reference-node prices parsed from {name}")
        write_rows(part, rows)

    monthly_rows: list[dict[str, str]] = []
    finalized_months: set[str] = set()
    for path in sorted(MONTHLY_DIR.glob("*.csv")):
        rows = read_csv(path)
        monthly_rows.extend(rows)
        finalized_months.update(row["date"].replace("-", "")[:6] for row in rows)

    existing_by_date = {row["date"]: row for row in read_csv(OUT)}
    daily_candidates: dict[str, tuple[bool, dict[str, str]]] = {}
    for year in years:
        for blob in list_blobs(PREFIX + year):
            relative = blob["name"].removeprefix(PREFIX)
            if "/" in relative:
                continue
            base = Path(blob["name"]).name
            day = base[:8]
            if not day.isdigit() or day[:6] < START_YYYYMM or day[:6] in finalized_months:
                continue
            is_final = base.endswith("_FinalEnergyPrices.csv")
            current = daily_candidates.get(day)
            if current is None or (is_final and not current[0]):
                daily_candidates[day] = (is_final, blob)

    live_daily_rows: list[dict[str, object]] = []
    live_daily_names: set[str] = set()
    for day, (_, blob) in sorted(daily_candidates.items()):
        name = blob["name"]
        live_daily_names.add(name)
        etags[name] = blob["etag"]
        iso_date = f"{day[:4]}-{day[4:6]}-{day[6:8]}"
        if old_etags.get(name) == blob["etag"] and iso_date in existing_by_date:
            live_daily_rows.append(existing_by_date[iso_date])
            continue
        rows = parse(download(name))
        if not rows:
            raise RuntimeError(f"No reference-node prices parsed from {name}")
        live_daily_rows.extend(rows)

    # Retain old daily ETags only while they still correspond to an unfinalized
    # month represented by the current daily listing. Finalized months are compacted
    # into the monthly cache and no longer need their individual daily ETags.
    for name, etag in old_etags.items():
        if "/ByMonth/" not in name and name in live_daily_names and name not in etags:
            etags[name] = etag

    combined = monthly_rows + live_daily_rows
    deduplicated = {str(row["date"]): row for row in combined}
    output_rows = [deduplicated[date] for date in sorted(deduplicated)]
    write_rows(OUT, output_rows)

    metadata = {
        "source": "Electricity Authority final energy prices",
        "reference_nodes": list(NODES),
        "start_yyyymm": START_YYYYMM,
        "etags": dict(sorted(etags.items())),
    }
    text = json.dumps(metadata, indent=2) + "\n"
    META.parent.mkdir(parents=True, exist_ok=True)
    if META.exists() and META.read_text(encoding="utf-8") == text:
        print(f"Unchanged {META}")
    else:
        META.write_text(text, encoding="utf-8")
        print(f"Wrote {META}")

    mode = "full" if full_scan else "fast"
    print(f"EA final energy price check completed; mode={mode}; days={len(output_rows)}")


if __name__ == "__main__":
    main()
