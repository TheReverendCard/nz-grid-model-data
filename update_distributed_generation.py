from __future__ import annotations

import hashlib
import json
from pathlib import Path

import requests

DATA_DIR = Path("data/distributed_generation")
META_PATH = Path("data/metadata/distributed_generation_sources.json")

DG_TRENDS_URL = "https://www.emi.ea.govt.nz/Retail/Download/DataReport/CSV/GUEHMT"
DG_TRENDS_PARAMS = {
    "DateFrom": "20130901",
    "FuelType": "solar_all",
    "Show": "CapacityAvg",
    "_rsdr": "ALL",
    "_si": "v|4",
}
DG_TRENDS_PATH = DATA_DIR / "installed_dg_trends_solar_all.csv"

SOLAR_REGION_URL = (
    "https://emidatasets.blob.core.windows.net/publicdata/"
    "Datasets/Retail/SolarInstallations/SolarInstallationsByRegion.csv"
)
SOLAR_REGION_PATH = DATA_DIR / "solar_installations_by_region.csv"


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def fetch(url: str, *, params: dict[str, str] | None = None) -> tuple[bytes, dict[str, str]]:
    response = requests.get(url, params=params, timeout=120)
    response.raise_for_status()
    if not response.content:
        raise RuntimeError(f"Empty response from {response.url}")
    return response.content, {
        "url": response.url,
        "etag": response.headers.get("ETag", ""),
        "last_modified": response.headers.get("Last-Modified", ""),
        "content_type": response.headers.get("Content-Type", ""),
        "content_length": response.headers.get("Content-Length", str(len(response.content))),
    }


def write_if_changed(path: Path, content: bytes) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() == content:
        print(f"Unchanged {path}")
        return False
    path.write_bytes(content)
    print(f"Wrote {path} ({len(content):,} bytes)")
    return True


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    META_PATH.parent.mkdir(parents=True, exist_ok=True)

    trends_content, trends_headers = fetch(DG_TRENDS_URL, params=DG_TRENDS_PARAMS)
    if b"Month end" not in trends_content[:4096] and b"Month End" not in trends_content[:4096]:
        raise RuntimeError("GUEHMT download does not look like the expected CSV report")
    write_if_changed(DG_TRENDS_PATH, trends_content)

    region_content, region_headers = fetch(SOLAR_REGION_URL)
    if b"," not in region_content[:4096]:
        raise RuntimeError("SolarInstallationsByRegion download does not look like CSV")
    write_if_changed(SOLAR_REGION_PATH, region_content)

    metadata = {
        "source": "New Zealand Electricity Authority EMI",
        "datasets": {
            "installed_distributed_generation_trends_solar_all": {
                "description": "Installed distributed generation trends (GUEHMT), national Solar (All) series",
                "local_file": str(DG_TRENDS_PATH),
                "request_url": trends_headers["url"],
                "sha256": sha256_bytes(trends_content),
                "etag": trends_headers["etag"],
                "last_modified": trends_headers["last_modified"],
                "content_length": trends_headers["content_length"],
            },
            "solar_installations_by_region": {
                "description": "Current solar installation counts/capacity by region from the Electricity Registry",
                "local_file": str(SOLAR_REGION_PATH),
                "request_url": region_headers["url"],
                "sha256": sha256_bytes(region_content),
                "etag": region_headers["etag"],
                "last_modified": region_headers["last_modified"],
                "content_length": region_headers["content_length"],
            },
        },
    }
    encoded = (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode("utf-8")
    write_if_changed(META_PATH, encoded)
    print("Distributed generation update completed successfully.")


if __name__ == "__main__":
    main()
