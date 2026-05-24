import sqlite3
import os
import glob
import re
import pandas as pd
from datetime import datetime, timedelta

DB_NAME = "active_fund_advisor.db"
DOWNLOADS_DIR = "/Users/vysakhpr/Downloads"

def clean_name(name):
    """Tokenizes and cleans a fund name to extract core distinguishing words."""
    # Replace hyphens and slashes with spaces to avoid merging words
    name = name.replace('-', ' ').replace('/', ' ')
    # Remove punctuation, parentheses, and lowercase
    name = re.sub(r'[^\w\s]', '', name.lower())
    words = name.split()
    
    # Common noise words in mutual fund names
    stop_words = {
        'fund', 'growth', 'direct', 'plan', 'option', 'mutual', 'amc', 
        'growthoption', 'regular', 'dividend', 'idcw', 'scheme'
    }
    
    cleaned = [w for w in words if w not in stop_words]
    return set(cleaned)

def find_best_match(excel_name, db_funds):
    """
    Finds the best matching fund from DB seeded funds.
    Returns (scheme_code, score) or (None, 0).
    """
    excel_words = clean_name(excel_name)
    if not excel_words:
        return None, 0.0
        
    best_code = None
    best_score = 0.0
    
    # Define category tokens to check for conflicts
    categories = {
        'small', 'mid', 'large', 'multi', 'flexi', 'contra', 'value', 'tax', 
        'elss', 'infrastructure', 'banking', 'pharma', 'healthcare', 'digital', 
        'consumer', 'consumption'
    }
    
    # Define AMC tokens to check for conflicts
    amcs = {
        'nippon', 'hdfc', 'quant', 'motilal', 'sundaram', 'axis', 'invesco', 
        'uti', 'canara', 'robeco', 'bandhan', 'sbi', 'tata', 'icici', 
        'prudential', 'franklin', 'aditya', 'birla', 'whiteoak', 'pgim', 'dsp'
    }
    
    for code, db_name in db_funds:
        db_words = clean_name(db_name)
        if not db_words:
            continue
            
        # 1. Strict AMC Match Check
        excel_amc = excel_words.intersection(amcs)
        db_amc = db_words.intersection(amcs)
        if excel_amc and db_amc and excel_amc != db_amc:
            continue
            
        # 2. Strict Category Conflict Check (e.g. prevent mid matching small)
        excel_cats = excel_words.intersection(categories)
        db_cats = db_words.intersection(categories)
        if excel_cats and db_cats and excel_cats != db_cats:
            continue
            
        # 3. Strict Index Keyword Checks (prevent Nifty 50 matching Nifty Next 50)
        if ('next' in excel_words) != ('next' in db_words):
            continue
        if ('50' in excel_words) != ('50' in db_words):
            continue
        if ('150' in excel_words) != ('150' in db_words):
            continue
            
        intersection = excel_words.intersection(db_words)
        
        # Overlap score
        score = len(intersection) / max(len(excel_words), len(db_words))
        
        # Boost score if one name is a subset of another
        if len(intersection) == len(excel_words) or len(intersection) == len(db_words):
            score = max(score, len(intersection) / min(len(excel_words), len(db_words)))
            
        if score > best_score:
            best_score = score
            best_code = code
            
    # Require 70% confidence overlap to accept the match
    if best_score >= 0.70:
        return best_code, best_score
        
    return None, 0.0

def get_latest_groww_file():
    """Scans the Downloads directory and returns the path to the newest Mutual_Funds_*.xlsx file."""
    pattern = os.path.join(DOWNLOADS_DIR, "Mutual_Funds_*.xlsx")
    files = glob.glob(pattern)
    if not files:
        return None
    # Sort files by modification time descending
    files.sort(key=os.path.getmtime, reverse=True)
    return files[0]

