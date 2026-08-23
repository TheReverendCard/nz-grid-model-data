from __future__ import annotations

import csv
import json
from collections import defaultdict
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

STORAGE_SITE_CODES = {"WKA", "TPO", "WPU", "WAN", "HWE", "MAN", "TAU", "OHA", "PKI", "TKA"}
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
SPILL_SITE_CODES = {"WKA", "TPO", "HWE", "MAN", "TAU", "OHA", "PKI", "TKA"}


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def write_json_if_changed(path: Path, value: object) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == text:
        print(f"Unchanged {path}")
        return False
    path.write_text(text, encoding="utf-8")
    print(f"Wrote {path}")
    return True


def list_blobs(prefix: str) -> list[dict[str, str]]:
    response = requests.get(
        CONTAINER,
        params={"restype": "container", "comp": "list", "prefix": prefix},
        timeout=60,
    )
    response.raise_for_status()
    root = ElementTree.fromstring(response.content)
    blobs: list[dict[str, str]] = []
    for blob in root.findall(".//Blob"):
        name = blob.findtext("Name")
        if not name:
            continue
        blobs.append({
            "name": name,
            "last_modified": blob.findtext("Properties/Last-Modified") or "",
            "content_length": blob.findtext("Properties/Content-Length") or "",
            "etag": blob.findtext("Properties/Etag") or "",
        })
    return blobs


def choose_latest_infrastructure_csv(blobs: list[dict[str, str]]) -> dict[str, str]:
    candidates = [b for b in blobs if b["name"].endswith(TARGET_SUFFIX)]
    if not candidates:
        raise RuntimeError("No HMD infrastructure CSV matched the expected filename suffix")
    return max(candidates, key=lambda b: Path(b["name"]).name)


def blob_url(blob_name: str) -> str:
    return f"{CONTAINER}/{quote(blob_name, safe='/')}"


def same_source(old: object, blob: dict[str, str]) -> bool:
    return isinstance(old, dict) and old.get("blob_name") == blob["name"] and old.get("source_etag") == blob["etag"]


def count_csv_rows(path: Path) -> int:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = sum(1 for _ in csv.reader(handle))
    if rows < 2:
        raise RuntimeError(f"File {path} does not look like a populated CSV")
    return rows - 1


def download_file(url: str, output_path: Path) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    response = requests.get(url, timeout=120)
    response.raise_for_status()
    if not response.content:
        raise RuntimeError(f"EA returned an empty file for {url}")
    output_path.write_bytes(response.content)
    rows = count_csv_rows(output_path)
    print(f"Downloaded {output_path} ({rows} data rows)")
    return rows


def source_record(blob: dict[str, str], output_path: Path, data_rows: int, **extra) -> dict[str, object]:
    return {
        **extra,
        "local_file": str(output_path),
        "blob_name": blob["name"],
        "source_url": blob_url(blob["name"]),
        "source_last_modified": blob["last_modified"],
        "source_content_length": blob["content_length"],
        "source_etag": blob["etag"],
        "data_rows": data_rows,
    }


def write_hmd_manifest(blobs: list[dict[str, str]]) -> None:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for blob in blobs:
        relative = blob["name"].removeprefix(HMD_ROOT)
        component = relative.split("/", 1)[0] if "/" in relative else "_root"
        grouped[component].append(blob)
    manifest = {
        "source": "New Zealand Electricity Authority EMI Azure Blob Storage",
        "prefix": HMD_ROOT,
        "blob_count": len(blobs),
        "components": {
            component: sorted(items, key=lambda item: item["name"])
            for component, items in sorted(grouped.items())
        },
    }
    write_json_if_changed(MANIFEST_FILE, manifest)


def fetch_file_indexes(blobs: list[dict[str, str]]) -> None:
    indexes = [b for b in blobs if Path(b["name"]).name.startswith("FileIndex") and b["name"].lower().endswith(".csv")]
    if not indexes:
        raise RuntimeError("No HMD FileIndex CSV files were discovered")
    previous_meta = load_json(INDEX_METADATA_FILE, {})
    previous_records = {
        r.get("blob_name"): r for r in previous_meta.get("indexes", [])
        if isinstance(r, dict)
    } if isinstance(previous_meta, dict) else {}
    basename_counts: dict[str, int] = defaultdict(int)
    for blob in indexes:
        basename_counts[Path(blob["name"]).name] += 1
    records = []
    for blob in sorted(indexes, key=lambda item: item["name"]):
        basename = Path(blob["name"]).name
        local_name = f"{Path(blob['name']).parent.name}_{basename}" if basename_counts[basename] > 1 else basename
        path = INDEX_DIR / local_name
        old = previous_records.get(blob["name"])
        if path.exists() and same_source(old, blob):
            rows = int(old.get("data_rows", count_csv_rows(path)))
            print(f"Unchanged {path}")
        else:
            rows = download_file(blob_url(blob["name"]), path)
        records.append(source_record(blob, path, rows))
    write_json_if_changed(INDEX_METADATA_FILE, {
        "source": "New Zealand Electricity Authority EMI Azure Blob Storage",
        "dataset": "Hydrological Modelling Dataset",
        "index_count": len(records),
        "indexes": records,
    })
    print(f"Checked {len(records)} HMD file indexes")


