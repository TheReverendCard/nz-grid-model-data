# NZ Grid: Renewables, Hydro and Thermal Security

A public, reproducible evidence and modelling repository for a practical New Zealand electricity question:

> As New Zealand builds more wind, solar, geothermal and batteries, do those resources preserve enough hydro and cover enough demand to reduce thermal generation and price-shock risk, including through dry periods?

This repository combines authoritative public data, selected outputs from established New Zealand electricity models, transparent derived calculations, and public-facing visualisations.  It is intended to make system-security questions easier to inspect and reproduce.  It is **not** intended to replace professional production-cost, dispatch, hydro-scheduling, or transmission-expansion models.

## Current scope

The repository currently contains six connected evidence/model layers:

1. **Observed wholesale electricity data** from the Electricity Authority, including monthly generation, grid-export demand data, and reconciled injection/offtake used to calibrate the 2024 baseline.
2. **Hydrological data** from the Electricity Authority Hydrological Modelling Dataset (HMD), including model-relevant infrastructure, flows, reservoir storage, spill, and derived tributary-flow series.
3. **Distributed generation data**, primarily rooftop/distributed solar and solar-with-battery observations, used to estimate behind-the-meter generation and build adoption scenarios.
4. **Future demand and security reference data** from MBIE EDGS and Transpower's Security of Supply Annual Assessment (SOSA).
5. **Professional-model evidence**, particularly published JADE inputs/outputs for stochastic hydro scheduling and security analysis.
6. **Derived scenario tables and visualisations** that combine those sources into interpretable comparisons.

The working model baseline is intentionally narrower than a full national dispatch simulator.  The project uses observed data and professional-model evidence where available, then adds simple, inspectable calculations around the questions those sources do not directly answer.

## Modelling principles

### Prefer established models over rebuilding them

Where a professional New Zealand model already solves a problem, this project should use or interrogate that model rather than quietly replacing it with a simplified home-grown equivalent.

- **JADE**: stochastic hydro scheduling, reservoir water values and inflow uncertainty.  This is the preferred source for hydro-management behaviour.
- **GEM**: long-term generation and transmission expansion scenarios.
- **vSPD**: detailed scheduling, pricing and dispatch where market-level simulation is required.
- **HSS**: deterministic hydro supply-security testing and a useful dry-year/security cross-check.
- **HMD**: official hydrological infrastructure, constraints, flows, storage and spill data.
- **EA / EMI**: observed wholesale and distributed-generation data.
- **MBIE EDGS**: future electricity-demand pathways and assumptions.
- **Transpower SOSA**: published winter-energy and capacity-margin reference cases.

### Keep model assumptions visible

Derived outputs should identify what changed and what did not.  A comparison that adjusts a published SOSA margin for a distributed-solar scenario, for example, is a **delta adjustment to SOSA**, not a claim that SOSA itself has been rerun.

### Separate energy security from capacity security

Annual or seasonal energy sufficiency and instantaneous/peak capacity adequacy are related but different questions.  The repository therefore keeps winter-energy margins, North Island winter capacity margins, annual thermal generation, peak thermal capacity, hydro storage and shortage metrics separate wherever possible.

### Do not conflate unlike hydro-storage measures

This is an important project-specific rule:

- HMD reservoir storage series describe the hydrological/model reservoir data published in HMD.
- Some repository scripts derive energy-equivalent measures from those data and infrastructure assumptions.
- System Operator / Transpower controlled-storage figures expressed in GWh are defined and aggregated differently.

These quantities must **not** be treated as interchangeable simply because both are described informally as "hydro storage".  Any comparison must state the definition and conversion being used.

## Repository layout

```text
.github/workflows/                 GitHub Actions acquisition, rebuild and render workflows

data/distributed_generation/       EA distributed-solar and battery source data + normalized/model outputs
data/hydro/                         HMD indexes, curated raw hydro series and normalized hydro tables
data/jade/                          Derived JADE analysis products; raw JADE workspaces are not normally retained
data/mbie/                          MBIE EDGS source workbooks and normalized scenario tables
data/metadata/                      Source manifests, ETags, selected blob names and provenance records
data/model/                         Cross-source model outputs and comparison tables
data/sosa/                          Transpower SOSA workbook and normalized reference tables
data/visuals/                       Public-facing PNG outputs
data/wholesale/                     EA wholesale raw monthly data, reconciliation caches and normalized tables
data/source_registry.csv            Human-readable source purpose and local-copy policy

update_*.py                         Upstream acquisition/source-check scripts
normalize_*.py                      Source-specific normalization scripts
inspect_*.py                        Schema/source inspection helpers
build_*.py                          Derived analyses, scenarios and charts
diagnose_*.py / validate_*.py       Data-quality and calibration checks
prepare_*.py / summarize_*.py       JADE diagnostic workflow helpers
```

