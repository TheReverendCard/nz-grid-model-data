from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import xml.etree.ElementTree as ET

import requests

from update_data import (
    HMD_ROOT,
    INFRA_PREFIX,
    MANIFEST_FILE,
    fetch_file_indexes,
    fetch_infrastructure,
    fetch_model_hydro_series,
    load_json,
    write_hmd_manifest,
)

CONTAINER = "https://emidatasets.blob.core.windows.net/publicdata"
INFRA_COMPONENT = INFRA_PREFIX.removeprefix(HMD_ROOT).rstrip("/")
FLOW_COMPONENT_PREFIX = "2_Flows_"
STORAGE_COMPONENT_PREFIX = "3_StorageAndSpill_"


def list_blobs(prefix: str) -> list[dict[str, str]]:
    blobs: list[dict[str, str]] = []
    marker = ""
    while True:
        params = {"restype": "container", "comp": "list", "prefix": prefix}
        if marker:
            params["marker"] = marker
        response = requests.get(CONTAINER, params=params, timeout=120)
        response.raise_for_status()
        root = ET.fromstring(response.content)
        for blob in root.findall("./Blobs/Blob"):
            name = blob.findtext("Name")
            if not name:
                continue
            props = blob.find("Properties")
            blobs.append(
                {
                    "name": name,
                    "last_modified": props.findtext("Last-Modified", default="") if props is not None else "",
                    "content_length": props.findtext("Content-Length", default="") if props is not None else "",
                    "etag": props.findtext("Etag", default="") if props is not None else "",
                }
            )
        marker = root.findtext("NextMarker", default="")
        if not marker:
            break
    return blobs


def list_component_prefixes() -> list[str]:
    prefixes: list[str] = []
    marker = ""
    while True:
        params = {
            "restype": "container",
            "comp": "list",
            "prefix": HMD_ROOT,
            "delimiter": "/",
        }
        if marker:
            params["marker"] = marker
        response = requests.get(CONTAINER, params=params, timeout=60)
        response.raise_for_status()
        root = ET.fromstring(response.content)
        for node in root.findall("./Blobs/BlobPrefix"):
            name = node.findtext("Name", default="")
            if name.startswith(HMD_ROOT):
                component = name.removeprefix(HMD_ROOT).rstrip("/")
                if component:
                    prefixes.append(component)
        marker = root.findtext("NextMarker", default="")
        if not marker:
            break
    return sorted(set(prefixes))


def known_manifest_components() -> set[str]:
    manifest = load_json(MANIFEST_FILE, {})
    components = manifest.get("components", {}) if isinstance(manifest, dict) else {}
    if not isinstance(components, dict):
        return set()
    return {str(name) for name in components if str(name) != "_root"}


def latest_component(components: list[str], prefix: str) -> str:
    candidates = sorted(component for component in components if component.startswith(prefix))
    if not candidates:
        raise RuntimeError(f"No HMD component matched {prefix!r}")
    return candidates[-1]


def model_component_names(components: list[str]) -> list[str]:
    if INFRA_COMPONENT not in components:
        raise RuntimeError(f"HMD infrastructure component disappeared: {INFRA_COMPONENT}")
    return [
        INFRA_COMPONENT,
        latest_component(components, FLOW_COMPONENT_PREFIX),
        latest_component(components, STORAGE_COMPONENT_PREFIX),
    ]


def list_model_blobs(components: list[str]) -> list[dict[str, str]]:
    selected = model_component_names(components)
    print("HMD model-relevant components:")
    for component in selected:
        print(f"  {component}")

    blobs: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=len(selected)) as pool:
        futures = {
            pool.submit(list_blobs, f"{HMD_ROOT}{component}/"): component
            for component in selected
        }
        for future in as_completed(futures):
            component = futures[future]
            component_blobs = future.result()
            print(f"Listed {len(component_blobs)} blobs in {component}")
            blobs.extend(component_blobs)
    return blobs


def refresh_full_manifest() -> None:
    print("Refreshing full HMD discovery manifest...")
    all_blobs = list_blobs(HMD_ROOT)
    print(f"Found {len(all_blobs)} blobs under {HMD_ROOT}")
    write_hmd_manifest(all_blobs)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--full-discovery",
        action="store_true",
        help="Refresh the complete HMD discovery manifest before checking model-relevant components.",
    )
    args = parser.parse_args()

    components = list_component_prefixes()
    if not components:
        raise RuntimeError("No HMD top-level component prefixes were discovered")

    previous_components = known_manifest_components()
    topology_changed = bool(previous_components) and set(components) != previous_components
    missing_manifest = not previous_components

    if args.full_discovery or topology_changed or missing_manifest:
        reason = (
            "manual/scheduled full discovery"
            if args.full_discovery
            else "top-level component topology changed"
            if topology_changed
            else "no usable prior manifest"
        )
        print(f"Full HMD manifest refresh required: {reason}")
        refresh_full_manifest()
    else:
        print("HMD top-level component topology unchanged; skipped full recursive discovery")

    model_blobs = list_model_blobs(components)
    fetch_file_indexes(model_blobs)
    fetch_model_hydro_series(model_blobs)
    fetch_infrastructure(model_blobs)

    mode = "full" if args.full_discovery else "fast"
    print(f"EA HMD source check completed successfully; mode={mode}")


if __name__ == "__main__":
    main()
