import sqlite3
import os
import sys

def print_result(name, passed, detail=""):
    status = "✅ PASSED" if passed else "❌ FAILED"
    print(f"{status} - {name} {detail}")

def check_database_exists():
    exists = os.path.exists("active_fund_advisor.db")
    print_result("Database file exists", exists, "(active_fund_advisor.db)")
    return exists

def check_tables():
    conn = sqlite3.connect("active_fund_advisor.db")
    cursor = conn.cursor()
    
    expected_tables = ["funds", "nav_history", "holdings", "scores"]
    all_passed = True
    
    for table in expected_tables:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
        exists = cursor.fetchone() is not None
        print_result(f"Table exists: {table}", exists)
        if not exists:
            all_passed = False
            
    conn.close()
    return all_passed

def check_seed_data():
    conn = sqlite3.connect("active_fund_advisor.db")
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM funds")
    count = cursor.fetchone()[0]
    passed = count == 15
    print_result("Funds table seeded", passed, f"(Found {count}/15 funds)")
    
    cursor.execute("SELECT COUNT(*) FROM funds WHERE category = 'Benchmark'")
    bench_count = cursor.fetchone()[0]
    print_result("Benchmark funds configured", bench_count == 3, f"(Found {bench_count}/3 benchmarks)")
    
    conn.close()
    return passed and (bench_count == 3)

def check_nav_history():
    conn = sqlite3.connect("active_fund_advisor.db")
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM nav_history")
    count = cursor.fetchone()[0]
    passed = count > 10000
    print_result("NAV history populated", passed, f"(Found {count} records in database)")
    
    # Check if we have data for UTI Nifty 50
    cursor.execute("SELECT COUNT(*) FROM nav_history WHERE scheme_code = '120716'")
    n50_count = cursor.fetchone()[0]
    print_result("Nifty 50 benchmark NAVs populated", n50_count > 500, f"({n50_count} records)")
    
    conn.close()
    return passed

def check_scoring_engine():
    try:
        import scoring_engine
        latest_date = scoring_engine.run_scoring_engine_for_all()
        conn = sqlite3.connect("active_fund_advisor.db")
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM scores WHERE date = ?", (latest_date,))
        count = cursor.fetchone()[0]
        conn.close()
        
        passed = count > 0
        print_result("Scoring Engine execution", passed, f"(Scored {count} items for {latest_date})")
        return passed
    except Exception as e:
        print_result("Scoring Engine execution", False, f"Error: {e}")
        return False

def check_weekly_advisor():
    try:
        import weekly_advisor
        latest_date = weekly_advisor.get_latest_date()
        passed = latest_date is not None
        print_result("Weekly Advisor report compilation check", passed, f"(Latest date is {latest_date})")
        return passed
    except Exception as e:
        print_result("Weekly Advisor report compilation check", False, f"Error: {e}")
        return False

def main():
    print("=== Pipeline Verification Suite ===")
    
    success = True
    success &= check_database_exists()
    if not success:
        print("Stopping tests: Database file is missing.")
        sys.exit(1)
        
    success &= check_tables()
    success &= check_seed_data()
    success &= check_nav_history()
    success &= check_scoring_engine()
    success &= check_weekly_advisor()
    
    print("===================================")
    if success:
        print("🎉 ALL TESTS PASSED! Pipeline is functional.")
        sys.exit(0)
    else:
        print("❌ SOME TESTS FAILED. Please check logs.")
        sys.exit(1)

if __name__ == "__main__":
    main()