The many `build_*.py` files are deliberately small and task-specific rather than a single opaque modelling application.  This makes it easier to trace a public chart back to the exact transformation that produced it.

## Authoritative sources and local-copy policy

The canonical machine-readable registry is `data/source_registry.csv`.  In broad terms:

| Source | Owner | Used for | Local policy |
|---|---|---|---|
| HMD | Electricity Authority | Hydro infrastructure, flows, storage and spill | Retain only the curated model-relevant subset plus full-catalog metadata |
| Wholesale generation/grid export | Electricity Authority | Observed generation and demand baseline | Retain monthly files from 2024 onward |
| Reconciliation GR-010 | Electricity Authority | Settlement-quality 2024 calibration | Stream source gzip files, retain monthly aggregates and revision metadata |
| Distributed generation | Electricity Authority | Solar/battery history and scenario calibration | Retain compact source reports and Registry extracts used directly by models |
| JADE published expected-water-value data | Electricity Authority / EPOC | Hydro scheduling/security evidence | Keep manifests and derived products; normally leave raw model files upstream |
| SOSA 2026 supplementary data | Transpower | Winter-energy and NI capacity reference cases | Retain source workbook and normalized tables |
| EDGS 2024 | MBIE | Future demand pathways | Retain source workbooks and normalized tables |
| GEM / vSPD / HSS | Electricity Authority | Future expansion, dispatch and security cross-checks | Link upstream unless a derived subset adds reproducibility value |

Primary public links:

- Electricity Authority data and tools: https://www.ea.govt.nz/data-and-insights/
- JADE datasets: https://www.ea.govt.nz/data-and-insights/datasets/wholesale/expected-water-values/
- JADE source: https://github.com/EPOC-NZ/JADE.jl
- Generation investment pipeline: https://www.ea.govt.nz/data-and-insights/charts-and-dashboards/generation-investment-pipeline/
- MBIE EDGS: https://www.mbie.govt.nz/building-and-energy/energy-and-natural-resources/energy-statistics-and-modelling/energy-modelling/electricity-demand-and-generation-scenarios
- Electricity Authority modelling tools: https://www.emi.ea.govt.nz/Wholesale/Tools

## Source acquisition scripts

### HMD: `update_data.py` and `update_hmd_sources.py`

`update_data.py` is retained as the stable historical entry point used by automation.  It now routes to `update_hmd_sources.py`.

Routine HMD checks use a fast path:

1. perform a shallow listing of top-level HMD components;
2. detect whether the published component topology/version has changed;
3. list only the current model-relevant infrastructure, flows, and storage/spill component trees;
4. run those component listings in parallel;
5. compare source ETags against `data/metadata/hmd_*` provenance records;
6. download only changed model-relevant files.

A full recursive HMD catalog listing is still performed when:

- the top-level HMD component topology changes;
- no usable manifest exists;
- `--full-discovery` is requested;
- a scheduled Sunday daily-refresh run occurs; or
- the daily workflow is manually dispatched with `force_rebuild=true`.

This preserves rapid detection of new HMD releases without recursively enumerating the entire dataset every day.

The full upstream catalog is represented in `data/metadata/hmd_manifest.json`.  Curated source selection and ETags are stored separately for indexes, model series, and infrastructure.

### Wholesale: `update_wholesale.py`

The wholesale updater retrieves Electricity Authority Azure monthly files for:

- generation; and
- grid export / metered demand inputs.

The retained modelling window begins at `202401`.

Routine runs use a rolling fast scan of the current year and latest known year rather than listing the entire historical Azure directory.  Known older months remain in metadata and on disk.  A full historical scan is performed on Sunday scheduled runs, with `--full-scan`, or when a manual daily workflow uses `force_rebuild=true`.

ETags prevent unchanged monthly files from being downloaded again.  If source metadata changes but downloaded bytes are identical, the existing file is retained.

### Reconciliation: `update_reconciliation.py`

The reconciliation updater currently targets Electricity Authority GR-010 reconciled injection/offtake for calendar year **2024**.

For each month it selects the latest published revision by revision timestamp and ETag.  Source gzip files are streamed and aggregated rather than stored permanently.  Local monthly caches are written under:

