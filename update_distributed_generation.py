from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import requests

DATA_DIR = Path("data/distributed_generation")
META_PATH = Path("data/metadata/distributed_generation_sources.json")

DG_TRENDS_URL = "https://www.emi.ea.govt.nz/Retail/Download/DataReport/CSV/GUEHMT"
DG_TRENDS_BASE_PARAMS = {
    "DateFrom": "20130901",
    "FuelType": "solar_all",
    "Show": "CapacityAvg",
    "_rsdr": "ALL",
    "_si": "v|4",
}
DG_TRENDS_PATH = DATA_DIR / "installed_dg_trends_solar_all.csv"
DG_SOLAR_ONLY_TRENDS_PATH = DATA_DIR / "installed_dg_trends_solar_only.csv"
DG_SOLAR_BATTERY_TRENDS_PATH = DATA_DIR / "installed_dg_trends_solar_with_battery.csv"
DG_RESIDENTIAL_TRENDS_PATH = DATA_DIR / "installed_dg_trends_solar_residential.csv"

SOLAR_REGION_URL = (
    "https://emidatasets.blob.core.windows.net/publicdata/"
    "Datasets/Retail/SolarInstallations/SolarInstallationsByRegion.csv"
)
SOLAR_REGION_PATH = DATA_DIR / "solar_installations_by_region.csv"

SOLAR_STREET_URL = (
    "https://emidatasets.blob.core.windows.net/publicdata/"
    "Datasets/Retail/SolarInstallations/SolarInstallationsByStreet.csv"
)
SOLAR_STREET_PATH = DATA_DIR / "solar_installations_by_street.csv"


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


def validate_guehmt(content: bytes, label: str) -> None:
    sample = content[:4096]
    if b"Month end" not in sample and b"Month End" not in sample:
        raise RuntimeError(f"{label} GUEHMT download does not look like the expected CSV report")


def validate_csv(content: bytes, label: str) -> None:
    sample = content[:4096]
    if b"," not in sample or b"\n" not in sample:
        raise RuntimeError(f"{label} download does not look like CSV")


def write_if_changed(path: Path, content: bytes) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() == content:
        print(f"Unchanged {path}")
        return False
    path.write_bytes(content)
    print(f"Wrote {path} ({len(content):,} bytes)")
    return True


def metadata_record(description: str, path: Path, content: bytes, headers: dict[str, str]) -> dict[str, str]:
    return {
        "description": description,
        "local_file": str(path),
        "request_url": headers["url"],
        "sha256": sha256_bytes(content),
        "etag": headers["etag"],
        "last_modified": headers["last_modified"],
        "content_length": headers["content_length"],
    }


