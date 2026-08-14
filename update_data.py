import os
import io
import time
import pandas as pd
from playwright.sync_api import sync_playwright

def fetch_ea_dataset(url, filename, is_hydro=False):
    print(f"Opening browser to fetch {filename} from {url}...")
    with sync_playwright() as p:
        # Launch Chromium with standard desktop resolution and stealth headers
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            accept_downloads=True,
            viewport={'width': 1920, 'height': 1080}
        )
        page = context.new_page()

        try:
            # Navigate to URL and wait for Cloudflare JS challenge to resolve
            response = page.goto(url, wait_until="networkidle", timeout=60000)
            
            # Allow time for Cloudflare challenge redirection if present
            time.sleep(5)
            
            # Check response text
            raw_text = page.content()
            
            # If page contains HTML tags, wait briefly and fetch inner body/text
            if "<html" in raw_text.lower() or "<body" in raw_text.lower():
                # Extract text content if browser navigated to direct text view
                raw_text = page.evaluate("() => document.body.innerText").strip()

            if not raw_text or "<doctype" in raw_text.lower():
                print(f"FAILED: Cloudflare challenge blocked {filename}")
                return

            # Clean and parse into Pandas
            comment_char = '#' if is_hydro else None
            df = pd.read_csv(io.StringIO(raw_text), comment=comment_char)

            if not df.empty:
                df.to_csv(filename, index=False)
                print(f"SUCCESS: Saved {filename} ({len(df)} rows, {len(df.columns)} cols)")
            else:
                print(f"FAILED: Dataframe empty for {filename}")

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
