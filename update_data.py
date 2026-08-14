import os
import io
import pandas as pd
from playwright.sync_api import sync_playwright

def fetch_ea_dataset(url, filename, is_hydro=False):
    print(f"Opening browser to fetch {filename} from {url}...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            accept_downloads=True,
            viewport={'width': 1920, 'height': 1080}
        )
        page = context.new_page()

        try:
            # Tell Playwright to expect a file download instead of a normal webpage
            with page.expect_download(timeout=60000) as download_info:
                page.goto(url)
            
            # Save the raw file to a temporary location
            download = download_info.value
            temp_path = f"temp_{filename}"
            download.save_as(temp_path)
            
            # Read the downloaded file into Pandas to clean metadata
            comment_char = '#' if is_hydro else None
            df = pd.read_csv(temp_path, comment=comment_char)
            
            # Save the clean CSV and delete the temp file
            if not df.empty:
                df.to_csv(filename, index=False)
                print(f"SUCCESS: Saved {filename} ({len(df)} rows, {len(df.columns)} cols)")
            else:
                print(f"FAILED: Downloaded file was empty for {filename}")
                
            if os.path.exists(temp_path):
                os.remove(temp_path)

        except Exception as e:
            print(f"ERROR processing {filename}: {e}")
        finally:
            browser.close()

# 1. Hydro Storage Data
hydro_url = "https://www.emi.ea.govt.nz/Environment/Download/DataReport/CSV/3UN1KD?DateFrom=20200101&RegionCode=NZ"
fetch_ea_dataset(hydro_url, "hydro_storage.csv", is_hydro=True)

# 2. Generation Investment Pipeline
pipeline_url = "https://www.emi.ea.govt.nz/Wholesale/Download/DataReport/CSV/ProposedGenerationFleet"
fetch_ea_dataset(pipeline_url, "generation_pipeline.csv", is_hydro=False)
