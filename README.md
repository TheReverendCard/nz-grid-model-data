# NZ Grid Public Data

An open, automatically refreshed collection of public New Zealand electricity data from the Electricity Authority and Transpower.

**Main branch scope:** observed/source data, cleaned CSVs, provenance metadata, and a lightweight public dashboard. Experimental dispatch, dry-year, Waitaki, calibration, and future-scenario modelling remain off `main` for now.

## Public datasets

- Hydro storage, inflows, spills and infrastructure constraints
- Wholesale generation by plant and fuel, with history back to 1997 where available
- Grid-export demand data
- Distributed solar installations, including residential/non-residential and with/without-battery splits
- Transpower generation and energy-storage connection pipeline
- Wholesale reference-node price history
- Dashboard-ready renewable share, hydro storage, solar uptake, price and correlation CSVs

Open `docs/index.html` through GitHub Pages (when Pages is enabled) for the dashboard. Every chart also links to its underlying CSV.

Sources retain provenance metadata under `data/metadata/`.
