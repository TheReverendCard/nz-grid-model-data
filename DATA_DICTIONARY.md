# Data dictionary

## `data/public/renewable_share_daily.csv`
Daily observed generation aggregated from EA `Generation_MD`. Renewable classification includes hydro, geothermal, wind, solar and clearly identified biomass/biogas. Ambiguous industrial cogeneration is not automatically counted renewable.

## `data/public/generation_mix_daily.csv`
Daily MWh by simplified fuel group, derived from plant-level EA generation data.

## `data/public/demand_daily.csv`
Daily MWh from EA `Grid_export`. This is a grid-export measure, not a direct estimate of gross behind-the-meter consumption.

## `data/public/hydro_storage_daily.csv`
National sum of active, contingent and total usable storage across the curated major-lake HMD series, in Mm³.

## `data/public/solar_installations_monthly.csv`
Registry-based solar ICP counts/capacity and new-install rates. `Solar+Batteries` was introduced as a registry category in November 2023; older classifications may be inconsistent. Battery counts are derived as `Solar (All) - Solar (without battery)`. Non-residential is derived as all ICPs minus residential.

## `data/pipeline/transpower_generation_storage_pipeline.csv`
Current Transpower generation and energy-storage connection projects. This is a connection/delivery pipeline and is not identical to the EA's broader investment-pipeline dashboard.

## `data/public/wholesale_prices_daily.csv`
Daily average wholesale prices at three reference nodes: Otahuhu (`OTA2201`), Haywards (`HAY2201`) and Benmore (`BEN2201`), plus their simple mean. It is not a demand-weighted national settlement price.

## `data/public/renewables_vs_price_daily.csv`
Date-aligned renewable share and reference-node mean wholesale price for exploratory correlation charts. Correlation is descriptive, not causal.
