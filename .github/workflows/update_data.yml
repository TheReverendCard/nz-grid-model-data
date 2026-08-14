import os
import io
import pandas as pd
from playwright.sync_api import sync_playwright

def fetch_ea_dataset(url, filename, is_hydro=False):
    print(f"Fetching {filename} from {url}...")
    with sync_playwright() as p:
        # Launch Chromium context with realistic browser headers
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            accept_downloads=True
        )
        
        try:
            # Issue request using browser context to pass Cloudflare TLS/JA3 fingerprinting
            response = context.request.get(url, timeout=60000)
            
            if response.status == 200:
                raw_text = response.text().strip()
                
                # Check for empty response or Cloudflare challenge pages
                if not raw_text or "<html" in raw_text.lower() or "<!doctype" in raw_text.lower():
                    print(f"FAILED: Received HTML challenge instead of CSV for {filename}")
                    return
                
                # Parse CSV content into Pandas
                comment_char = '#' if is_hydro else None
                df = pd.read_csv(io.StringIO(raw_text), comment=comment_char)
                
                if not df.empty:
                    df.to_csv(filename, index=False)
                    print(f"SUCCESS: Saved {filename} ({len(df)} rows, {len(df.columns)} cols)")
                else:
                    print(f"FAILED: Dataframe was empty for {filename}")
            else:
                print(f"FAILED: HTTP {response.status} for {filename}")
                
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
