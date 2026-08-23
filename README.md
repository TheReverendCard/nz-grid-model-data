# NZ Grid: Renewables, Hydro and Thermal Security

A public, reproducible evidence and modelling repository for a practical New Zealand electricity question:

> As New Zealand builds more wind, solar, geothermal and batteries, do those resources preserve enough hydro and cover enough demand to reduce thermal generation and price-shock risk, including through dry periods?

This repository combines authoritative public data, selected outputs from established New Zealand electricity models, transparent derived calculations, compact public datasets, and public-facing visualisations.  It is intended to make system-security questions easier to inspect and reproduce.  It is **not** intended to replace professional production-cost, dispatch, hydro-scheduling, or transmission-expansion models.

## What is in this repository

The repository now has seven connected layers:

1. **Observed wholesale electricity history** from the Electricity Authority, including generation from August 1997 onward, 2024+ grid-export demand inputs, and 2024 reconciled injection/offtake used for baseline calibration.
2. **Hydrological data** from the Electricity Authority Hydrological Modelling Dataset (HMD), including model-relevant infrastructure, flows, reservoir storage, spill, and derived tributary-flow series.
3. **Distributed-generation data**, primarily rooftop/distributed solar and solar-with-battery observations used for both public evidence and future adoption scenarios.
4. **Observed/public evidence tables** under `data/public/`, including generation mix, renewable share, demand, hydro reservoir volumes, distributed solar, wholesale reference prices, and renewable-share/price joins.
5. **Future demand and security reference data** from MBIE EDGS, Transpower SOSA, and the Transpower generation/storage connection pipeline.
6. **Professional-model evidence**, particularly published JADE inputs/outputs for stochastic hydro scheduling and security analysis.
7. **Derived scenario tables and visualisations** that combine those sources into interpretable comparisons.

The working model baseline is intentionally narrower than a full national dispatch simulator.  The project uses observed data and professional-model evidence where available, then adds simple, inspectable calculations around questions those sources do not directly answer.

## Modelling principles

### Prefer established models over rebuilding them

Where a professional New Zealand model already solves a problem, this project should use or interrogate that model rather than quietly replacing it with a simplified home-grown equivalent.

- **JADE**: stochastic hydro scheduling, reservoir water values and inflow uncertainty.  This is the preferred source for hydro-management behaviour.
- **GEM**: long-term generation and transmission expansion scenarios.
- **vSPD**: detailed scheduling, pricing and dispatch where market-level simulation is required.
- **HSS**: deterministic hydro supply-security testing and a useful dry-year/security cross-check.
- **HMD**: official hydrological infrastructure, constraints, flows, storage and spill data.
- **EA / EMI**: observed wholesale, price, reconciliation and distributed-generation data.
- **MBIE EDGS**: future electricity-demand pathways and assumptions.
- **Transpower SOSA**: published winter-energy and capacity-margin reference cases.
- **Transpower connection pipeline**: current generation and storage projects progressing through the connection/delivery process.

### Keep assumptions visible

Derived outputs should identify what changed and what did not.  A comparison that adjusts a published SOSA margin for a distributed-solar scenario, for example, is a **delta adjustment to SOSA**, not a claim that SOSA itself has been rerun.

### Separate observed evidence from model scenarios

The repository keeps compact observed/public products under `data/public/`.  Scenario and modelling outputs live under source-specific `model/` folders or `data/model/`.

This distinction matters because an observed renewable-share chart and a 2035 capacity-margin scenario answer different questions and should not be presented as the same kind of evidence.

### Separate energy security from capacity security

Annual or seasonal energy sufficiency and instantaneous/peak capacity adequacy are related but different questions.  The repository therefore keeps winter-energy margins, North Island winter capacity margins, annual thermal generation, peak thermal capacity, hydro storage and shortage metrics separate wherever possible.

### Do not conflate unlike hydro-storage measures

This is an important project-specific rule:

- HMD reservoir storage series describe hydrological/model reservoir volumes.
- Some repository scripts derive energy-equivalent measures from those data and infrastructure assumptions.
- System Operator / Transpower controlled-storage figures expressed in GWh are defined and aggregated differently.

