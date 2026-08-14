from __future__ import annotations

import json
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


def download_if_changed(blob: dict[str, str], output: Path, previous_etag: str | None) -> bool:
    if output.exists() and previous_etag and blob["etag"] == previous_etag:
        print(f"Unchanged {output}")
        return False

    url = f"{CONTAINER}/{quote(blob['name'], safe='/()') }"
    response = requests.get(url, timeout=120)
    response.raise_for_status()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(response.content)
    print(f"Downloaded {output} ({len(response.content)} bytes)")
    return True


def main() -> None:
    previous = load_previous_metadata()
    previous_by_blob = {
        item["blob_name"]: item.get("source_etag", "")
        for dataset in previous.get("datasets", {}).values()
        for item in dataset.get("files", [])
    }

    metadata = {
        "source": "New Zealand Electricity Authority Azure Blob Storage",
        "purpose": "Historical generation and grid-demand inputs for NZ electricity model",
        "datasets": {},
    }

    changed = False
    for key, config in DATASETS.items():
        blobs = list_blobs(config["prefix"])
        selected = []
        for blob in blobs:
            yyyymm = yyyymm_from_name(blob["name"])
            if yyyymm is None or yyyymm < START_YYYYMM:
                continue
            if not blob["name"].endswith(config["suffix"]):
                continue
            selected.append(blob)

        selected.sort(key=lambda item: item["name"])
        files = []
        for blob in selected:
            output = config["output_dir"] / Path(blob["name"]).name
            changed |= download_if_changed(blob, output, previous_by_blob.get(blob["name"]))
            files.append(
                {
                    "blob_name": blob["name"],
                    "local_file": str(output),
                    "source_last_modified": blob["last_modified"],
                    "source_content_length": blob["content_length"],
                    "source_etag": blob["etag"],
                }
            )

        metadata["datasets"][key] = {
            "prefix": config["prefix"],
            "start_yyyymm": START_YYYYMM,
            "file_count": len(files),
            "files": files,
        }
        print(f"Selected {len(files)} {key} monthly files")

    # Only rewrite metadata when its source contents change. A check timestamp belongs in Actions logs,
    # not in versioned data, so a no-op daily run does not create a pointless commit.
    comparable_previous = previous.copy() if previous else {}
    if metadata != comparable_previous:
        METADATA_PATH.parent.mkdir(parents=True, exist_ok=True)
        METADATA_PATH.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        changed = True
        print(f"Wrote {METADATA_PATH}")

    print(f"Wholesale update completed; changed={changed}; checked_at_utc={datetime.now(timezone.utc).isoformat()}")


if __name__ == "__main__":
    main()