def import_portfolio():
    excel_path = get_latest_groww_file()
    if not excel_path:
        print(f"❌ Error: No Groww Mutual Fund statement found in {DOWNLOADS_DIR} matching 'Mutual_Funds_*.xlsx'")
        return
        
    print(f"📂 Found Groww statement: {os.path.basename(excel_path)}")
    
    # Load sheet
    try:
        df = pd.read_excel(excel_path, sheet_name="Holdings")
    except Exception as e:
        print(f"❌ Error reading Excel file: {e}")
        return
        
    # Find the header row containing "Scheme Name"
    scheme_col = df.columns[0]
    header_indices = df[df.iloc[:, 0] == "Scheme Name"].index
    if len(header_indices) == 0:
        # Search other columns just in case
        for col_idx in range(df.shape[1]):
            header_indices = df[df.iloc[:, col_idx] == "Scheme Name"].index
            if len(header_indices) > 0:
                break
                
    if len(header_indices) == 0:
        print("❌ Error: Could not locate the 'Scheme Name' header in the Excel sheet.")
        return
        
    header_row_idx = header_indices[0]
    
    # Set header and slice data
    df.columns = df.iloc[header_row_idx]
    holdings_df = df.iloc[header_row_idx + 1:].copy()
    holdings_df.dropna(subset=["Scheme Name"], inplace=True)
    
    # Remove any footer summary rows (XIRR/Total etc) by filtering rows that don't have AMC or Units
    holdings_df = holdings_df[holdings_df["Units"].notna()]
    
    # Fetch seeded funds from DB
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT scheme_code, scheme_name FROM funds")
    db_funds = cursor.fetchall()
    
    print("\n⏳ Mapping Groww holdings to active screening database...")
    print("=" * 60)
    
    imported_count = 0
    skipped_count = 0
    
    # Default purchase date: 366 days ago (LTCG status)
    default_purchase_date = (datetime.now() - timedelta(days=366)).strftime("%Y-%m-%d")
    
    # Clean holdings table before import to keep it fresh
    cursor.execute("DELETE FROM holdings")
    
    for _, row in holdings_df.iterrows():
        excel_name = str(row["Scheme Name"]).strip()
        units = float(row["Units"])
        invested_val = float(row["Invested Value"])
        
        if units <= 0:
            continue
            
        avg_price = invested_val / units
        
        # Find match
        scheme_code, score = find_best_match(excel_name, db_funds)
        
        if scheme_code:
            # Get actual DB scheme name
            cursor.execute("SELECT scheme_name FROM funds WHERE scheme_code = ?", (scheme_code,))
            db_name = cursor.fetchone()[0]
            
            cursor.execute("""
                INSERT OR REPLACE INTO holdings (scheme_code, units, avg_purchase_price, purchase_date)
                VALUES (?, ?, ?, ?)
            """, (scheme_code, units, avg_price, default_purchase_date))
            
            print(f"✅ Matched: '{excel_name}'")
            print(f"   -> DB Fund: '{db_name}' ({scheme_code})")
            print(f"   -> Units: {units:.3f} | Invested: ₹{invested_val:,.2f} | Avg Price: ₹{avg_price:.4f}")
            imported_count += 1
        else:
            print(f"⚠️ Skipped: '{excel_name}' (No active screening database match)")
            skipped_count += 1
            
    conn.commit()
    conn.close()
    
    print("=" * 60)
    print(f"🎉 Import Completed: Successfully imported {imported_count} holdings. Skipped {skipped_count} irrelevant funds.")
    print("ℹ️ Note: Purchase dates have been defaulted to 366 days ago (LTCG). Update manually in DB if you want precise STCG simulation.")

def parse_groww_sheet_in_memory(file_content):
    """Parses a Groww statement from in-memory file content (bytes).
    
    Returns a list of tuples: (scheme_code, units, avg_price, purchase_date)
    """
    import io
    try:
        df = pd.read_excel(io.BytesIO(file_content), sheet_name="Holdings")
    except Exception as e:
        print(f"❌ Error reading Excel file: {e}")
        return []
        
    # Find the header row containing "Scheme Name"
    scheme_col = df.columns[0]
    header_indices = df[df.iloc[:, 0] == "Scheme Name"].index
    if len(header_indices) == 0:
        for col_idx in range(df.shape[1]):
            header_indices = df[df.iloc[:, col_idx] == "Scheme Name"].index
            if len(header_indices) > 0:
                break
                
    if len(header_indices) == 0:
        print("❌ Error: Could not locate the 'Scheme Name' header in the Excel sheet.")
        return []
        
    header_row_idx = header_indices[0]
    
    # Set header and slice data
    df.columns = df.iloc[header_row_idx]
    holdings_df = df.iloc[header_row_idx + 1:].copy()
    holdings_df.dropna(subset=["Scheme Name"], inplace=True)
    
    # Remove any footer summary rows
    holdings_df = holdings_df[holdings_df["Units"].notna()]
    
    # Fetch seeded funds from DB
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT scheme_code, scheme_name FROM funds")
    db_funds = cursor.fetchall()
    conn.close()
    
    holdings_list = []
    default_purchase_date = (datetime.now() - timedelta(days=366)).strftime("%Y-%m-%d")
    
    for _, row in holdings_df.iterrows():
        excel_name = str(row["Scheme Name"]).strip()
        try:
            units = float(row["Units"])
            invested_val = float(row["Invested Value"])
        except (ValueError, TypeError):
            continue
            
        if units <= 0:
            continue
            
        avg_price = invested_val / units
        
        # Find match
        scheme_code, score = find_best_match(excel_name, db_funds)
        if scheme_code:
            holdings_list.append((scheme_code, units, avg_price, default_purchase_date))
            
    return holdings_list

if __name__ == "__main__":
    import_portfolio()
