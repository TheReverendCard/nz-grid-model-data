from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree

import requests

CONTAINER = "https://emidatasets.blob.core.windows.net/publicdata"
JADE_ROOT = "Datasets/Wholesale/Expected water values/"
OUT = Path("data/metadata/jade_manifest.json")
LATEST_OUT = Path("data/metadata/jade_latest.json")
WEEK_RE = re.compile(r"^Week\s+(\d+)$", re.IGNORECASE)


def list_blobs(prefix: str) -> list[dict[str, str]]:
    blobs: list[dict[str, str]] = []
    marker = ""
    page = 0
    while True:
        params = {"restype": "container", "comp": "list", "prefix": prefix}
        if marker:
            params["marker"] = marker
        response = requests.get(CONTAINER, params=params, timeout=60)
        response.raise_for_status()
        root = ElementTree.fromstring(response.content)
        page += 1
        page_count = 0
        for blob in root.findall(".//Blob"):
            name = blob.findtext("Name")
            if not name:
                continue
            blobs.append({
                "name": name,
                "last_modified": blob.findtext("Properties/Last-Modified") or "",
                "content_length": blob.findtext("Properties/Content-Length") or "",
                "etag": blob.findtext("Properties/Etag") or "",
                "source_url": f"{CONTAINER}/{quote(name, safe='/')}",
            })
            page_count += 1
        print(f"JADE Azure listing page {page}: {page_count} blobs")
        marker = root.findtext("NextMarker") or ""
        if not marker:
            break
    return blobs


def write_if_changed(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == text:
        print(f"Unchanged {path}")
    else:
        path.write_text(text, encoding="utf-8")
        print(f"Wrote {path}")


def build_latest_summary(years: dict) -> dict:
    numeric_years = sorted((int(year), year) for year in years if year.isdigit())
    if not numeric_years:
        raise RuntimeError("JADE manifest contains no numeric year folders")
    latest_year = numeric_years[-1][1]
    folders = years[latest_year]["folders"]

    week_candidates: list[tuple[int, str]] = []
    for folder in folders:
        match = WEEK_RE.match(folder)
        if match:
            week_candidates.append((int(match.group(1)), folder))
    if not week_candidates:
        raise RuntimeError(f"JADE year {latest_year} contains no weekly folders")

    _, latest_week = max(week_candidates)
    files = folders[latest_week]["files"]
    output_files = [item for item in files if "/Outputs/" in item["name"]]
    input_files = [item for item in files if "/Inputs/" in item["name"]]

    return {
        "source": "New Zealand Electricity Authority EMI JADE published weekly model files",
        "available_years": [year for _, year in numeric_years],
        "year_blob_counts": {year: years[year]["blob_count"] for _, year in numeric_years},
        "latest_year": latest_year,
        "latest_week": latest_week,
        "latest_week_modified": folders[latest_week]["latest_modified"],
        "latest_week_blob_count": folders[latest_week]["blob_count"],
        "output_file_count": len(output_files),
        "output_files": output_files,
        "input_file_count": len(input_files),
        "input_files": input_files,
        "note": "Compact discovery summary generated from the fully paginated JADE Azure manifest. Raw JADE files remain upstream; derived subsets may be stored locally for public charts and reproducibility.",
    }


def main() -> None:
    blobs = list_blobs(JADE_ROOT)
    if not blobs:
        raise RuntimeError(f"No JADE files discovered under {JADE_ROOT}")

    grouped: dict[str, dict[str, list[dict[str, str]]]] = defaultdict(lambda: defaultdict(list))
    for blob in blobs:
        relative = blob["name"].removeprefix(JADE_ROOT)
        parts = relative.split("/")
        year = parts[0] if parts else "unknown"
        folder = parts[1] if len(parts) > 1 else "_root"
        grouped[year][folder].append(blob)

    years = {}
    for year, folders in sorted(grouped.items()):
        years[year] = {
            "blob_count": sum(len(items) for items in folders.values()),
            "folders": {
                folder: {
                    "blob_count": len(items),
                    "latest_modified": max((item["last_modified"] for item in items), default=""),
                    "files": sorted(items, key=lambda item: item["name"]),
                }
                for folder, items in sorted(folders.items())
            },
        }

    manifest = {
        "source": "New Zealand Electricity Authority EMI Azure Blob Storage",
        "dataset": "Expected water values / JADE weekly inputs and outputs",
        "source_prefix": JADE_ROOT,
        "source_browser": "https://www.ea.govt.nz/data-and-insights/datasets/wholesale/expected-water-values/",
        "jade_code": "https://github.com/EPOC-NZ/JADE.jl",
        "policy": "Index and link upstream JADE files; do not mirror raw JADE inputs/outputs in this repository unless a transformed subset is required for a public visual or reproducibility.",
        "blob_count": len(blobs),
        "years": years,
    }

    write_if_changed(OUT, manifest)
    latest = build_latest_summary(years)
    write_if_changed(LATEST_OUT, latest)
    print(
        f"JADE latest: {latest['latest_year']} {latest['latest_week']} with "
        f"{latest['output_file_count']} output files and {latest['input_file_count']} input files"
    )


if __name__ == "__main__":
    main()