- `data/wholesale/model/reconciliation_monthly/`
- `data/wholesale/model/reconciliation_injection_poc_monthly/`

Annual daily and point-of-connection summaries are then produced.

On a no-change run, if all twelve selected monthly blob names/ETags match and the expected cached outputs exist, the updater exits without rereading and re-aggregating the monthly files.

### Distributed generation: `update_distributed_generation.py`

This updater retrieves:

- national installed distributed-generation solar trends;
- Solar-only trends;
- Solar-with-battery trends;
- residential solar trends;
- current solar installations by region; and
- street-level Registry solar installations.

The EA GUEHMT report service inserts a new `Run at:` timestamp on every request.  That timestamp is provenance noise rather than a model-input change.  The updater therefore canonicalizes that volatile header when comparing successive reports, preventing identical observations from triggering model rebuilds.

The Registry-based region/street extracts use conditional HTTP requests where ETag/Last-Modified metadata are available.

The updater exposes three important substantive change signals to GitHub Actions:

- `distributed_generation_changed`
- `solar_model_inputs_changed`
- `battery_model_inputs_changed`

These allow solar and battery scenario chains to rebuild independently when possible.

### JADE: `update_jade_manifest.py`

The JADE updater indexes the Electricity Authority's published weekly expected-water-value inputs and outputs.  It records the available year/week structure, identifies the latest published week, and classifies potentially useful output files.

The repository intentionally does **not** mirror the complete JADE publication tree.  `data/metadata/jade_manifest.json` and `jade_latest.json` identify authoritative upstream files; analysis scripts fetch only the referenced files they need.

### Transpower SOSA: `update_sosa_2026.py`

The SOSA updater conditionally retrieves the 2026 Final Supplementary Data workbook and normalizes the reference tables used by current comparisons, including:

- medium-demand winter energy;
- medium-demand winter peak;
- reference New Zealand winter energy margin; and
- reference North Island winter capacity margin.

Conditional HTTP requests avoid reparsing the workbook when the upstream source has not changed.

### MBIE EDGS: `update_mbie_edgs.py`

The EDGS updater checks the 2024 assumptions and results workbooks using ETag/Last-Modified conditional requests.  Workbook normalization is separated from acquisition so a metadata-only source check does not force expensive spreadsheet processing.

`inspect_mbie_edgs.py` records workbook structure for diagnostics, while `normalize_mbie_edgs.py` produces the scenario tables used by the future-demand chain.

## Normalization and derived model layers

The central model flow is approximately:

```text
EA HMD --------------------> normalize_hydro.py -----------------> hydro/storage analyses
       \
        -> infrastructure + curated series + provenance

EA wholesale --------------> normalize_wholesale.py ------------> observed baseline
EA reconciliation ---------> monthly/annual reconciled tables ---/
EA distributed generation -> normalize_distributed_generation.py -> BTM + solar/battery scenarios

MBIE EDGS -----------------> normalize_mbie_edgs.py -------------> future demand scenarios
Transpower SOSA -----------------------------------------------> security-margin comparisons
EA JADE published outputs -------------------------------------> hydro/thermal security analyses

all relevant layers -------------------------------------------> data/model + data/visuals
```

### Observed 2024 baseline

The 2024 baseline combines observed wholesale data, reconciled settlement data, and an estimate of behind-the-meter solar.  Relevant scripts include:

- `diagnose_generation_poc.py`
- `validate_baseline.py`
- `diagnose_solar_yield.py`
- `estimate_btm_solar_2024.py`
- `build_baseline_2024.py`

The aim is to establish a transparent observed/calibrated starting point before applying future demand or distributed-generation scenarios.

### Distributed solar and batteries

The distributed-generation chain includes:

- `build_solar_size_buckets.py`
- `build_distributed_solar_scenarios.py`
- `cap_larger_distributed_solar_growth.py`
- `build_distributed_battery_scenarios.py`

These produce scenario trajectories under `data/distributed_generation/model/`.  They are projections/scenario inputs, not Electricity Authority forecasts.

### Future demand and replay inputs

`build_future_demand_scenarios.py` aligns the observed/calibrated baseline with MBIE EDGS demand pathways.  `build_replay_inputs.py` assembles the data needed for future-demand/historical-condition replay experiments.

### Hydro and renewable interactions

Current hydro-related derived work includes:

- hydro storage energy-equivalent calculations;
- hydro/solar seasonal comparisons;
- water-value diagnostics;
- observed hydro release and price-stress views;
- hydro-trough counterfactual visualisation; and
- exploratory Waitaki routing/storage calculations.