These quantities must **not** be treated as interchangeable simply because both are described informally as "hydro storage".  Any comparison must state the definition and conversion being used.

## Repository layout

```text
.github/workflows/                 GitHub Actions acquisition, rebuild and render workflows

DATA_DICTIONARY.md                 Field-level guide to the compact observed/public datasets
README.md                          Architecture, purpose, provenance and operating guide

data/distributed_generation/       EA distributed-solar/battery sources + normalized/model outputs
data/hydro/                         HMD indexes, curated raw hydro series and normalized tables
data/jade/                          Derived JADE analysis products
data/mbie/                          MBIE EDGS source workbooks and normalized scenario tables
data/metadata/                      Source manifests, ETags, selected versions and provenance
data/model/                         Cross-source model outputs, diagnostics and comparison tables
data/pipeline/                      Transpower generation/storage connection-pipeline source + CSV
data/prices/                        Cached finalized monthly wholesale-price reference tables
data/public/                        Compact observed/public CSV evidence layer
data/sosa/                          Transpower SOSA workbook and normalized reference tables
data/visuals/                       Generated public-facing figures
data/wholesale/                     EA generation/grid-export raw data, reconciliation caches and models
data/source_registry.csv            Machine-readable source purpose and local-copy policy

update_*.py                         Upstream acquisition/source-check scripts
normalize_*.py                      Source-specific normalization scripts
inspect_*.py                        Schema/source inspection helpers
build_*.py                          Derived analyses, scenarios, public views and charts
diagnose_*.py / validate_*.py       Data-quality and calibration checks
prepare_*.py / summarize_*.py       JADE diagnostic workflow helpers
```

The many `build_*.py` files are deliberately small and task-specific rather than one opaque modelling application.  This makes it easier to trace a public chart or result back to the transformation that produced it.

## Source registry and local-copy policy

The canonical machine-readable source list is `data/source_registry.csv`.

| Source | Owner | Main use | Local policy |
|---|---|---|---|
| HMD | Electricity Authority | Hydro infrastructure, flows, reservoir volumes and spill | Retain curated model-relevant subset plus full-catalog metadata |
| Wholesale generation | Electricity Authority | Historical observed generation and calibrated baseline | Retain monthly files from August 1997 onward |
| Grid export | Electricity Authority | 2024+ grid-demand input | Retain monthly files from 2024 onward |
| Final energy prices | Electricity Authority | Public reference-node wholesale-price history | Cache finalized monthly summaries; use daily source files for open months |
| Reconciliation GR-010 | Electricity Authority | Settlement-quality 2024 calibration | Stream source gzip files; retain monthly aggregates and revision metadata |
| Distributed generation | Electricity Authority | Solar/battery history and scenario calibration | Retain compact source reports and Registry extracts used directly by outputs |
| JADE published expected-water-value data | Electricity Authority / EPOC | Hydro scheduling/security evidence | Keep manifests and derived products; leave most raw model files upstream |
| SOSA 2026 supplementary data | Transpower | Winter-energy and NI capacity reference cases | Retain source workbook and normalized tables |
| Generation/storage connection pipeline | Transpower | Current connection/delivery pipeline | Retain workbook, normalized CSV and provenance; check weekly |
| EDGS 2024 | MBIE | Future demand pathways | Retain source workbooks and normalized tables |
| GEM / vSPD / HSS | Electricity Authority | Expansion, dispatch and security cross-checks | Link upstream unless a local derived subset adds reproducibility value |

Primary public sources:

- Electricity Authority data and insights: https://www.ea.govt.nz/data-and-insights/
- JADE expected-water-value dataset: https://www.ea.govt.nz/data-and-insights/datasets/wholesale/expected-water-values/
- JADE source: https://github.com/EPOC-NZ/JADE.jl
- Electricity Authority modelling tools: https://www.emi.ea.govt.nz/Wholesale/Tools
- Electricity Authority generation investment pipeline: https://www.ea.govt.nz/data-and-insights/charts-and-dashboards/generation-investment-pipeline/
- MBIE EDGS: https://www.mbie.govt.nz/building-and-energy/energy-and-natural-resources/energy-statistics-and-modelling/energy-modelling/electricity-demand-and-generation-scenarios

