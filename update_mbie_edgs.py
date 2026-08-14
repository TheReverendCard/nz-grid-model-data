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


def fetch(url: str, path: Path) -> dict[str, object]:
    headers = {"User-Agent": "nz-grid-model-data/1.0 (+https://github.com/TheReverendCard/nz-grid-model-data)"}
    response = requests.get(url, headers=headers, timeout=60)
    response.raise_for_status()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(response.content)
    return {
        "url": url,
        "bytes": len(response.content),
        "etag": response.headers.get("etag"),
        "last_modified": response.headers.get("last-modified"),
    }


def main() -> None:
    metadata = {
        "source": "MBIE Electricity Demand and Generation Scenarios 2024",
        "source_page": "https://www.mbie.govt.nz/building-and-energy/energy-and-natural-resources/energy-statistics-and-modelling/energy-modelling/electricity-demand-and-generation-scenarios",
        "notes": [
            "EDGS 2024 was published July 2024.",
            "MBIE updated the assumptions/results data files after publication, including fuel switching in November 2024 and sector-split distributed solar plus generation-stack inputs in March 2025.",
        ],
        "files": {},
    }
    for key, url in FILES.items():
        path = OUT_DIR / f"edgs_2024_{key}.xlsx"
        metadata["files"][key] = fetch(url, path)
        print(f"Fetched {key}: {path}")
    META.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {META}")


if __name__ == "__main__":
    main()
