# EV feed-in peak capacity vs small distributed solar

## Purpose

This maintained visual compares projected **EV feed-in peak power capacity** with projected **small distributed solar nameplate capacity** from 2026 to 2035.

It is intended to answer a narrow capacity question: under plausible EV uptake, charger adoption, export-power and customer-participation assumptions, when could the aggregate export capability of New Zealand's light EV fleet approach or exceed the installed capacity of the repo's small distributed solar curve?

The comparison is **capacity, not energy**. Equal MW values do not mean equal annual generation, equal availability or equal system value. Solar is primarily a daytime generation resource. EV export could potentially be scheduled into evening or other peak periods, but only when vehicles are plugged in, have sufficient state of charge, support bidirectional operation, are permitted to export by network and market rules, and their owners participate.

## Outputs

The builder is:

- `build_ev_feed_in_vs_small_solar.py`

It writes:

- `data/visuals/ev_feed_in_vs_small_solar_capacity.png`
- `data/visuals/ev_feed_in_vs_small_solar_plot_data.csv`
- `data/visuals/ev_feed_in_vs_small_solar_manifest.json`

The audit CSV contains annual values for the solar curve, both EV fleet pathways, all four feed-in scenarios and crossover flags. The manifest records the source files, dated EV anchor, fleet assumptions, export assumptions, curve method and crossover years.

## Solar comparison

The central solar line is the repo's existing **small distributed solar below 25 kW** projection:

- `data/distributed_generation/model/distributed_solar_adoption_20pct.csv`
- built by `build_distributed_solar_20pct.py`
- 20% small-solar ICP saturation ceiling

The visual uses total projected installed small-solar nameplate capacity at each calendar year end. It does not use utility-scale solar, the Electricity Authority generation-investment pipeline solar category, or the broader sub-1 MW distributed total.

The 10% and 30% small-solar scenarios remain available elsewhere in the repo, but are deliberately omitted from this chart to keep the four EV scenarios readable. They can be added later as a subtle uncertainty band if useful.

## Current EV fleet anchor

The maintained input is:

- `data/transport/ev_fleet_anchor.csv`

The initial anchor is **101,701 zero-emission light vehicles as at 16 August 2026**, sourced from the Ministry of Transport's weekly electric-vehicle report based on Motor Vehicle Register data.

This deliberately replaces the older approximate 117,000-EV discussion anchor with a newer, dated government figure and uses a narrower definition that is more consistent with the Ministry's zero-emission-vehicle target. Broad plug-in fleet totals can be higher because they may include PHEVs. That distinction matters for V2G because the chart should not silently mix incompatible fleet definitions.

EVDB (`evdb.nz/ev-stats`) is a useful independent cross-check for market trends and plug-in fleet composition. It is not scraped by this repository. EVDB describes its statistics as curated and its terms restrict automated scraping, while the underlying registration data are available from government sources. For a maintained public model, the preferred direction is therefore to update the anchor from Ministry of Transport / NZTA data directly and use EVDB as a sanity check.

The render workflow is intentionally separate from source-data acquisition. The current anchor CSV can be updated independently when a reliable primary-source machine-readable updater is added.

## EV uptake pathways

### EECA / Climate Change Commission style pathway

The pathway is anchored to:

- current government fleet anchor: 101,701 zero-emission light vehicles at 16 August 2026
- 550,000 light passenger and light commercial EVs by the end of 2030
- 38% of the light vehicle fleet by the end of 2035

For the 2035 percentage conversion, the model currently assumes a **4.5 million light-vehicle fleet**, giving:

- 38% × 4.5 million = **1.710 million EVs in 2035**

The builder fits a three-parameter logistic curve exactly through the current, 2030 and 2035 anchors. This produces a smooth S-shaped path rather than straight-line growth.

### Ministry of Transport pathway

The Ministry / ERP-style pathway is anchored to:

- the same current government fleet anchor
- **30% zero-emission vehicles in the light fleet by 2035**
- the same 4.5 million assumed 2035 light fleet

This gives:

- 30% × 4.5 million = **1.350 million zero-emission light vehicles in 2035**

The MoT path is not linearly interpolated from the current fleet. Instead, it uses the EECA / CCC logistic curve's normalized progress from the current anchor to 2035 and scales that same adoption timing between the current stock and the lower MoT endpoint. This keeps early uptake comparatively slower and later uptake stronger, avoiding the implausibly early crossover created by a straight-line interpolation.

These pathways are **scenario anchors, not precise forecasts**.

## EV export assumptions

### Current-like export

- export power per participating EV: **5 kW**
- dedicated charger adoption: **47%**
- willing / able peak-period participation among charger owners: **50%**

Effective aggregate export capacity per EV:

`5 kW × 0.47 × 0.50 = 1.175 kW per EV`

### Future-ready export

- export power per participating EV: **7 kW**
- dedicated charger adoption: **70%**
- willing / able peak-period participation among charger owners: **50%**

Effective aggregate export capacity per EV:

`7 kW × 0.70 × 0.50 = 2.45 kW per EV`

The future-ready case represents a later system in which residential feed-in standards, bidirectional charger availability and dedicated home-charger uptake are substantially more supportive of V2G. It is not an assumption that every EV can export 7 kW.

## Indicative results from the current inputs

| Scenario | 2030 feed-in MW | 2035 feed-in MW | Crosses central solar by 2030? | Crosses by 2035? | First annual crossover |
| --- | ---: | ---: | --- | --- | ---: |
| EECA / CCC, current-like export | 646 | 2,009 | No | No | None through 2035 |
| EECA / CCC, future-ready export | 1,348 | 4,190 | No | Yes | 2031 |
| MoT, current-like export | ~528 | 1,586 | No | No | None through 2035 |
| MoT, future-ready export | ~1,102 | 3,308 | No | Yes | 2033 |

These values are regenerated from the source inputs by the builder. The manifest and plot-data CSV are authoritative for the exact current run.

## Why equal EV MW and solar MW are not equivalent

The visual intentionally puts both resources on a common power-capacity axis, but their operational roles differ substantially.

Small solar nameplate MW is weather- and daylight-dependent. Its contribution to an evening winter peak can be much lower than its installed nameplate capacity. EV feed-in MW could, in principle, be dispatched during a peak, but the aggregate available power is constrained by how many vehicles are connected, how much energy owners are willing to make available, battery state of charge, bidirectional hardware, inverter and charger ratings, distribution-network limits and control / market arrangements.

The EV curves therefore represent **potential aggregate export power under participation assumptions**, not firm capacity. A more complete system-value comparison would also model available MWh, plug-in probability by half-hour, battery state of charge, trip requirements, seasonal peak coincidence, distribution constraints and response duration.

## Important V2G caveats

- A dedicated EV charger is not necessarily bidirectional.
- Vehicle support for V2G / V2H varies by model and charging standard.
- New Zealand network export standards and connection permissions are not uniform.
- Customer participation may vary strongly with tariffs, battery-warranty treatment, convenience and compensation.
- The 50% participation factor is a scenario assumption, not observed behaviour.
- Five or seven kW of export power should not be confused with the energy available from the traction battery.
- Fleet targets and uptake pathways can change with vehicle prices, policy, charging access, fuel prices, used imports and supply.
- The assumed 4.5 million light-vehicle fleet in 2035 should be replaced if the repo adopts a maintained official fleet projection.

## Refresh path

The visual render is maintained separately from transport-source acquisition. A future source updater should ideally fetch a documented Ministry of Transport or NZTA machine-readable fleet series, validate its fleet definition, update `data/transport/ev_fleet_anchor.csv`, and then allow the normal render workflow to rebuild the chart.

EVDB remains useful for checking whether the resulting BEV / plug-in trend is plausible, but is intentionally not an automated dependency.
