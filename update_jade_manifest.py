from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree

import requests

CONTAINER = "https://emidatasets.blob.core.windows.net/publicdata"
JADE_ROOT = "Datasets/Wholesale/Expected water values/"
OUT = Path("data/metadata/jade_manifest.json")


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

    OUT.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(manifest, indent=2) + "\n"
    if OUT.exists() and OUT.read_text(encoding="utf-8") == text:
        print(f"Unchanged {OUT}")
    else:
        OUT.write_text(text, encoding="utf-8")
        print(f"Wrote {OUT} with {len(blobs)} upstream JADE blobs")


if __name__ == "__main__":
    main()
