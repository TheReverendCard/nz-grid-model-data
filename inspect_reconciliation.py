from __future__ import annotations

import csv
import gzip
import io
import json
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

CONTAINER = "https://emidatasets.blob.core.windows.net/publicdata"
PREFIX = "Datasets/Wholesale/Volumes/Reconciliation/2024/"
OUTPUT = Path("data/metadata/reconciliation_schema.json")
FILE_RE = re.compile(r"ReconciledInjectionAndOfftake_(\d{6})_(\d{8})_(\d{6})\.csv\.gz$")


def list_blobs(prefix: str) -> list[dict[str, str]]:
    url = f"{CONTAINER}?restype=container&comp=list&prefix={prefix}"
    response = requests.get(url, timeout=60)
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


def choose_latest_january(blobs: list[dict[str, str]]) -> dict[str, str]:
    candidates: list[tuple[str, str, dict[str, str]]] = []
    for blob in blobs:
        match = FILE_RE.search(blob["name"])
        if match and match.group(1) == "202401":
            candidates.append((match.group(2), match.group(3), blob))
    if not candidates:
        raise RuntimeError("No January 2024 reconciliation file found")
    candidates.sort()
    return candidates[-1][2]


def inspect_csv(blob: dict[str, str]) -> dict[str, object]:
    url = f"{CONTAINER}/{blob['name']}"
    response = requests.get(url, timeout=180)
    response.raise_for_status()

    with gzip.GzipFile(fileobj=io.BytesIO(response.content), mode="rb") as gz:
        text = io.TextIOWrapper(gz, encoding="utf-8-sig", newline="")
        reader = csv.DictReader(text)
        fieldnames = reader.fieldnames or []
        samples: defaultdict[str, set[str]] = defaultdict(set)
        rows_seen = 0
        for row in reader:
            rows_seen += 1
            for field in fieldnames:
                value = (row.get(field) or "").strip()
                if value and len(samples[field]) < 12:
                    samples[field].add(value)
            if rows_seen >= 5000:
                break

    return {
        "source": "New Zealand Electricity Authority reconciled injection and offtake (GR-010)",
        "blob_name": blob["name"],
        "source_url": url,
        "etag": blob["etag"],
        "last_modified": blob["last_modified"],
        "content_length": blob["content_length"],
        "inspected_at_utc": datetime.now(timezone.utc).isoformat(),
        "rows_sampled": rows_seen,
        "fieldnames": fieldnames,
        "sample_values": {field: sorted(values) for field, values in samples.items()},
    }


def main() -> None:
    blobs = list_blobs(PREFIX)
    blob = choose_latest_january(blobs)
    print(f"Inspecting {blob['name']}")
    result = inspect_csv(blob)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT}")
    print("Fields:", ", ".join(result["fieldnames"]))


if __name__ == "__main__":
    main()
