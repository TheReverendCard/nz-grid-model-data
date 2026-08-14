import time
import os
import pandas as pd
from playwright.sync_api import sync_playwright

def download_ea_csv(url, filename, is_hydro=False):
    print(f"Opening browser to fetch {filename}...")
    with sync_playwright() as p:
        # Launch headless Chromium with standard desktop viewport
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        try:
            # Handle download event
            with page.expect_download(timeout=60000) as download_info:
                page.goto(url, wait_until="networkidle")
            
            download = download_info.value
            temp_path = f"temp_{filename}"
            download.save_as(temp_path)
            
            # Read and clean
            comment_char = '#' if is_hydro else None
            df = pd.read_csv(temp_path, comment=comment_char)
            
            if not df.empty:
                df.to_csv(filename, index=False)
                print(f"SUCCESS: Saved {filename} ({len(df)} rows)")
            
            if os.path.exists(temp_path):
                os.remove(temp_path)
                
        except Exception as e:
            print(f"Error fetching {filename} via Playwright: {e}")
        finally:
            browser.close()

# 1. Hydro Storage
hydro_url = "https://www.emi.ea.govt.nz/Environment/Download/DataReport/CSV/3UN1KD?DateFrom=20200101&RegionCode=NZ"
download_ea_csv(hydro_url, "hydro_storage.csv", is_hydro=True)

# 2. Generation Pipeline
pipeline_url = "https://www.emi.ea.govt.nz/Wholesale/Download/DataReport/CSV/ProposedGenerationFleet"
download_ea_csv(pipeline_url, "generation_pipeline.csv", is_hydro=False)
