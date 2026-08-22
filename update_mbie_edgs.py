from __future__ import annotations

import json
from pathlib import Path

import requests

BASE = "https://www.mbie.govt.nz/assets/Data-Files/Energy"
FILES = {
    "assumptions": f"{BASE}/electricity-demand-generation-scenarios-2024-assumptions.xlsx",
    "results": f"{BASE}/electricity-demand-generation-scenarios-2024-results.xlsx",
}
OUT_DIR = Path("data/mbie/edgs2024")
META = OUT_DIR / "sources.json"


def load_previous_metadata() -> dict[str, object]:
    if not META.exists():
        return {}
    try:
        return json.loads(META.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_json_if_changed(path: Path, payload: dict[str, object]) -> bool:
    text = json.dumps(payload, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == text:
        print(f"Unchanged {path}")
        return False
    path.write_text(text, encoding="utf-8")
    print(f"Wrote {path}")
    return True


def fetch(url: str, path: Path, previous: dict[str, object]) -> tuple[dict[str, object], bool]:
    headers = {
        "User-Agent": "nz-grid-model-data/1.0 (+https://github.com/TheReverendCard/nz-grid-model-data)"
    }
    if path.exists():
        etag = str(previous.get("etag") or "")
        last_modified = str(previous.get("last_modified") or "")
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

    response = requests.get(url, headers=headers, timeout=60)
    if response.status_code == 304 and path.exists():
        print(f"Unchanged {path} (HTTP 304)")
        return {
            "url": str(previous.get("url") or url),
            "bytes": path.stat().st_size,
            "etag": previous.get("etag"),
            "last_modified": previous.get("last_modified"),
        }, False

    response.raise_for_status()
    path.parent.mkdir(parents=True, exist_ok=True)
    changed = not path.exists() or path.read_bytes() != response.content
    if changed:
        path.write_bytes(response.content)
        print(f"Wrote {path} ({len(response.content):,} bytes)")
    else:
        print(f"Unchanged {path} (byte-identical response)")

    return {
        "url": response.url,
        "bytes": len(response.content),
        "etag": response.headers.get("etag"),
        "last_modified": response.headers.get("last-modified"),
    }, changed


def main() -> None:
    previous_meta = load_previous_metadata()
    previous_files = (
        previous_meta.get("files", {})
        if isinstance(previous_meta.get("files"), dict)
        else {}
    )

    metadata: dict[str, object] = {
        "source": "MBIE Electricity Demand and Generation Scenarios 2024",
        "source_page": "https://www.mbie.govt.nz/building-and-energy/energy-and-natural-resources/energy-statistics-and-modelling/energy-modelling/electricity-demand-and-generation-scenarios",
        "notes": [
            "EDGS 2024 was published July 2024.",
            "MBIE updated the assumptions/results data files after publication, including fuel switching in November 2024 and sector-split distributed solar plus generation-stack inputs in March 2025.",
        ],
        "files": {},
    }

    any_changed = False
    files_out = metadata["files"]
    assert isinstance(files_out, dict)
    for key, url in FILES.items():
        path = OUT_DIR / f"edgs_2024_{key}.xlsx"
        previous = previous_files.get(key, {})
        if not isinstance(previous, dict):
            previous = {}
        record, changed = fetch(url, path, previous)
        files_out[key] = record
        any_changed |= changed

    metadata_changed = write_json_if_changed(META, metadata)
    print(
        f"MBIE EDGS check completed; workbook_changed={any_changed}; "
        f"metadata_changed={metadata_changed}"
    )


if __name__ == "__main__":
    main()
