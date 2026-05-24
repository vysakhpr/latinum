import sqlite3
import os
import sys
from datetime import datetime
from mftool import Mftool

# Configuration: Scheme Codes, Names, Categories, and Benchmarks
# Benchmarks:
#   120716: UTI Nifty 50 Index Fund Direct Growth (Proxy for Nifty 50 TRI)
#   147622: Motilal Oswal Nifty Midcap 150 Index Fund Direct (Proxy for Nifty Midcap 150 TRI)
#   150468: ICICI Prudential Nifty IT Index Fund Direct Growth (Proxy for Nifty IT TRI)
FUND_CONFIG = [
    # Benchmarks (Excluded from screening recommendations)
    {"code": "120716", "name": "UTI Nifty 50 Index Fund - Growth Option- Direct", "category": "Benchmark", "benchmark_code": None},
    {"code": "147622", "name": "Motilal Oswal Nifty Midcap 150 Index Fund - Direct Plan", "category": "Benchmark", "benchmark_code": None},
    {"code": "150468", "name": "ICICI Prudential Nifty IT Index Fund - Direct Plan - Growth", "category": "Benchmark", "benchmark_code": None},
    
    # Active Flexi-Cap (Benchmark: Nifty 50)
    {"code": "122639", "name": "Parag Parikh Flexi Cap Fund - Direct Plan - Growth", "category": "Flexi-Cap", "benchmark_code": "120716"},
    {"code": "120843", "name": "quant Flexi Cap Fund - Growth Option-Direct Plan", "category": "Flexi-Cap", "benchmark_code": "120716"},
    {"code": "118955", "name": "HDFC Flexi Cap Fund - Growth Option - Direct Plan", "category": "Flexi-Cap", "benchmark_code": "120716"},
    
    # Active Mid-Cap (Benchmark: Nifty Midcap 150)
    {"code": "127042", "name": "Motilal Oswal Midcap Fund-Direct Plan-Growth Option", "category": "Mid-Cap", "benchmark_code": "147622"},
    {"code": "118989", "name": "HDFC Mid Cap Fund - Growth Option - Direct Plan", "category": "Mid-Cap", "benchmark_code": "147622"},
    {"code": "120841", "name": "quant Mid Cap Fund - Growth Option - Direct Plan", "category": "Mid-Cap", "benchmark_code": "147622"},
    
    # Active Small-Cap (Benchmark: Nifty Midcap 150)
    {"code": "118778", "name": "Nippon India Small Cap Fund - Direct Plan Growth Plan - Growth Option", "category": "Small-Cap", "benchmark_code": "147622"},
    {"code": "120828", "name": "quant Small Cap Fund - Growth Option - Direct Plan", "category": "Small-Cap", "benchmark_code": "147622"},
    {"code": "145206", "name": "Tata Small Cap Fund-Direct Plan-Growth", "category": "Small-Cap", "benchmark_code": "147622"},
    
    # Active Value/Contra (Benchmark: Nifty 50)
    {"code": "120323", "name": "ICICI Prudential Value Fund (erstwhile Value Discovery Fund) - Direct Plan - Growth", "category": "Value", "benchmark_code": "120716"},
    {"code": "119835", "name": "SBI CONTRA FUND - DIRECT PLAN - GROWTH", "category": "Value", "benchmark_code": "120716"},
    {"code": "118481", "name": "Bandhan Value Fund - Direct Plan - Growth", "category": "Value", "benchmark_code": "120716"},
    
    # Active Sector - Infrastructure (Benchmark: Nifty 50)
    {"code": "120833", "name": "quant Infrastructure Fund - Growth Option-Direct Plan", "category": "Infrastructure", "benchmark_code": "120716"},
    {"code": "120621", "name": "ICICI Prudential Infrastructure Fund - Direct Plan -  Growth", "category": "Infrastructure", "benchmark_code": "120716"},
    
    # Active Sector - Banking & Financial Services (Benchmark: Nifty 50)
    {"code": "148986", "name": "HDFC Banking & Financial Services Fund - Growth Option - Direct Plan", "category": "Banking-Financial", "benchmark_code": "120716"},
    {"code": "133859", "name": "SBI BANKING & FINANCIAL SERVICES FUND - DIRECT PLAN - GROWTH", "category": "Banking-Financial", "benchmark_code": "120716"},
    
    # Active Sector - Healthcare & Pharma (Benchmark: Nifty 50)
    {"code": "118759", "name": "Nippon India Pharma Fund - Direct Plan Growth Plan - Growth Option", "category": "Healthcare-Pharma", "benchmark_code": "120716"},
    {"code": "145454", "name": "DSP Healthcare Fund - Direct Plan - Growth", "category": "Healthcare-Pharma", "benchmark_code": "120716"},
    
    # Active Sector - Technology (Benchmark: Nifty IT Index Fund)
    {"code": "135800", "name": "Tata Digital India Fund-Direct Plan-Growth", "category": "Technology", "benchmark_code": "150468"},
    {"code": "120578", "name": "SBI TECHNOLOGY OPPORTUNITIES FUND - DIRECT PLAN - GROWTH", "category": "Technology", "benchmark_code": "150468"},
    
    # Active Sector - Consumption (Benchmark: Nifty 50)
    {"code": "135805", "name": "Tata India Consumer Fund-Direct Plan-Growth", "category": "Consumption", "benchmark_code": "120716"},
    {"code": "120575", "name": "SBI CONSUMPTION OPPORTUNITIES FUND - DIRECT PLAN - GROWTH", "category": "Consumption", "benchmark_code": "120716"},
    
    # UTI Nifty Next 50 Benchmark Index (Excluded from screening recommendations)
    {"code": "143341", "name": "UTI Nifty Next 50 Index Fund - Direct Plan - Growth Option", "category": "Benchmark", "benchmark_code": None},
    
    # Active Large-Cap (Benchmark: Nifty 50)
    {"code": "148507", "name": "Sundaram Large Cap Fund (Formerly Known as Sundaram Blue Chip Fund)Direct Plan - Growth", "category": "Large-Cap", "benchmark_code": "120716"},
    {"code": "118269", "name": "CANARA ROBECO LARGE CAP FUND - DIRECT PLAN - GROWTH OPTION", "category": "Large-Cap", "benchmark_code": "120716"},

    # Active Multi-Cap (Benchmark: Nifty 50)
    {"code": "120823", "name": "quant Multi Cap Fund-GROWTH OPTION-Direct Plan", "category": "Multi-Cap", "benchmark_code": "120716"},
    {"code": "141226", "name": "Mahindra Manulife Multi Cap Fund - Direct Plan -Growth", "category": "Multi-Cap", "benchmark_code": "120716"},

    # Active ELSS & Thematic (Benchmark: Nifty 50)
    {"code": "118473", "name": "BANDHAN ELSS Tax Saver Fund - Direct Plan - Growth", "category": "ELSS", "benchmark_code": "120716"},
    {"code": "118539", "name": "Franklin India Opportunities Fund - Direct - Growth", "category": "Thematic", "benchmark_code": "120716"},

    # Debt Fund (Excluded from active equity rotation)
    {"code": "119533", "name": "Aditya Birla Sun Life Corporate Bond Fund - Growth - Direct Plan", "category": "Debt", "benchmark_code": "120716"},

    # Additional Mid-Caps (Benchmark: Nifty Midcap 150)
    {"code": "118668", "name": "Nippon India Growth Mid Cap Fund - Direct Plan Growth Plan - Growth Option", "category": "Mid-Cap", "benchmark_code": "147622"},
    {"code": "150584", "name": "WhiteOak Capital Mid Cap Fund Direct Plan Growth", "category": "Mid-Cap", "benchmark_code": "147622"},
    {"code": "125307", "name": "PGIM India Midcap Fund - Direct Plan - Growth Option", "category": "Mid-Cap", "benchmark_code": "147622"},

    # Additional Small-Caps (Benchmark: Nifty Midcap 150)
    {"code": "145137", "name": "Invesco India Smallcap Fund - Direct Plan - Growth", "category": "Small-Cap", "benchmark_code": "147622"},
    {"code": "146130", "name": "CANARA ROBECO SMALL CAP FUND - DIRECT PLAN - GROWTH OPTION", "category": "Small-Cap", "benchmark_code": "147622"},
    {"code": "147946", "name": "BANDHAN SMALL CAP FUND - DIRECT PLAN GROWTH", "category": "Small-Cap", "benchmark_code": "147622"},
    {"code": "125354", "name": "Axis Small Cap Fund - Direct Plan - Growth", "category": "Small-Cap", "benchmark_code": "147622"}
]

