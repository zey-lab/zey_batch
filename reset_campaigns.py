import pandas as pd
from pathlib import Path

file_path = Path("data/campaigns.xlsx")
if file_path.exists():
    print(f"Reading {file_path}...")
    df = pd.read_excel(file_path)
    
    print("Resetting campaign status...")
    # Reset columns
    df["Campaign Process Date"] = None
    df["Campaign Process Status"] = None
    
    # Save back
    print("Saving file...")
    df.to_excel(file_path, index=False)
    print("Campaigns reset successfully. You can now re-run the application.")
else:
    print(f"File not found: {file_path}")
