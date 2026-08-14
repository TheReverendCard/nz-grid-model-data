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
INDEX_DIR = DATA_DIR / "indexes"
RAW_DIR = DATA_DIR / "raw"
STORAGE_DIR = RAW_DIR / "storage"
INFLOW_DIR = RAW_DIR / "inflows"
TRIBFLOW_DIR = RAW_DIR / "tributary_flows"
SPILL_DIR = RAW_DIR / "spill"
META_DIR = Path("data/metadata")
OUTPUT_FILE = DATA_DIR / "infrastructure_and_constraints.csv"
METADATA_FILE = META_DIR / "hmd_infrastructure_source.json"
MANIFEST_FILE = META_DIR / "hmd_manifest.json"
INDEX_METADATA_FILE = META_DIR / "hmd_index_sources.json"
SERIES_METADATA_FILE = META_DIR / "hmd_model_series_sources.json"

# Storage lakes that matter most for dry-year / seasonal hydro modelling.
STORAGE_SITE_CODES = {
    "WKA",  # Waikaremoana
    "TPO",  # Taupo
    "WPU",  # Wakatipu
    "WAN",  # Wanaka
    "HWE",  # Hawea
    "MAN",  # Manapouri
    "TAU",  # Te Anau
    "OHA",  # Ohau
    "PKI",  # Pukaki
    "TKA",  # Tekapo
}

# Preferred headwater inflow series.  Natural inflows are used where available;
# operational series are used where they better represent the current scheme.
PREFERRED_INFLOW_FILES = {
    "NI_WKA_Natural_LakeWaikaremoana_Inflow_3650(1).csv",
    "NI_TPO_Actual_LakeTaupoInfrastructure_Inflow_72790(1).csv",
    "SI_HWE_Natural_LakeHawea_Inflow_9170(1).csv",
    "SI_MAN_Actual_LakeManapouri(WithMararoaSpillAndMinFlowRegime)_Inflow_99552(1).csv",
    "SI_TAU_Natural_LakeTeAnau_Inflow_9570(1).csv",
    "SI_OHU_Natural_LakeOhau_Inflow_98614(3).csv",
    "SI_PKI_Natural_LakePukaki_Inflow_98770(1).csv",
    "SI_TEK_Natural_LakeTekapo_Inflow_98770(2).csv",
}

# Spill/release histories for the main controlled headwater reservoirs.
SPILL_SITE_CODES = {"WKA", "TPO", "HWE", "MAN", "TAU", "OHA", "PKI", "TKA"}


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

    return max(candidates, key=lambda b: Path(b["name"]).name)


def blob_url(blob_name: str) -> str:
    return f"{CONTAINER}/{quote(blob_name, safe='/')}"


def download_file(url: str, output_path: Path) -> int:
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
    return row_count - 1


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