## Source acquisition and update scripts

### HMD: `update_data.py` and `update_hmd_sources.py`

`update_data.py` is retained as the stable historical entry point used by automation.  It routes to the optimized HMD checker in `update_hmd_sources.py`.

Routine HMD checks:

1. perform a shallow listing of top-level HMD components;
2. detect whether the published component topology/version changed;
3. list only the current model-relevant infrastructure, flows, and storage/spill component trees;
4. run those component listings in parallel;
5. compare source ETags against `data/metadata/hmd_*` provenance records; and
6. download only changed model-relevant files.

A full recursive HMD catalog listing is performed when:

- the top-level HMD component topology changes;
- no usable manifest exists;
- `--full-discovery` is requested;
- the Sunday daily-refresh schedule runs; or
- the daily workflow is manually dispatched with `force_rebuild=true`.

The full catalog is represented by `data/metadata/hmd_manifest.json`; selected indexes, model series and infrastructure have separate provenance records.

### Wholesale generation and grid export: `update_wholesale.py`

The wholesale updater maintains two different retention windows because the public evidence layer and calibrated model have different needs:

- **Generation:** August 1997 onward, supporting long historical public evidence.
- **Grid export:** January 2024 onward, supporting the current calibrated demand baseline.

Routine runs list only the current/latest-known year prefix rather than the complete historical directories.  ETags prevent unchanged monthly files from being downloaded again.

A full source-history scan occurs on Sunday scheduled runs, with `--full-scan`, or when the daily workflow is manually dispatched with `force_rebuild=true`.

`normalize_wholesale.py` aggregates the checked-in monthly source files into daily generation and demand tables.

### Reconciliation: `update_reconciliation.py`

The reconciliation updater currently targets Electricity Authority GR-010 reconciled injection/offtake for calendar year **2024**.

For each month it selects the latest published revision by timestamp and ETag.  The source gzip is streamed and aggregated rather than retained permanently.  Local monthly caches are written under:

- `data/wholesale/model/reconciliation_monthly/`
- `data/wholesale/model/reconciliation_injection_poc_monthly/`

Annual daily and point-of-connection summaries are produced from those caches.

If all twelve selected monthly blob names/ETags match the prior run and expected outputs exist, the updater exits before rereading and rebuilding the annual aggregates.

### Distributed generation: `update_distributed_generation.py`

The updater retrieves:

- national all-solar installed-DG trends;
- Solar-only trends;
- explicit Solar-with-battery trends;
- residential all-solar trends;
- current solar installations by region; and
- street-level Registry solar installations.

The EA GUEHMT report service inserts a new `Run at:` timestamp on every request.  That header is provenance noise rather than a model-input change.  The updater canonicalizes the volatile line when comparing responses, preventing identical observations from waking downstream models.

The Registry region/street extracts use conditional HTTP requests where ETag/Last-Modified metadata are available.

The updater exposes three substantive change signals:

- `distributed_generation_changed`
- `solar_model_inputs_changed`
- `battery_model_inputs_changed`

This allows solar and battery scenario chains to rebuild independently where practical.

### Final energy prices: `update_prices.py`

This updater maintains a compact wholesale-price evidence series at three reference nodes:

- Otahuhu (`OTA2201`)
- Haywards (`HAY2201`)
- Benmore (`BEN2201`)

Finalized months from 2024 onward are cached under `data/prices/monthly/`.  For the current unfinalized month, daily source-file ETags are compared with `data/metadata/price_sources.json`; unchanged days reuse the row already present in `data/public/wholesale_prices_daily.csv` rather than being downloaded again.

Routine runs scan the current/latest relevant year.  Sunday or forced runs perform a broader monthly verification.

Price changes are **public-evidence changes only**.  They do not trigger hydro, solar, SOSA or future-demand scenario rebuilds.

### Transpower connection pipeline: `update_connection_pipeline.py`

This updater conditionally checks Transpower's Generation and Energy Storage Connection Pipeline workbook using ETag/Last-Modified headers where available.

