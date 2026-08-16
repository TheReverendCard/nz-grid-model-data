# NZ Grid: Renewables, Hydro and Thermal Security

A public, reproducible visual evidence project for answering a practical New Zealand electricity question:

> As New Zealand builds more wind, solar, geothermal and batteries, do those resources preserve enough hydro and cover enough demand to reduce thermal generation and price-shock risk, including through dry periods?

This repository is **not intended to replace professional electricity-system models**. It brings together official data and outputs from established New Zealand modelling tools, then creates common scenario tables and public-facing visualisations.

## Modelling principle

Use the professional model that already solves the problem, and add new modelling only where there is a genuine gap.

- **JADE**: stochastic hydro scheduling, reservoir water values and inflow uncertainty. This is the preferred source for hydro-management behaviour rather than a home-grown reservoir dispatcher.
- **GEM**: long-term generation and transmission expansion scenarios.
- **vSPD**: detailed scheduling, pricing and dispatch analysis where market-level dispatch is required.
- **HSS**: deterministic hydro supply-security testing and a useful cross-check for dry-year/security framing.
- **HMD**: official hydrological infrastructure, constraints, flows, storage and spill data.
- **EA / EMI and MBIE**: observed generation/demand, distributed generation, investment pipeline and future demand scenarios.

## Upstream sources

Where an authoritative public dataset or model output is already directly accessible, this repository links to it rather than republishing a duplicate copy.

### Electricity Authority modelling tools

- JADE overview and datasets: https://www.ea.govt.nz/data-and-insights/tools-and-apis/
- JADE source: https://github.com/EPOC-NZ/JADE.jl
- Published JADE expected water values, weekly inputs and outputs: https://www.ea.govt.nz/data-and-insights/datasets/wholesale/expected-water-values/
- GEM overview/download information: https://www.emi.ea.govt.nz/wholesale/Tools/GEM
- vSPD, JADE, GEM and HSS tool index: https://www.emi.ea.govt.nz/Wholesale/Tools

### Core public datasets

- Generation investment pipeline: https://www.ea.govt.nz/data-and-insights/charts-and-dashboards/generation-investment-pipeline/
- Electricity Authority datasets and APIs: https://www.ea.govt.nz/data-and-insights/

The repository also uses MBIE Electricity Demand and Generation Scenarios (EDGS) for future electricity-demand pathways and the Electricity Authority Hydrological Modelling Dataset (HMD) where raw hydrological observations or constraints are needed.

## What this repository should publish

A CSV/JSON belongs here when it adds value that is **not already available upstream**, for example:

- a calibrated common demand scenario derived from EDGS plus observed EA demand;
- a compact cross-source scenario table joining demand, committed generation and storage assumptions;
- historical replay summary metrics assembled from professional-model outputs;
- chart-ready monthly/weekly aggregates;
- derived measures such as hydro preserved by additional renewables, thermal generation avoided, minimum storage, or peak thermal requirement;
- provenance metadata identifying the source model, source date, scenario and transformation.

Raw JADE/GEM/EA files should generally **not** be mirrored merely for convenience. The README or provenance metadata should point to the authoritative source instead.

## Visual story

The first public-facing version is intended to answer four questions visually.

### 1. Seasonal hydro storage

Show observed and modelled hydro storage through the year, including historical dry periods and future-fleet scenarios. The important question is whether additional renewable generation allows the system to enter and move through winter with more water remaining in storage.

### 2. What fills the seasonal gap?

Show demand against stacked wind, solar, geothermal, hydro, batteries and thermal generation. This makes visible whether additional renewables fill periods that currently require hydro drawdown or thermal generation.

### 3. Hydro preserved by renewables

Quantify when wind and solar defer hydro generation and whether that saved water remains available during later high-demand or dry periods. This is the core link between summer renewable production and winter security.

### 4. Residual thermal requirement

For each scenario and historical inflow/weather replay, show:

- annual thermal generation (GWh);
- peak thermal capacity required (MW);
- duration/frequency of significant thermal use;
- minimum hydro storage;
- scarcity or shortage metrics where available;
- water-value / price-stress indicators where professional model outputs support them.

This distinguishes **thermal energy dependence** from **thermal capacity as insurance**.

## Initial scenario set

The first comparisons should remain deliberately simple and defensible:

1. recent observed system;
2. future EDGS Reference demand with operating + committed generation;
3. Reference demand with committed + actively pursued generation;
4. a higher-renewables case;
5. selected historical dry/inflow replays for each future fleet.

Project-status definitions should follow the Electricity Authority generation investment pipeline rather than inventing a separate classification.

## Common result schema

Derived scenario outputs should converge on a compact table with fields such as:

```text
scenario_id
model
model_version_or_run_date
demand_scenario
fleet_scenario
hydrology_or_inflow_sequence
time_period
demand_gwh
hydro_gwh
wind_gwh
solar_gwh
geothermal_gwh
battery_charge_gwh
battery_discharge_gwh
thermal_gwh
hydro_storage_gwh
hydro_storage_pct
thermal_peak_mw
water_value_nzd_per_mwh
unserved_energy_mwh
source_url
```

Not every model will populate every field. Missing values should remain explicit rather than being inferred silently.

## Current development status

Earlier work in this branch explored reconstructing the Waitaki hydro network directly from HMD data. That work remains useful as an audit/learning trail, but **the project is now pivoting away from a custom national hydro dispatcher**. Future development should preferentially ingest JADE and other professional-model outputs and focus on scenario harmonisation, provenance and visualisation.

The experimental Waitaki scripts should not be treated as the authoritative hydro model.

## Reproducibility

Every derived chart/table should identify:

- authoritative upstream source;
- retrieval/run date;
- scenario assumptions;
- transformation code;
- professional model used, where applicable;
- whether the result is observed, modelled, or derived.

The aim is to make the visual story simple without making the methodology opaque.

## Licence

Repository code is licensed under AGPL-3.0 unless otherwise noted. Upstream datasets and modelling tools retain their own licences and terms.