DB_NAME = "active_fund_advisor.db"

def init_db():
    """Initializes the SQLite database and tables."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # 1. Funds table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS funds (
            scheme_code TEXT PRIMARY KEY,
            scheme_name TEXT NOT NULL,
            category TEXT NOT NULL,
            benchmark_code TEXT
        )
    """)
    
    # 2. NAV History table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS nav_history (
            scheme_code TEXT NOT NULL,
            date TEXT NOT NULL,
            nav REAL NOT NULL,
            PRIMARY KEY (scheme_code, date)
        )
    """)
    
    # 3. Holdings table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS holdings (
            scheme_code TEXT PRIMARY KEY,
            units REAL NOT NULL,
            avg_purchase_price REAL NOT NULL,
            purchase_date TEXT NOT NULL
        )
    """)
    
    # 4. Scores table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS scores (
            scheme_code TEXT NOT NULL,
            date TEXT NOT NULL,
            rolling_window TEXT NOT NULL,
            cagr REAL,
            volatility REAL,
            beta REAL,
            beta_benchmark REAL,
            beta_it REAL,
            jensen_alpha REAL,
            tracking_error REAL,
            tracking_error_nifty50 REAL,
            information_ratio REAL,
            upside_capture REAL,
            downside_capture REAL,
            win_rate REAL,
            composite_score REAL,
            PRIMARY KEY (scheme_code, date, rolling_window)
        )
    """)
    
    # 5. Bot State table (stores Telegram metadata references like latest_file_id)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bot_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    
    conn.commit()
    return conn

def seed_funds(conn):
    """Seeds the tracked list of funds and benchmarks."""
    cursor = conn.cursor()
    for fund in FUND_CONFIG:
        cursor.execute("""
            INSERT OR REPLACE INTO funds (scheme_code, scheme_name, category, benchmark_code)
            VALUES (?, ?, ?, ?)
        """, (fund["code"], fund["name"], fund["category"], fund["benchmark_code"]))
    conn.commit()
    print("Fund configurations seeded successfully.")

def get_latest_nav_date(conn, scheme_code):
    """Gets the latest date we have NAV data for a given scheme."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT MAX(date) FROM nav_history WHERE scheme_code = ?
    """, (scheme_code,))
    result = cursor.fetchone()
    return result[0] if result else None

def parse_date(date_str):
    """Parses date from DD-MM-YYYY to YYYY-MM-DD."""
    try:
        dt = datetime.strptime(date_str.strip(), "%d-%m-%Y")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        try:
            dt = datetime.strptime(date_str.strip(), "%d-%b-%Y")
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            return None

def fetch_and_store_nav(conn):
    """Fetches historical NAV for all configured funds incrementally."""
    obj = Mftool()
    cursor = conn.cursor()
    
    for fund in FUND_CONFIG:
        code = fund["code"]
        name = fund["name"]
        print(f"\nFetching NAV for {name} ({code})...")
        
        latest_date = get_latest_nav_date(conn, code)
        print(f"  Latest date in database: {latest_date}")
        
        try:
            raw_data = obj.get_scheme_historical_nav(code)
            if not raw_data or 'data' not in raw_data:
                print(f"  ⚠️ No historical data returned for code {code}.")
                continue
            
            records = raw_data['data']
            print(f"  Retrieved {len(records)} historical data points from AMFI.")
            
            # Prepare rows to insert
            to_insert = []
            for item in records:
                raw_date = item.get('date')
                raw_nav = item.get('nav')
                
                if not raw_date or not raw_nav:
                    continue
                
                db_date = parse_date(raw_date)
                if not db_date:
                    continue
                
                # Incremental check: only process records newer than the latest date in db
                if latest_date and db_date <= latest_date:
                    continue
                
                try:
                    nav_val = float(raw_nav)
                except ValueError:
                    continue
                
                to_insert.append((code, db_date, nav_val))
                
            if to_insert:
                print(f"  Inserting {len(to_insert)} new NAV records into database...")
                cursor.executemany("""
                    INSERT OR IGNORE INTO nav_history (scheme_code, date, nav)
                    VALUES (?, ?, ?)
                """, to_insert)
                conn.commit()
                print("  ✅ Incremental update completed.")
            else:
                print("  Already up-to-date. No new records to insert.")
                
        except Exception as e:
            print(f"  💥 Error fetching NAV for code {code}: {e}")

def main():
    print("Starting Daily Data-Ingestion Script...")
    conn = init_db()
    seed_funds(conn)
    fetch_and_store_nav(conn)
    conn.close()
    print("\nDaily Ingestion Script Finished.")

if __name__ == "__main__":
    main()