If unchanged, workbook parsing is skipped.  When changed, the source workbook is retained and normalized into:

`data/pipeline/transpower_generation_storage_pipeline.csv`

The pipeline changes relatively slowly and is therefore checked in the **weekly reference workflow**, not the daily operational-data workflow.

This Transpower connection/delivery pipeline is not identical to the Electricity Authority's broader investment-pipeline dashboard.  Their project status concepts should not be treated as interchangeable.

### JADE: `update_jade_manifest.py`

The JADE updater indexes the Electricity Authority's published weekly expected-water-value inputs and outputs.  It records year/week structure, identifies the latest publication, and classifies potentially useful output files.

The repository intentionally does **not** mirror the full JADE publication tree.  `data/metadata/jade_manifest.json` and `jade_latest.json` identify authoritative upstream files; analysis scripts retrieve only the referenced data required for derived results.

### Transpower SOSA: `update_sosa_2026.py`

The SOSA updater conditionally retrieves the 2026 Final Supplementary Data workbook and normalizes current reference tables, including:

- medium-demand winter energy;
- medium-demand winter peak;
- reference New Zealand winter energy margin; and
- reference North Island winter capacity margin.

Conditional requests avoid reparsing the workbook when the upstream source is unchanged.

### MBIE EDGS: `update_mbie_edgs.py`

The EDGS updater checks the 2024 assumptions and results workbooks using ETag/Last-Modified conditional requests.  Workbook normalization is separated from acquisition so metadata-only source checks do not force spreadsheet processing.

`inspect_mbie_edgs.py` records workbook structure for diagnostics, while `normalize_mbie_edgs.py` creates scenario tables used by the future-demand chain.

## Observed/public evidence layer

`data/public/` is a compact derivative layer intended for dashboards, exploratory analysis and external reuse.  Its fields and cautions are documented in `DATA_DICTIONARY.md`.

Current products include:

- `generation_mix_daily.csv`
- `renewable_share_daily.csv`
- `demand_daily.csv`
- `hydro_storage_daily.csv`
- `solar_installations_monthly.csv`
- `wholesale_prices_daily.csv`
- `renewables_vs_price_daily.csv`
- `manifest.json`

`build_public_views.py` rebuilds these from normalized checked-in sources.  Public price rows are sourced directly by `update_prices.py`, then joined to renewable-share observations by `build_public_views.py`.

The public layer is deliberately **observed/derived evidence only**.  Future scenarios are kept elsewhere so a dashboard consumer cannot accidentally confuse an observed series with a model projection.

## Normalization and derived model layers

The central model flow is approximately:

```text
EA HMD --------------------> normalize_hydro.py -----------------> hydro/storage analyses
       \
        -> infrastructure + curated series + provenance

EA wholesale --------------> normalize_wholesale.py ------------> observed generation/demand
EA reconciliation ---------> monthly/annual reconciled tables ---+--> calibrated 2024 baseline
EA distributed generation -> normalize_distributed_generation.py -/

EA final prices -----------------------------------------------> data/public price evidence
observed normalized layers ------------------------------------> build_public_views.py

MBIE EDGS -----------------> normalize_mbie_edgs.py -------------> future demand scenarios
Transpower SOSA -----------------------------------------------> security-margin comparisons
EA JADE published outputs -------------------------------------> hydro/thermal security analyses

all relevant model layers -------------------------------------> data/model + data/visuals
```

### Observed 2024 baseline

The calibrated 2024 baseline combines observed wholesale data, reconciled settlement data, and an estimate of behind-the-meter solar.

Relevant scripts include:

- `diagnose_generation_poc.py`
- `validate_baseline.py`
- `diagnose_solar_yield.py`
- `estimate_btm_solar_2024.py`
- `build_baseline_2024.py`

The point is to establish a transparent observed/calibrated starting point before applying future-demand or distributed-generation scenarios.

### Distributed solar and batteries

The distributed-generation scenario chain includes:

- `build_solar_size_buckets.py`
- `build_distributed_solar_scenarios.py`
- `cap_larger_distributed_solar_growth.py`
- `build_distributed_battery_scenarios.py`