def read_index(filename: str) -> list[dict[str, str]]:
    with (INDEX_DIR / filename).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def blob_by_basename(blobs: list[dict[str, str]]) -> dict[str, dict[str, str]]:
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


def download_series(blob_lookup, filenames, output_dir, series_type, previous_records):
    records = []
    for filename in sorted(set(filenames)):
        blob = blob_lookup.get(filename)
        if blob is None:
            raise RuntimeError(f"Could not uniquely locate HMD source file for selected {series_type}: {filename}")
        path = output_dir / filename
        old = previous_records.get(blob["name"])
        if path.exists() and same_source(old, blob):
            rows = int(old.get("data_rows", count_csv_rows(path)))
            print(f"Unchanged {path}")
        else:
            rows = download_file(blob_url(blob["name"]), path)
        records.append(source_record(blob, path, rows, series_type=series_type))
    return records


def fetch_model_hydro_series(blobs: list[dict[str, str]]) -> None:
    lookup = blob_by_basename(blobs)
    storage_files = [r["FileName"] for r in read_index("FileIndex_Storage.csv") if r.get("SiteCode") in STORAGE_SITE_CODES]
    flow_index = read_index("FileIndex_Flows.csv")
    available_flow_files = {r["FileName"] for r in flow_index}
    missing = PREFERRED_INFLOW_FILES - available_flow_files
    if missing:
        raise RuntimeError("Preferred HMD inflow series disappeared: " + ", ".join(sorted(missing)))
    inflow_files = sorted(PREFERRED_INFLOW_FILES)
    derived_files = [r["FileName"] for r in read_index("FileIndex_DerivedTributaryFlows.csv")]
    spill_files = [r["FileName"] for r in read_index("FileIndex_Spill.csv") if r.get("SiteCode") in SPILL_SITE_CODES]

    previous_meta = load_json(SERIES_METADATA_FILE, {})
    previous_records = {
        r.get("blob_name"): r for r in previous_meta.get("series", [])
        if isinstance(r, dict)
    } if isinstance(previous_meta, dict) else {}

    records = []
    records.extend(download_series(lookup, storage_files, STORAGE_DIR, "storage", previous_records))
    records.extend(download_series(lookup, inflow_files, INFLOW_DIR, "headwater_inflow", previous_records))
    records.extend(download_series(lookup, derived_files, TRIBFLOW_DIR, "derived_tributary_flow", previous_records))
    records.extend(download_series(lookup, spill_files, SPILL_DIR, "spill_or_release", previous_records))

    write_json_if_changed(SERIES_METADATA_FILE, {
        "source": "New Zealand Electricity Authority EMI Azure Blob Storage",
        "dataset": "Hydrological Modelling Dataset",
        "purpose": "Curated raw time series for first NZ dry-year electricity model",
        "series_count": len(records),
        "selection": {
            "storage_site_codes": sorted(STORAGE_SITE_CODES),
            "preferred_headwater_inflow_files": sorted(PREFERRED_INFLOW_FILES),
            "derived_tributary_flows": "all entries in FileIndex_DerivedTributaryFlows.csv",
            "spill_site_codes": sorted(SPILL_SITE_CODES),
        },
        "series": records,
    })
    print(f"Checked {len(records)} model hydro series")


def fetch_infrastructure(blobs: list[dict[str, str]]) -> None:
    selected = choose_latest_infrastructure_csv([b for b in blobs if b["name"].startswith(INFRA_PREFIX)])
    previous = load_json(METADATA_FILE, {})
    if OUTPUT_FILE.exists() and same_source(previous, selected):
        rows = int(previous.get("data_rows", count_csv_rows(OUTPUT_FILE))) if isinstance(previous, dict) else count_csv_rows(OUTPUT_FILE)
        print(f"Unchanged {OUTPUT_FILE}")
    else:
        rows = download_file(blob_url(selected["name"]), OUTPUT_FILE)
    metadata = {
        "source": "New Zealand Electricity Authority EMI Azure Blob Storage",
        "dataset": "Hydrological Modelling Dataset",
        "component": "Infrastructure and Hydro Constraint Attributes",
        "blob_name": selected["name"],
        "source_url": blob_url(selected["name"]),
        "source_last_modified": selected["last_modified"],
        "source_content_length": selected["content_length"],
        "source_etag": selected["etag"],
        "data_rows": rows,
    }
    write_json_if_changed(METADATA_FILE, metadata)


def main() -> None:
    # The public workflow still invokes update_data.py. Route that stable entry
    # point through the targeted source checker, which performs shallow topology
    # discovery on routine runs and full recursive discovery when requested or
    # when a new HMD component version appears.
    from update_hmd_sources import main as optimized_source_main

    optimized_source_main()


if __name__ == "__main__":
    main()
