from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
import xml.etree.ElementTree as ET

import requests

CONTAINER = "https://emidatasets.blob.core.windows.net/publicdata"
START_YYYYMM = "202401"

DATASETS = {
    "generation": {
        "prefix": "Datasets/Wholesale/Generation/Generation_MD/",
        "suffix": "_Generation_MD.csv",
        "output_dir": Path("data/wholesale/raw/generation"),
    },
    "grid_export": {
        "prefix": "Datasets/Wholesale/Metered_data/Grid_export/",
        "suffix": "_Grid_export.csv",
        "output_dir": Path("data/wholesale/raw/grid_export"),
    },
}

METADATA_PATH = Path("data/metadata/wholesale_sources.json")


def list_blobs(prefix: str) -> list[dict[str, str]]:
    blobs: list[dict[str, str]] = []
    marker = ""
    while True:
        params = {"restype": "container", "comp": "list", "prefix": prefix}
        if marker:
            params["marker"] = marker
        response = requests.get(CONTAINER, params=params, timeout=120)
        response.raise_for_status()
        root = ET.fromstring(response.text)
        for blob in root.findall("./Blobs/Blob"):
            name = blob.findtext("Name", default="")
            props = blob.find("Properties")
            blobs.append(
                {
                    "name": name,
                    "last_modified": props.findtext("Last-Modified", default="") if props is not None else "",
                    "content_length": props.findtext("Content-Length", default="") if props is not None else "",
                    "etag": (props.findtext("Etag", default="") if props is not None else "").strip('"'),
                }
            )
        marker = root.findtext("NextMarker", default="")
        if not marker:
            break
    return blobs


def yyyymm_from_name(name: str) -> str | None:
    base = Path(name).name
    prefix = base[:6]
    return prefix if len(prefix) == 6 and prefix.isdigit() else None


def load_previous_metadata() -> dict:
    if not METADATA_PATH.exists():
        return {}
    try:
        return json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def previous_dataset(previous: dict, key: str) -> dict:
    datasets = previous.get("datasets", {})
    if not isinstance(datasets, dict):
        return {}
    dataset = datasets.get(key, {})
    return dataset if isinstance(dataset, dict) else {}


def previous_files(dataset: dict) -> list[dict[str, str]]:
    files = dataset.get("files", [])
    return [item for item in files if isinstance(item, dict)] if isinstance(files, list) else []


def fast_scan_years(dataset: dict) -> list[str]:
    years = {str(datetime.now(timezone.utc).year)}
    known_months = [
        yyyymm_from_name(str(item.get("blob_name", "")))
        for item in previous_files(dataset)
    ]
    known_months = [month for month in known_months if month]
    if known_months:
        years.add(max(known_months)[:4])
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


def download_if_changed(blob: dict[str, str], output: Path, previous_etag: str | None) -> bool:
    if output.exists() and previous_etag and blob["etag"] == previous_etag:
        print(f"Unchanged {output}")
        return False

    url = f"{CONTAINER}/{quote(blob['name'], safe='/()')}"
    response = requests.get(url, timeout=120)
    response.raise_for_status()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and output.read_bytes() == response.content:
        print(f"Unchanged {output} (byte-identical despite source metadata change)")
        return False
    output.write_bytes(response.content)
    print(f"Downloaded {output} ({len(response.content)} bytes)")
    return True


def source_record(blob: dict[str, str], output: Path) -> dict[str, str]:
    return {
        "blob_name": blob["name"],
        "local_file": str(output),
        "source_last_modified": blob["last_modified"],
        "source_content_length": blob["content_length"],
        "source_etag": blob["etag"],
    }


def scan_dataset(key: str, config: dict, previous: dict, *, full_scan: bool) -> tuple[dict, bool]:
    old_dataset = previous_dataset(previous, key)
    old_files = previous_files(old_dataset)
    old_by_blob = {str(item.get("blob_name", "")): item for item in old_files}

    if full_scan:
        scan_prefixes = [config["prefix"]]
        scanned_years: set[str] | None = None
        print(f"{key}: full historical source listing")
    else:
        years = fast_scan_years(old_dataset)
        scan_prefixes = [config["prefix"] + year for year in years]
        scanned_years = set(years)
        print(f"{key}: fast listing for year prefix(es) {', '.join(years)}")

    discovered: dict[str, dict[str, str]] = {}
    for prefix in scan_prefixes:
        for blob in list_blobs(prefix):
            yyyymm = yyyymm_from_name(blob["name"])
            if yyyymm is None or yyyymm < START_YYYYMM:
                continue
            if not blob["name"].endswith(config["suffix"]):
                continue
            discovered[blob["name"]] = blob

    changed = False
    records_by_blob: dict[str, dict[str, str]] = {}

    # Fast scans only replace metadata for the year prefixes actually checked.
    # Older checked-in months remain authoritative until the scheduled/manual full scan.
    if not full_scan and scanned_years is not None:
        for item in old_files:
            month = yyyymm_from_name(str(item.get("blob_name", "")))
            if month and month[:4] not in scanned_years:
                records_by_blob[str(item["blob_name"])] = item

    for blob_name, blob in sorted(discovered.items()):
        output = config["output_dir"] / Path(blob_name).name
        old = old_by_blob.get(blob_name, {})
        previous_etag = str(old.get("source_etag", "")) if isinstance(old, dict) else ""
        changed |= download_if_changed(blob, output, previous_etag)
        records_by_blob[blob_name] = source_record(blob, output)

    # If a fast listing temporarily omits a previously known month in the scanned
    # year, retain it rather than silently dropping a checked-in source. A full
    # scan remains the explicit catalog reconciliation path.
    if not full_scan and scanned_years is not None:
        for item in old_files:
            blob_name = str(item.get("blob_name", ""))
            month = yyyymm_from_name(blob_name)
            if month and month[:4] in scanned_years and blob_name not in discovered:
                print(f"Warning: fast listing did not return previously known {blob_name}; retaining existing record")
                records_by_blob[blob_name] = item

    files = [records_by_blob[name] for name in sorted(records_by_blob)]
    return {
        "prefix": config["prefix"],
        "start_yyyymm": START_YYYYMM,
        "file_count": len(files),
        "files": files,
    }, changed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--full-scan",
        action="store_true",
        help="List the complete wholesale source directories instead of only the current/latest known year prefixes.",
    )
    args = parser.parse_args()
    full_scan = args.full_scan or workflow_requests_full_scan()

    previous = load_previous_metadata()
    metadata = {
        "source": "New Zealand Electricity Authority Azure Blob Storage",
        "purpose": "Historical generation and grid-demand inputs for NZ electricity model",
        "datasets": {},
    }

    changed = False
    for key, config in DATASETS.items():
        dataset_metadata, dataset_changed = scan_dataset(
            key,
            config,
            previous,
            full_scan=full_scan,
        )
        metadata["datasets"][key] = dataset_metadata
        changed |= dataset_changed
        print(f"Selected {dataset_metadata['file_count']} {key} monthly files")

    if metadata != previous:
        METADATA_PATH.parent.mkdir(parents=True, exist_ok=True)
        METADATA_PATH.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        changed = True
        print(f"Wrote {METADATA_PATH}")

    mode = "full" if full_scan else "fast"
    print(
        f"Wholesale update completed; mode={mode}; changed={changed}; "
        f"checked_at_utc={datetime.now(timezone.utc).isoformat()}"
    )


if __name__ == "__main__":
    main()