Outputs live mainly under `data/distributed_generation/model/`.  These are scenario trajectories and modelling inputs, not Electricity Authority forecasts.

The explicit EA Solar-with-battery category is preferred for battery connection observations.  Registered generation capacity is useful as a connection-power ceiling/proxy, but it is not battery-only power and not storage-energy capacity.

### Future demand and replay inputs

`build_future_demand_scenarios.py` aligns the observed/calibrated baseline with MBIE EDGS demand pathways.

`build_replay_inputs.py` assembles the inputs used for future-demand/historical-condition replay experiments.

### Hydro and renewable interactions

Current hydro-related derived work includes:

- hydro storage energy-equivalent calculations;
- hydro/solar seasonal comparisons;
- water-value diagnostics;
- observed hydro release and price-stress views;
- hydro-trough counterfactual visualisation; and
- exploratory Waitaki routing/storage calculations.

These scripts should not be interpreted as replacements for JADE's stochastic hydro policy.  The Waitaki-specific scripts remain useful for source auditing, network understanding, semantics checks and targeted experiments, but they are not the authoritative national hydro dispatcher.

### SOSA distributed-generation comparisons

The SOSA comparison chain combines Transpower's published reference margins with project distributed-solar/battery scenarios.

Current outputs include:

- distributed-solar winter-energy comparisons;
- distributed-battery North Island capacity comparisons;
- Stage 2 winter-energy and capacity-margin charts; and
- a 2035 North Island capacity stress test.

These are transparent **delta comparisons to SOSA's published reference cases**, not complete Transpower model reruns.

### JADE analyses

JADE-derived scripts inspect published output schemas and derive chart-ready summaries of storage, thermal dispatch, security and thermal-valley behaviour.

The separate renewable impulse-response diagnostic runs paired JADE simulations with a small renewable-energy pulse.  Its temporary JADE workspace is deliberately excluded from published repository data; only compact derived diagnostics are uploaded as workflow artifacts.

## GitHub Actions architecture

The CI architecture follows one rule:

> **A source check is not the same thing as a model rebuild.**

Unchanged or provenance-only source checks should be cheap and should not wake downstream analysis.

### Daily fast-moving source refresh

Workflow: `.github/workflows/update_data.yml`

Schedule: `0 14 * * *` UTC, plus manual dispatch.

Five independent source jobs run in parallel:

1. HMD
2. wholesale generation/grid export
3. reconciliation
4. distributed generation
5. final energy prices

Each job stages only the paths it owns and produces a binary Git patch artifact.  The model-relevant jobs separately report whether a **substantive model input** changed.

The downstream aggregation job:

1. checks out a clean repository;
2. applies the source patches;
3. normalizes only changed model sources;
4. runs only downstream dependency branches that require rebuilding;
5. rebuilds the compact public layer if its observed inputs changed; and
6. commits the combined result once.

This avoids separate source jobs racing to push competing commits.

Wholesale-price changes are public-view only and do not wake scenario models.

On Sundays, HMD and wholesale automatically perform their comprehensive catalog/history checks.  The price updater also performs broader monthly verification.  Other days use the optimized fast paths.

A validated ordinary no-change run on the development branch completed in **less than one minute** after the workflow refactor.  The additional price checker is independent and optimized to avoid refetching unchanged open-month daily files.

### Weekly slow-changing reference refresh

Workflow: `.github/workflows/update_reference_data.yml`

Schedule: `0 15 * * 0` UTC, plus manual dispatch.

Four independent checks run in parallel:

1. JADE published inputs/outputs
2. Transpower SOSA
3. MBIE EDGS
4. Transpower generation/storage connection pipeline

JADE, SOSA and EDGS propagate substantive changes into their dependent analyses.  The Transpower connection pipeline is reference/public data only and does not trigger scenario-model rebuilds.

### Manual full rebuild

Workflow: `.github/workflows/full_rebuild.yml`

This workflow skips the normal source-discovery/update jobs and rebuilds the current derived output scope from the checked-in source state, including:

- normalized core sources;
- the observed/public CSV layer;
- baseline and future-demand outputs;
- distributed solar/battery scenarios;
- JADE-derived analyses;
- SOSA comparisons; and
- current public visualisations.

