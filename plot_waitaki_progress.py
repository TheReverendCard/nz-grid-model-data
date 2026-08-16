from __future__ import annotations

import csv
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt

DAILY = Path("data/model/waitaki_source_route_dispatch_v1_daily.csv")
OUT = Path("data/model/waitaki_wip_progress.png")

ENERGY_MWH_PER_MM3 = {
    "TKA": 1150.194,
    "PKI": 728.827,
    "OHA": 728.827,
}

SCENARIOS_TO_PLOT = [0.0, 1000.0, 2500.0]


def total_energy_gwh(row: dict[str, str], observed: bool) -> float:
    total_mwh = 0.0
    for site, value in ENERGY_MWH_PER_MM3.items():
        suffix = "observed_storage_mm3" if observed else "storage_mm3"
        total_mwh += float(row[f"{site}_{suffix}"]) * value
    return total_mwh / 1000.0


def main() -> None:
    by_scenario: dict[float, list[tuple[datetime, float]]] = defaultdict(list)
    observed: list[tuple[datetime, float]] = []

    with DAILY.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            scenario = float(row["scenario_incremental_solar_gwh"])
            if scenario not in SCENARIOS_TO_PLOT:
                continue
            d = datetime.strptime(row["date"], "%Y-%m-%d")
            by_scenario[scenario].append((d, total_energy_gwh(row, observed=False)))
            if scenario == 0.0:
                observed.append((d, total_energy_gwh(row, observed=True)))

    if not observed:
        raise RuntimeError("No zero-solar rows found for observed Waitaki storage trajectory")
    for scenario in SCENARIOS_TO_PLOT:
        if not by_scenario[scenario]:
            raise RuntimeError(f"Missing routed-dispatch rows for {scenario:g} GWh solar scenario")

    fig, ax = plt.subplots(figsize=(11, 5.8))

    x_obs, y_obs = zip(*observed)
    ax.plot(x_obs, y_obs, linewidth=2.4, label="Observed 2024 upper Waitaki")

    labels = {
        0.0: "Routed model: no extra solar",
        1000.0: "Routed model: +1.0 TWh solar",
        2500.0: "Routed model: +2.5 TWh solar",
    }
    for scenario in SCENARIOS_TO_PLOT:
        xs, ys = zip(*by_scenario[scenario])
        ax.plot(xs, ys, linewidth=1.6, label=labels[scenario])

    winter = datetime(2024, 5, 1)
    ax.axvline(winter, linestyle="--", linewidth=1.0)
    ax.text(winter, ax.get_ylim()[1], " 1 May", va="top", ha="left", fontsize=9)

    ax.set_title("WiP: Upper Waitaki stored-energy trajectory, 2024")
    ax.set_ylabel("Stored-energy equivalent (GWh)")
    ax.set_xlabel("2024")
    ax.set_ylim(bottom=0)
    ax.grid(alpha=0.25)
    ax.legend(loc="best", frameon=False)
    fig.tight_layout()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=150)
    plt.close(fig)
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
