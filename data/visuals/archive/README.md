# Monthly generation pipeline archive

The current generation-pipeline chart lives in `data/visuals/generation_pipeline_monthly_latest.*`.

When a later Electricity Authority investment-pipeline snapshot replaces the current snapshot, the render script copies the prior PNG, SVG, and plot-data CSV into this folder using the prior `YYYY-MM` snapshot month before rendering the new current chart.

August 2026 is the first retained monthly snapshot, so there is intentionally no July 2026 archive chart.
