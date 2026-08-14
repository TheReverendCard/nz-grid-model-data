import os
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
            with page.expect_download(timeout=60000) as download_info:
                try:
                    page.goto(url)
                except Exception as goto_err:
                    if "Download is starting" not in str(goto_err) and "net::ERR_ABORTED" not in str(goto_err):
                        raise goto_err
            
            download = download_info.value
            temp_path = f"temp_{filename}"
            download.save_as(temp_path)
            
            # --- THE TRUTH SERUM ---
            # Print the first 500 characters of whatever we just downloaded
            with open(temp_path, 'r', encoding='utf-8', errors='ignore') as f:
                raw_preview = f.read(500)
            
            print(f"\n--- SNEAK PEEK: {filename} ---")
            print(raw_preview if raw_preview.strip() else "[FILE IS COMPLETELY EMPTY (0 BYTES)]")
            print("---------------------------------------\n")
            
            # Read into Pandas
            comment_char = '#' if is_hydro else None
            
            try:
                df = pd.read_csv(temp_path, comment=comment_char)
                if not df.empty:
                    df.to_csv(filename, index=False)
                    print(f"SUCCESS: Saved {filename} ({len(df)} rows, {len(df.columns)} cols)")
                else:
                    print(f"FAILED: Pandas parsed {filename} but found no data rows.")
            except pd.errors.EmptyDataError:
                print(f"FAILED: Pandas completely rejected {filename} (EmptyDataError).")
                
            if os.path.exists(temp_path):
                os.remove(temp_path)

        except Exception as e:
            print(f"ERROR processing {filename}: {e}")
        finally:
            browser.close()

hydro_url = "https://www.emi.ea.govt.nz/Environment/Download/DataReport/CSV/3UN1KD?DateFrom=20200101&RegionCode=NZ"
fetch_ea_dataset(hydro_url, "hydro_storage.csv", is_hydro=True)

pipeline_url = "https://www.emi.ea.govt.nz/Wholesale/Download/DataReport/CSV/ProposedGenerationFleet"
fetch_ea_dataset(pipeline_url, "generation_pipeline.csv", is_hydro=False)
