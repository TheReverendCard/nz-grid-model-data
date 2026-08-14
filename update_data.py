from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree

import requests

CONTAINER = "https://emidatasets.blob.core.windows.net/publicdata"
HMD_ROOT = "Datasets/Environment/HydrologicalModellingDataset/"
INFRA_PREFIX = HMD_ROOT + "1_InfrastructureAndHydroConstraintAttributes/"
TARGET_SUFFIX = "_InfrastructureAndHydroConstraintAttributes.csv"

DATA_DIR = Path("data/hydro")
META_DIR = Path("data/metadata")
OUTPUT_FILE = DATA_DIR / "infrastructure_and_constraints.csv"
METADATA_FILE = META_DIR / "hmd_infrastructure_source.json"
MANIFEST_FILE = META_DIR / "hmd_manifest.json"


def list_blobs(prefix: str) -> list[dict[str, str]]:
    """Return blobs under an EA EMI Azure prefix using the public REST endpoint."""
    response = requests.get(
        CONTAINER,
        params={
            "restype": "container",
            "comp": "list",
            "prefix": prefix,
        },
        timeout=60,
    )
    response.raise_for_status()

    root = ElementTree.fromstring(response.content)
    blobs: list[dict[str, str]] = []

    for blob in root.findall(".//Blob"):
        name = blob.findtext("Name")
        if not name:
            continue
        blobs.append(
            {
                "name": name,
                "last_modified": blob.findtext("Properties/Last-Modified") or "",
                "content_length": blob.findtext("Properties/Content-Length") or "",
                "etag": blob.findtext("Properties/Etag") or "",
            }
        )

    return blobs


def choose_latest_infrastructure_csv(blobs: list[dict[str, str]]) -> dict[str, str]:
    candidates = [b for b in blobs if b["name"].endswith(TARGET_SUFFIX)]
    if not candidates:
        names = "\n".join(b["name"] for b in blobs[:30])
        raise RuntimeError(
            "No HMD infrastructure CSV matched the expected filename suffix. "
            f"First blobs returned:\n{names}"
        )

    # The files are date-prefixed YYYYMMDD, so filename order is chronological.
    return max(candidates, key=lambda b: Path(b["name"]).name)


def blob_url(blob_name: str) -> str:
    return f"{CONTAINER}/{quote(blob_name, safe='/')}"


def download_file(url: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    response = requests.get(url, timeout=120)
    response.raise_for_status()

    if not response.content:
        raise RuntimeError(f"EA returned an empty file for {url}")

    output_path.write_bytes(response.content)

    with output_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        row_count = sum(1 for _ in reader)

    if row_count < 2:
        raise RuntimeError(
            f"Downloaded file {output_path} does not look like a populated CSV."
        )

    print(f"Downloaded {output_path} ({row_count - 1} data rows)")


def write_infrastructure_metadata(blob: dict[str, str], source_url: str) -> None:
    META_DIR.mkdir(parents=True, exist_ok=True)
    metadata = {
        "source": "New Zealand Electricity Authority EMI Azure Blob Storage",
        "dataset": "Hydrological Modelling Dataset",
        "component": "Infrastructure and Hydro Constraint Attributes",
        "blob_name": blob["name"],
        "source_url": source_url,
        "source_last_modified": blob["last_modified"],
        "source_content_length": blob["content_length"],
        "source_etag": blob["etag"],
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    METADATA_FILE.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote metadata to {METADATA_FILE}")


def write_hmd_manifest(blobs: list[dict[str, str]]) -> None:
    """Persist the EA's current HMD blob layout so we can discover components safely."""
    META_DIR.mkdir(parents=True, exist_ok=True)
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)

    for blob in blobs:
        relative = blob["name"].removeprefix(HMD_ROOT)
        component = relative.split("/", 1)[0] if "/" in relative else "_root"
        grouped[component].append(blob)

    manifest = {
        "source": "New Zealand Electricity Authority EMI Azure Blob Storage",
        "prefix": HMD_ROOT,
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "blob_count": len(blobs),
        "components": {
            component: sorted(items, key=lambda item: item["name"])
            for component, items in sorted(grouped.items())
        },
    }
    MANIFEST_FILE.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote HMD manifest with {len(grouped)} components to {MANIFEST_FILE}")


def main() -> None:
    print("Discovering EA HMD dataset layout...")
    all_hmd_blobs = list_blobs(HMD_ROOT)
    print(f"Found {len(all_hmd_blobs)} blobs under {HMD_ROOT}")
    write_hmd_manifest(all_hmd_blobs)

    infrastructure_blobs = [
        blob for blob in all_hmd_blobs if blob["name"].startswith(INFRA_PREFIX)
    ]
    selected = choose_latest_infrastructure_csv(infrastructure_blobs)
    source_url = blob_url(selected["name"])
    print(f"Selected infrastructure file: {selected['name']}")

    download_file(source_url, OUTPUT_FILE)
    write_infrastructure_metadata(selected, source_url)
    print("EA HMD discovery/update completed successfully.")


if __name__ == "__main__":
    main()