def fetch_file_indexes(blobs: list[dict[str, str]]) -> None:
    """Download all small HMD FileIndex CSVs discovered in the public container."""
    indexes = [
        blob
        for blob in blobs
        if Path(blob["name"]).name.startswith("FileIndex")
        and blob["name"].lower().endswith(".csv")
    ]

    if not indexes:
        raise RuntimeError("No HMD FileIndex CSV files were discovered.")

    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    source_records: list[dict[str, str | int]] = []
    basename_counts: dict[str, int] = defaultdict(int)
    for blob in indexes:
        basename_counts[Path(blob["name"]).name] += 1

    for blob in sorted(indexes, key=lambda item: item["name"]):
        source_name = blob["name"]
        basename = Path(source_name).name
        if basename_counts[basename] > 1:
            parent = Path(source_name).parent.name
            local_name = f"{parent}_{basename}"
        else:
            local_name = basename

        source_url = blob_url(source_name)
        output_path = INDEX_DIR / local_name
        data_rows = download_file(source_url, output_path)
        source_records.append(
            {
                "local_file": str(output_path),
                "blob_name": source_name,
                "source_url": source_url,
                "source_last_modified": blob["last_modified"],
                "source_content_length": blob["content_length"],
                "source_etag": blob["etag"],
                "data_rows": data_rows,
            }
        )

    index_metadata = {
        "source": "New Zealand Electricity Authority EMI Azure Blob Storage",
        "dataset": "Hydrological Modelling Dataset",
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "index_count": len(source_records),
        "indexes": source_records,
    }
    INDEX_METADATA_FILE.write_text(
        json.dumps(index_metadata, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Downloaded {len(source_records)} HMD file indexes")


def read_index(filename: str) -> list[dict[str, str]]:
    path = INDEX_DIR / filename
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def blob_by_basename(blobs: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    """Create a basename lookup and fail if the HMD contains ambiguous CSV names."""
    lookup: dict[str, dict[str, str]] = {}
    duplicates: set[str] = set()
    for blob in blobs:
        if not blob["name"].lower().endswith(".csv"):
            continue
        basename = Path(blob["name"]).name
        if basename in lookup:
            duplicates.add(basename)
        else:
            lookup[basename] = blob

    for duplicate in duplicates:
        lookup.pop(duplicate, None)
    return lookup


def download_series(
    blob_lookup: dict[str, dict[str, str]],
    filenames: list[str],
    output_dir: Path,
    series_type: str,
) -> list[dict[str, str | int]]:
    records: list[dict[str, str | int]] = []
    for filename in sorted(set(filenames)):
        blob = blob_lookup.get(filename)
        if blob is None:
            raise RuntimeError(
                f"Could not uniquely locate HMD source file for selected {series_type}: {filename}"
            )
        source_url = blob_url(blob["name"])
        output_path = output_dir / filename
        data_rows = download_file(source_url, output_path)
        records.append(
            {
                "series_type": series_type,
                "local_file": str(output_path),
                "blob_name": blob["name"],
                "source_url": source_url,
                "source_last_modified": blob["last_modified"],
                "source_content_length": blob["content_length"],
                "source_etag": blob["etag"],
                "data_rows": data_rows,
            }
        )
    return records


def fetch_model_hydro_series(blobs: list[dict[str, str]]) -> None:
    """Fetch a curated HMD subset suitable for the first NZ dry-year model."""
    lookup = blob_by_basename(blobs)

    storage_index = read_index("FileIndex_Storage.csv")
    storage_files = [
        row["FileName"]
        for row in storage_index
        if row.get("SiteCode") in STORAGE_SITE_CODES
    ]

    flow_index = read_index("FileIndex_Flows.csv")
    available_flow_files = {row["FileName"] for row in flow_index}
    missing_preferred = PREFERRED_INFLOW_FILES - available_flow_files
    if missing_preferred:
        raise RuntimeError(
            "Preferred HMD inflow series disappeared from FileIndex_Flows.csv: "
            + ", ".join(sorted(missing_preferred))
        )
    inflow_files = sorted(PREFERRED_INFLOW_FILES)

    derived_index = read_index("FileIndex_DerivedTributaryFlows.csv")
    derived_tribflow_files = [row["FileName"] for row in derived_index]

    spill_index = read_index("FileIndex_Spill.csv")
    spill_files = [
        row["FileName"]
        for row in spill_index
        if row.get("SiteCode") in SPILL_SITE_CODES
    ]

    source_records: list[dict[str, str | int]] = []
    source_records.extend(download_series(lookup, storage_files, STORAGE_DIR, "storage"))
    source_records.extend(download_series(lookup, inflow_files, INFLOW_DIR, "headwater_inflow"))
    source_records.extend(
        download_series(lookup, derived_tribflow_files, TRIBFLOW_DIR, "derived_tributary_flow")
    )
    source_records.extend(download_series(lookup, spill_files, SPILL_DIR, "spill_or_release"))

    metadata = {
        "source": "New Zealand Electricity Authority EMI Azure Blob Storage",
        "dataset": "Hydrological Modelling Dataset",
        "purpose": "Curated raw time series for first NZ dry-year electricity model",
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "series_count": len(source_records),
        "selection": {
            "storage_site_codes": sorted(STORAGE_SITE_CODES),
            "preferred_headwater_inflow_files": sorted(PREFERRED_INFLOW_FILES),
            "derived_tributary_flows": "all entries in FileIndex_DerivedTributaryFlows.csv",
            "spill_site_codes": sorted(SPILL_SITE_CODES),
        },
        "series": source_records,
    }
    SERIES_METADATA_FILE.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(f"Downloaded {len(source_records)} model hydro series")


def main() -> None:
    print("Discovering EA HMD dataset layout...")
    all_hmd_blobs = list_blobs(HMD_ROOT)
    print(f"Found {len(all_hmd_blobs)} blobs under {HMD_ROOT}")
    write_hmd_manifest(all_hmd_blobs)
    fetch_file_indexes(all_hmd_blobs)
    fetch_model_hydro_series(all_hmd_blobs)

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