Use it when model/build code changes without source data changing, or when a complete consistency rebuild is desired.

JADE analysis steps may still read authoritative upstream files referenced by checked-in JADE metadata.  Those raw JADE files are not committed.

### Lightweight render workflows

Two frequently adjusted public visuals have dedicated workflows that do **not** perform source acquisition:

- `.github/workflows/render_hydro_trough.yml`
- `.github/workflows/render_sosa_comparisons.yml`

They run on relevant changes to `main` and can also be manually dispatched.

### JADE renewable impulse workflow

`.github/workflows/jade_impulse_response.yml` is separate because it installs Julia, JADE and an open-source solver and can run much longer than ordinary refreshes.

It is primarily a diagnostic workflow.  Raw JADE workspaces remain temporary; only compact derived artifacts are published.

## Change detection and provenance

A recurring problem in automated data repositories is treating harmless metadata churn as a substantive data change.  This repository explicitly separates those concepts.

Examples:

- A changed `sources.json` header does not automatically mean a model input changed.
- GUEHMT's volatile `Run at:` line is ignored when deciding whether distributed-generation observations changed.
- HMD and wholesale Azure ETags allow unchanged files to be skipped without downloading contents.
- Reconciliation checks monthly revision ETags before deciding whether to stream and aggregate a source month.
- SOSA, EDGS and the Transpower connection-pipeline workbook use conditional HTTP requests where supported.
- Final energy-price daily files reuse prior output rows when their ETag is unchanged.

Provenance metadata is still retained because it answers a different question: **which authoritative source version produced this repository state?**

GitHub Actions summaries report source changed/unchanged decisions, substantive model-input changes where relevant, force-rebuild state, and committed files.

## Reproducing locally

Python 3.12 is the workflow reference environment.

A broad dependency set sufficient for most Python build scripts is:

```bash
python -m pip install requests openpyxl matplotlib scipy pandas beautifulsoup4 numpy
```

Examples:

```bash
# Fast HMD source check
python update_data.py

# Explicit full HMD discovery
python update_hmd_sources.py --full-discovery

# Fast wholesale check
python update_wholesale.py

# Explicit full wholesale-history verification
python update_wholesale.py --full-scan

# Reconciliation
python update_reconciliation.py

# Distributed generation
python update_distributed_generation.py

# Final energy prices
python update_prices.py

# Full monthly-price verification
python update_prices.py --full-scan

# Transpower connection pipeline
python update_connection_pipeline.py

# Normalize core source layers
python normalize_hydro.py
python normalize_wholesale.py
python normalize_distributed_generation.py
python normalize_mbie_edgs.py

# Rebuild compact observed/public evidence
python build_public_views.py

# Rebuild selected comparison figures
python build_hydro_trough_counterfactual.py
python build_sosa_distributed_comparison.py
```

Some JADE diagnostics additionally require Julia and packages installed by the dedicated GitHub Actions workflow.

The automated workflows are the preferred reproducible execution path because they encode intended cadence, dependency gates and source-check behavior.

## Data/output conventions

### `data/metadata/`

Source provenance rather than model results.  Typical records include:

- Azure blob names;
- ETags;
- Last-Modified values;
- selected source versions;
- JADE manifests;
- workbook source hashes; and
- schema/diagnostic metadata.

### `data/public/`

Compact observed/derived evidence for dashboards and reuse.  See `DATA_DICTIONARY.md`.

### Source-local `model/` folders

Folders such as `data/distributed_generation/model/`, `data/wholesale/model/` and `data/mbie/edgs2024/model/` contain normalized or source-domain-specific derived data.

### `data/model/`

Cross-source scenario outputs, diagnostics and comparison tables that do not belong to a single source domain.

### `data/visuals/`

Generated public-facing figures.  A PNG should normally be reproducible from a named `build_*.py` script and checked-in source/model inputs.

### `data/pipeline/`

Transpower's retained connection-pipeline workbook plus its normalized CSV.  This is reference data, not a model scenario.

### `data/prices/`

Cached finalized monthly price summaries used to avoid repeatedly parsing historical source files.