def write_github_outputs(values: dict[str, bool]) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with Path(output_path).open("a", encoding="utf-8") as handle:
        for name, value in values.items():
            handle.write(f"{name}={'true' if value else 'false'}\n")


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    META_PATH.parent.mkdir(parents=True, exist_ok=True)

    trends_content, trends_headers = fetch(DG_TRENDS_URL, params=DG_TRENDS_BASE_PARAMS)
    validate_guehmt(trends_content, "All-solar")
    trends_changed = write_if_changed(DG_TRENDS_PATH, trends_content)

    solar_only_params = dict(DG_TRENDS_BASE_PARAMS)
    solar_only_params["FuelType"] = "solar"
    solar_only_content, solar_only_headers = fetch(DG_TRENDS_URL, params=solar_only_params)
    validate_guehmt(solar_only_content, "Solar-only")
    solar_only_changed = write_if_changed(DG_SOLAR_ONLY_TRENDS_PATH, solar_only_content)

    solar_battery_params = dict(DG_TRENDS_BASE_PARAMS)
    solar_battery_params["FuelType"] = "solarplusbattery"
    solar_battery_content, solar_battery_headers = fetch(DG_TRENDS_URL, params=solar_battery_params)
    validate_guehmt(solar_battery_content, "Solar-with-battery")
    solar_battery_changed = write_if_changed(DG_SOLAR_BATTERY_TRENDS_PATH, solar_battery_content)

    residential_params = dict(DG_TRENDS_BASE_PARAMS)
    residential_params["MarketSegment"] = "Res"
    residential_content, residential_headers = fetch(DG_TRENDS_URL, params=residential_params)
    validate_guehmt(residential_content, "Residential all-solar")
    if b"Residential" not in residential_content[:2048] and b"Res" not in residential_content[:2048]:
        print("Warning: residential GUEHMT response header did not explicitly echo the market segment")
    residential_changed = write_if_changed(DG_RESIDENTIAL_TRENDS_PATH, residential_content)

    region_content, region_headers = fetch(SOLAR_REGION_URL)
    validate_csv(region_content, "SolarInstallationsByRegion")
    region_changed = write_if_changed(SOLAR_REGION_PATH, region_content)

    street_content, street_headers = fetch(SOLAR_STREET_URL)
    validate_csv(street_content, "SolarInstallationsByStreet")
    street_changed = write_if_changed(SOLAR_STREET_PATH, street_content)

    metadata = {
        "source": "New Zealand Electricity Authority EMI",
        "datasets": {
            "installed_distributed_generation_trends_solar_all": metadata_record(
                "Installed distributed generation trends (GUEHMT), national solar including solar+battery categories, all ICPs",
                DG_TRENDS_PATH,
                trends_content,
                trends_headers,
            ),
            "installed_distributed_generation_trends_solar_only": metadata_record(
                "Installed distributed generation trends (GUEHMT), national Solar (without battery)",
                DG_SOLAR_ONLY_TRENDS_PATH,
                solar_only_content,
                solar_only_headers,
            ),
            "installed_distributed_generation_trends_solar_with_battery": metadata_record(
                "Installed distributed generation trends (GUEHMT), national Solar (with battery), explicit EA fuel type solarplusbattery",
                DG_SOLAR_BATTERY_TRENDS_PATH,
                solar_battery_content,
                solar_battery_headers,
            ),
            "installed_distributed_generation_trends_solar_residential": metadata_record(
                "Installed distributed generation trends (GUEHMT), national solar including solar+battery categories, residential ICPs",
                DG_RESIDENTIAL_TRENDS_PATH,
                residential_content,
                residential_headers,
            ),
            "solar_installations_by_region": metadata_record(
                "Current solar installation counts/capacity by region and market segment from the Electricity Registry",
                SOLAR_REGION_PATH,
                region_content,
                region_headers,
            ),
            "solar_installations_by_street": metadata_record(
                "Current street-level solar installation counts/capacity and market segment from the Electricity Registry",
                SOLAR_STREET_PATH,
                street_content,
                street_headers,
            ),
        },
    }
    encoded = (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode("utf-8")
    metadata_changed = write_if_changed(META_PATH, encoded)

    solar_model_inputs_changed = trends_changed or street_changed
    battery_model_inputs_changed = trends_changed or solar_only_changed or solar_battery_changed
    distributed_generation_changed = (
        trends_changed
        or solar_only_changed
        or solar_battery_changed
        or residential_changed
        or region_changed
        or street_changed
        or metadata_changed
    )
    write_github_outputs(
        {
            "solar_model_inputs_changed": solar_model_inputs_changed,
            "battery_model_inputs_changed": battery_model_inputs_changed,
            "distributed_generation_changed": distributed_generation_changed,
        }
    )
    print(
        "Change signals: "
        f"solar_model_inputs_changed={solar_model_inputs_changed}, "
        f"battery_model_inputs_changed={battery_model_inputs_changed}, "
        f"distributed_generation_changed={distributed_generation_changed}"
    )
    print("Distributed generation update completed successfully.")


if __name__ == "__main__":
    main()