These scripts should not be interpreted as a replacement for JADE's stochastic hydro policy.  The Waitaki-specific scripts are retained because they are useful for source auditing, network understanding and targeted experiments, but they are not the authoritative national hydro dispatcher.

### SOSA distributed-generation comparisons

The SOSA comparison chain combines Transpower's published reference margins with the project's distributed solar/battery scenarios.  Current outputs include:

- distributed-solar winter-energy comparisons;
- distributed-battery North Island capacity comparisons;
- Stage 2 margin comparison charts; and
- a 2035 North Island capacity stress test.

These are transparent delta comparisons to SOSA's published reference cases.  They are not full Transpower model reruns.

### JADE analyses

JADE-derived scripts inspect published output schemas and derive chart-ready summaries of storage, thermal dispatch, security and thermal-valley behaviour.

The separate renewable impulse-response diagnostic runs paired JADE simulations with a small renewable-energy pulse.  Its temporary JADE workspace is deliberately excluded from published repository data; only compact derived diagnostics are uploaded as workflow artifacts.

## GitHub Actions architecture

The CI architecture is designed around one rule:

> **A source check is not the same thing as a model rebuild.**

Unchanged or provenance-only source checks should be cheap and should not wake downstream analysis.

### Daily fast-moving source refresh

Workflow: `.github/workflows/update_data.yml`

Schedule: `0 14 * * *` UTC, plus manual dispatch.

Four independent source jobs run in parallel:

1. HMD
2. wholesale
3. reconciliation
4. distributed generation

Each source job:

- checks only its upstream source;
- stages only the paths it owns;
- produces a binary Git patch artifact;
- reports whether anything versioned changed; and
- separately reports whether a **substantive model input** changed.

The downstream aggregation job checks out a clean repository, applies the source patches, and runs only dependency branches that require rebuilding.  This avoids source jobs racing to push competing commits.

On Sundays, the HMD and wholesale source scripts automatically perform their more comprehensive catalog/history scans.  Other days use the optimized fast paths.

A validated ordinary no-change run on the development branch completed in **less than one minute** after the workflow refactor.  Runner startup and upstream listing latency are now a significant fraction of total runtime.

### Weekly slow-changing reference refresh

Workflow: `.github/workflows/update_reference_data.yml`

Schedule: `0 15 * * 0` UTC, plus manual dispatch.

Independent parallel checks:

1. JADE published inputs/outputs
2. Transpower SOSA
3. MBIE EDGS

The downstream job rebuilds only the reference-dependent branches affected by substantive source changes.

These datasets change much less frequently than the daily operational/observational sources and therefore do not need daily polling.

### Manual full rebuild

Workflow: `.github/workflows/full_rebuild.yml`

This workflow deliberately skips the normal source-discovery/update jobs and rebuilds the current modelling/output scope from the checked-in source state.

It is useful when:

- model code changes without source data changing;
- derived outputs need to be regenerated;
- a dependency/gating change needs validation; or
- a complete consistency rebuild is desired.

JADE analysis steps may still read authoritative upstream JADE files referenced by checked-in JADE metadata.  Those raw JADE files are not committed.

### Lightweight render workflows

Two frequently adjusted public visuals have dedicated lightweight workflows that **do not perform source acquisition**:

- `.github/workflows/render_hydro_trough.yml`
- `.github/workflows/render_sosa_comparisons.yml`

They run on relevant changes to `main` and can also be dispatched manually.  This keeps chart iteration separate from expensive or unnecessary data acquisition.

### JADE renewable impulse workflow

`.github/workflows/jade_impulse_response.yml` is a separate diagnostic workflow because it installs Julia, JADE and an open-source solver and can run much longer than ordinary repository refreshes.  It is primarily manual and publishes only derived artifact outputs.

## Change detection and provenance

A recurring problem in automated data repositories is treating harmless metadata churn as a substantive data change.  This repository explicitly separates them.

Examples:

- A changed `sources.json` header does not automatically mean a model input changed.
- GUEHMT's volatile `Run at:` line is ignored when determining whether distributed-generation observations changed.
- HMD and wholesale Azure ETags allow unchanged files to be skipped without downloading their contents.
- Reconciliation uses monthly revision ETags before deciding whether to stream and aggregate a source month.
- SOSA and EDGS use conditional HTTP requests where supported.

Source/provenance metadata is still retained where useful because it answers a different question: **which authoritative source version produced this repository state?**

The GitHub Actions run summary records:

- source changed / unchanged;
- substantive model-input changed / unchanged;
- force-rebuild state; and
- files committed by the run.

A routine no-change run should therefore visibly do little beyond its parallel source checks.

## Reproducing locally

Python 3.12 is the workflow reference environment.

A broad dependency set sufficient for most model/build scripts is:

```bash
python -m pip install requests openpyxl matplotlib scipy pandas beautifulsoup4 numpy
```

Examples:

```bash
# Fast HMD source check
python update_data.py

# Explicit full HMD discovery
python update_hmd_sources.py --full-discovery

# Fast wholesale source check
python update_wholesale.py

# Explicit full wholesale history scan
python update_wholesale.py --full-scan

# Reconciliation check/update
python update_reconciliation.py

# Distributed generation source check
python update_distributed_generation.py

# Normalize core source layers
python normalize_hydro.py
python normalize_wholesale.py
python normalize_distributed_generation.py
python normalize_mbie_edgs.py

# Rebuild selected public comparisons
python build_hydro_trough_counterfactual.py
python build_sosa_distributed_comparison.py
```

Some JADE diagnostics additionally require Julia and packages installed by the dedicated GitHub Actions workflow.

The automated workflows are the preferred reproducible execution path because they encode the intended cadence, dependency gates and source-check behavior.

## Data and output conventions

### `data/metadata/`

Contains source provenance rather than model results.  Typical records include:

- Azure blob names;
- ETags;
- Last-Modified values;
- selected source versions;
- JADE publication manifests;
- source schemas and diagnostics.

### Source-local `model/` folders

Folders such as `data/distributed_generation/model/`, `data/wholesale/model/` and `data/mbie/edgs2024/model/` contain normalized or source-domain-specific derived data.

### `data/model/`

Contains cross-source model outputs, comparison tables, diagnostics and summaries that do not belong cleanly to a single upstream dataset.

### `data/visuals/`

Contains generated public-facing figures.  A PNG should normally be reproducible from a named `build_*.py` script and checked-in source/model inputs.

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
- shortage/unserved-energy metrics where available; and
- water-value / price-stress indicators where professional-model outputs support them.

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

Not every model or source can populate every field.  Missing values should remain explicit rather than being inferred silently.

## Experimental and historical work retained in the repository

The repository contains exploratory scripts developed while learning the source data and testing alternative approaches, particularly around Waitaki routing, storage constraints, thermal timing, and solar-conservation experiments.

They are retained because they provide:

- an audit trail of how HMD fields were interpreted;
- useful diagnostics for source semantics;
- targeted experiments that may remain valuable; and
- reproducibility for figures or conclusions produced during development.

They should **not** be taken as the current authoritative national hydro model.  Where conclusions depend on hydro operating policy, professional JADE evidence should be preferred unless a custom calculation is clearly labelled as such.

## Adding or changing a source

When adding a new upstream dataset:

1. add/update its entry in `data/source_registry.csv`;
2. store provenance metadata separately from substantive normalized data;
3. use ETags, Last-Modified, source manifests, checksums or equivalent version metadata where available;
4. avoid downloading unchanged large files;
5. expose a substantive change signal if metadata can change independently of model inputs;
6. assign an appropriate polling cadence rather than defaulting everything to daily;
7. document which downstream builders depend on it; and
8. retain only the raw source material necessary for reproducibility.

When changing modelling logic, keep that separate from source-acquisition optimisation wherever practical.  Workflow refactors should not quietly change model assumptions.

## Reproducibility standard

Every published chart/table should be traceable to:

- authoritative upstream source;
- source version/date or ETag where available;
- scenario assumptions;
- transformation/build script;
- professional model used, where applicable; and
- whether the result is observed, modelled, adjusted, or derived.

The goal is to make the visual story simple without making the methodology opaque.

## Known scope limitations

- Reconciliation calibration is currently focused on 2024 rather than being a continuously expanding settlement-history model.
- SOSA comparisons currently use the 2026 supplementary dataset and are delta comparisons, not a Transpower model rerun.
- EDGS normalization is based on EDGS 2024 workbooks.
- The project does not yet provide a complete nodal/transmission-constrained national dispatch simulation of its own.
- Some exploratory scripts remain in the repository because they are useful diagnostics even though they are no longer the preferred modelling path.
- Upstream public datasets can change structure or publication practices; source-check scripts deliberately fail loudly where expected schema/source selections disappear.

## Licence

Repository code is licensed under AGPL-3.0 unless otherwise noted.  Upstream datasets and modelling tools retain their own licences, copyright and terms of use.