### Missing values

Missing data should remain explicit.  Scripts should not silently invent values merely to fill a common schema.

## Public-facing visual story

The repository is intended to make four questions visible:

### 1. Seasonal hydro storage

How does stored hydro move through the year under observed conditions and relevant future scenarios?  Does additional renewable generation allow the system to enter or move through winter with more water available?

### 2. What fills the seasonal gap?

How much demand is covered by wind, solar, geothermal, hydro, batteries and thermal generation, and when do those resources substitute for each other?

### 3. Hydro preserved by renewables

When additional wind or solar reduces hydro generation, how much water/energy is effectively preserved for later periods, and under what assumptions does that preservation persist?

### 4. Residual thermal requirement

How much thermal **energy** is still required, how much thermal **capacity** remains valuable as insurance, and how often is it actually called on?

Useful measures include:

- annual thermal generation (GWh);
- peak thermal capacity required (MW);
- duration/frequency of significant thermal use;
- hydro storage or storage-equivalent measures with definitions stated;
- winter energy/capacity margins;
- shortage/unserved-energy metrics where available;
- water-value / price-stress indicators; and
- observed reference-node price behaviour where appropriate.

## Common result schema

Where practical, cross-source scenario outputs should converge on fields such as:

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

Not every model/source can populate every field.  Missing values should remain explicit rather than being inferred silently.

## Experimental and historical work retained here

The repository contains exploratory scripts developed while learning the source data and testing alternative approaches, particularly around Waitaki routing, storage constraints, thermal timing, and solar-conservation experiments.

They are retained because they provide:

- an audit trail of how HMD fields were interpreted;
- useful diagnostics for source semantics;
- targeted experiments that may remain valuable; and
- reproducibility for figures or conclusions produced during development.

They should **not** be taken as the current authoritative national hydro model.  Where conclusions depend on hydro operating policy, professional JADE evidence should be preferred unless a custom calculation is clearly labelled as such.

The historical generation archive is also retained intentionally because it supports the observed/public evidence layer even though the calibrated scenario baseline currently focuses on 2024.

## Adding or changing a source

When adding a new upstream dataset:

1. add/update its entry in `data/source_registry.csv`;
2. document public output fields in `DATA_DICTIONARY.md` if it contributes to `data/public/`;
3. store provenance separately from substantive normalized data;
4. use ETags, Last-Modified, source manifests, checksums or equivalent metadata where available;
5. avoid downloading unchanged large files;
6. expose a substantive change signal if metadata can change independently of model inputs;
7. assign an appropriate polling cadence rather than defaulting everything to daily;
8. document which downstream builders depend on it; and
9. retain only the raw source material required for reproducibility or an intentional historical/public product.

Workflow/CI refactors should not quietly change model assumptions.

## Reproducibility standard

Every published chart/table should be traceable to:

- authoritative upstream source;
- source version/date or ETag where available;
- scenario assumptions;
- transformation/build script;
- professional model used, where applicable; and
- whether the result is observed, modelled, adjusted, or otherwise derived.

The goal is to make the visual story simple without making the methodology opaque.

## Known scope limitations

- Reconciliation calibration is currently focused on 2024 rather than a continuously expanding settlement-history model.
- Grid-export demand retention currently begins in 2024 even though generation history is retained from 1997.
- SOSA comparisons currently use the 2026 supplementary dataset and are delta comparisons, not Transpower reruns.
- EDGS normalization is based on EDGS 2024 workbooks.
- The Transpower connection pipeline and EA generation investment pipeline represent different project concepts and are not merged into a single status model here.
- The reference-node wholesale-price series is not a demand-weighted national settlement price.
- The project does not provide a complete nodal/transmission-constrained national dispatch simulation of its own.
- Some exploratory scripts remain because they are useful diagnostics even though they are no longer the preferred modelling path.
- Upstream public datasets can change structure or publication practices; source-check scripts deliberately fail loudly when expected schema/source selections disappear.

## Licence

Repository code is licensed under AGPL-3.0 unless otherwise noted.  Upstream datasets and modelling tools retain their own licences, copyright and terms of use.
