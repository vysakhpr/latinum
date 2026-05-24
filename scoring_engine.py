import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

DB_NAME = "active_fund_advisor.db"
RISK_FREE_RATE = 0.06  # 6.0% annualized risk-free rate

def load_data(scheme_code, benchmark_code, end_date_str, window_years):
    """
    Loads daily NAVs for the fund, its category benchmark, and Nifty 50,
    aligns them on date, applies forward-fill, and returns a pandas DataFrame.
    This guarantees we can retrieve the full 3y or 5y history for scoring.
    """
    conn = sqlite3.connect(DB_NAME)
    
    # Calculate start date
    end_date = datetime.strptime(end_date_str, "%Y-%m-%d")
    start_date = end_date - timedelta(days=int(window_years * 365.25))
    start_date_str = start_date.strftime("%Y-%m-%d")
    
    # UTI Nifty 50 code = '120716'
    n50_code = '120716'
    
    schemes = {
        'fund': scheme_code,
        'bench': benchmark_code,
        'nifty50': n50_code
    }
    
    dfs = []
    for col_name, code in schemes.items():
        query = """
            SELECT date, nav FROM nav_history
            WHERE scheme_code = ? AND date BETWEEN ? AND ?
            ORDER BY date ASC
        """
        df = pd.read_sql_query(query, conn, params=(code, start_date_str, end_date_str))
        if df.empty:
            conn.close()
            return None
        
        # Convert date to datetime, set index, rename nav
        df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)
        df.rename(columns={'nav': col_name}, inplace=True)
        dfs.append(df)
        
    conn.close()
    
    # Align all dataframes by outer join
    aligned_df = dfs[0]
    for df in dfs[1:]:
        aligned_df = aligned_df.join(df, how='outer')
        
    # Forward-fill missing values (e.g. minor holidays, lag) and drop remaining NaNs
    aligned_df.sort_index(inplace=True)
    aligned_df.ffill(inplace=True)
    aligned_df.dropna(inplace=True)
    
    return aligned_df

def calculate_beta_it(scheme_code, end_date_str, window_years):
    """
    Calculates the statistical beta of the fund against Nifty IT index fund (150468).
    Uses the maximum available overlapping daily returns in the window (minimum 60 days).
    """
    conn = sqlite3.connect(DB_NAME)
    end_date = datetime.strptime(end_date_str, "%Y-%m-%d")
    start_date = end_date - timedelta(days=int(window_years * 365.25))
    start_date_str = start_date.strftime("%Y-%m-%d")
    
    it_code = '150468'
    
    df_fund = pd.read_sql_query("""
        SELECT date, nav FROM nav_history
        WHERE scheme_code = ? AND date BETWEEN ? AND ?
        ORDER BY date ASC
    """, conn, params=(scheme_code, start_date_str, end_date_str))
    
    df_it = pd.read_sql_query("""
        SELECT date, nav FROM nav_history
        WHERE scheme_code = ? AND date BETWEEN ? AND ?
        ORDER BY date ASC
    """, conn, params=(it_code, start_date_str, end_date_str))
    
    conn.close()
    
    if df_fund.empty or df_it.empty:
        return 0.0
        
    df_fund['date'] = pd.to_datetime(df_fund['date'])
    df_fund.set_index('date', inplace=True)
    df_fund.rename(columns={'nav': 'fund'}, inplace=True)
    
    df_it['date'] = pd.to_datetime(df_it['date'])
    df_it.set_index('date', inplace=True)
    df_it.rename(columns={'nav': 'it'}, inplace=True)
    
    df_aligned = df_fund.join(df_it, how='inner')
    df_aligned.sort_index(inplace=True)
    df_aligned.ffill(inplace=True)
    df_aligned.dropna(inplace=True)
    
    if len(df_aligned) < 60:
        return 0.0
        
    df_returns = df_aligned.pct_change().dropna()
    if df_returns.empty:
        return 0.0
        
    cov = np.cov(df_returns['fund'], df_returns['it'])
    beta_it = cov[0, 1] / cov[1, 1] if cov[1, 1] > 0 else 0.0
    return float(beta_it)

def calculate_rolling_win_rate(scheme_code, benchmark_code, end_date_str, history_years=5):
    """
    Calculates the percentage of rolling 3-year windows where the fund's return
    exceeded the category benchmark's return. Uses a 5-year historical window.
    """
    df = load_data(scheme_code, benchmark_code, end_date_str, history_years)
    if df is None or len(df) < 756:  # Less than 3 years of trading days
        return 0.0
    
    # 3-year rolling return is NAV(t) / NAV(t - 756 trading days) - 1
    df_shifted_fund = df['fund'] / df['fund'].shift(756) - 1.0
    df_shifted_bench = df['bench'] / df['bench'].shift(756) - 1.0
    
    diff = df_shifted_fund - df_shifted_bench
    diff.dropna(inplace=True)
    
    if len(diff) == 0:
        return 0.0
    
    win_rate = (diff > 0).sum() / len(diff)
    return float(win_rate)

def calculate_metrics_for_window(scheme_code, benchmark_code, end_date_str, window_years):
    """
    Calculates all quantitative metrics over a specific rolling window (3y or 5y).
    """
    df = load_data(scheme_code, benchmark_code, end_date_str, window_years)
    
    # Require at least 80% of the expected trading days
    expected_days = int(window_years * 252)
    if df is None or len(df) < expected_days * 0.8:
        return None
        
    # 1. CAGR
    duration_years = (df.index[-1] - df.index[0]).days / 365.25
    cagr_p = (df['fund'].iloc[-1] / df['fund'].iloc[0]) ** (1.0 / duration_years) - 1.0
    cagr_m = (df['bench'].iloc[-1] / df['bench'].iloc[0]) ** (1.0 / duration_years) - 1.0
    cagr_n50 = (df['nifty50'].iloc[-1] / df['nifty50'].iloc[0]) ** (1.0 / duration_years) - 1.0
    
    # 2. Daily returns for statistical metrics
    df_returns = df.pct_change().dropna()
    if df_returns.empty:
        return None
        
    # Volatility (annualized)
    vol_p = df_returns['fund'].std() * np.sqrt(252)
    vol_m = df_returns['bench'].std() * np.sqrt(252)
    
    # 3. Beta against category benchmark
    cov_bench = np.cov(df_returns['fund'], df_returns['bench'])
    beta_bench = cov_bench[0, 1] / cov_bench[1, 1] if cov_bench[1, 1] > 0 else 1.0
    
    # 4. Beta against Nifty 50
    cov_n50 = np.cov(df_returns['fund'], df_returns['nifty50'])
    beta_n50 = cov_n50[0, 1] / cov_n50[1, 1] if cov_n50[1, 1] > 0 else 1.0
    
    # 5. Beta against Nifty IT (calculated separately to handle shorter Nifty IT history)
    beta_it = calculate_beta_it(scheme_code, end_date_str, window_years)
    
    # 6. Jensen's Alpha (annualized)
    jensen_alpha = cagr_p - (RISK_FREE_RATE + beta_bench * (cagr_m - RISK_FREE_RATE))
    
    # 7. Tracking Error against Category Benchmark
    te_bench = (df_returns['fund'] - df_returns['bench']).std() * np.sqrt(252)
    
    # 8. Tracking Error against Nifty 50
    te_n50 = (df_returns['fund'] - df_returns['nifty50']).std() * np.sqrt(252)
    
    # 9. Information Ratio
    ir = (cagr_p - cagr_m) / te_bench if te_bench > 0 else 0.0
    
    # 10. Upside & Downside Capture Ratios
    up_mask = df_returns['bench'] > 0
    down_mask = df_returns['bench'] < 0
    
    bench_up = (df_returns.loc[up_mask, 'bench'] + 1).prod() - 1.0
    fund_up = (df_returns.loc[up_mask, 'fund'] + 1).prod() - 1.0
    ucr = fund_up / bench_up if bench_up != 0 else 0.0
    
    bench_down = (df_returns.loc[down_mask, 'bench'] + 1).prod() - 1.0
    fund_down = (df_returns.loc[down_mask, 'fund'] + 1).prod() - 1.0
    dcr = fund_down / bench_down if bench_down != 0 else 0.0
    
    # 11. Rolling Return Win Rate (uses historical 5-year dataset for rolling 3y windows)
    # Win rate measures the consistency of outperforming the category benchmark
    win_rate = calculate_rolling_win_rate(scheme_code, benchmark_code, end_date_str, history_years=5)
    
    # 12. Composite Score calculation
    alpha_pct = jensen_alpha * 100.0  # 1% alpha -> 1.0 point
    ir_score = ir * 15.0             # IR of 1.5 -> 22.5 points
    
    # Closet indexer penalty / divergence reward:
    te_n50_pct = te_n50 * 100.0       # TE of 8% against N50 -> 8.0 points
    div_score = te_n50_pct * 2.0      # Scale tracking error
    if te_n50_pct < 4.0:              # closet indexer penalty
        div_score -= 30.0
        
    # Upside Capture Score:
    if ucr > 1.0:
        ucr_score = (ucr - 1.0) * 30.0 # Reward capturing >100% of bull runs
    else:
        ucr_score = (ucr - 1.0) * 15.0 # Penalize low capture
        
    # Win rate score:
    win_score = win_rate * 30.0       # 100% win rate -> 30 points
    
    composite_score = (alpha_pct * 3.0) + ir_score + div_score + ucr_score + win_score
    
    # Tech Beta Penalty: Massive penalty if beta against Nifty IT index fund > 0.4
    if beta_it > 0.4:
        composite_score -= 500.0
        
    return {
        "cagr": float(cagr_p),
        "volatility": float(vol_p),
        "beta": float(beta_n50),
        "beta_benchmark": float(beta_bench),
        "beta_it": float(beta_it),
        "jensen_alpha": float(jensen_alpha),
        "tracking_error": float(te_bench),
        "tracking_error_nifty50": float(te_n50),
        "information_ratio": float(ir),
        "upside_capture": float(ucr),
        "downside_capture": float(dcr),
        "win_rate": float(win_rate),
        "composite_score": float(composite_score)
    }

def run_scoring_engine_for_all(end_date_str=None):
    """
    Computes scores for all active funds for both 3y and 5y windows,
    and saves them to the SQLite scores table.
    """
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    if not end_date_str:
        cursor.execute("SELECT MAX(date) FROM nav_history")
        end_date_str = cursor.fetchone()[0]
        
    if not end_date_str:
        print("Error: No data in database to score.")
        conn.close()
        return None
        
    cursor.execute("SELECT scheme_code, scheme_name, benchmark_code FROM funds WHERE category != 'Benchmark'")
    active_funds = cursor.fetchall()
    
    print(f"Running Scoring Engine for date: {end_date_str}")
    
    for scheme_code, name, benchmark_code in active_funds:
        for window in [3, 5]:
            window_str = f"{window}y"
            try:
                metrics = calculate_metrics_for_window(scheme_code, benchmark_code, end_date_str, window)
                if not metrics:
                    print(f"  ⚠️ Skipped {name} ({window_str}): Insufficient historical data.")
                    continue
                
                cursor.execute("""
                    INSERT OR REPLACE INTO scores (
                        scheme_code, date, rolling_window, cagr, volatility, beta, beta_benchmark, beta_it,
                        jensen_alpha, tracking_error, tracking_error_nifty50, information_ratio,
                        upside_capture, downside_capture, win_rate, composite_score
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    scheme_code, end_date_str, window_str,
                    metrics["cagr"], metrics["volatility"], metrics["beta"], metrics["beta_benchmark"], metrics["beta_it"],
                    metrics["jensen_alpha"], metrics["tracking_error"], metrics["tracking_error_nifty50"], metrics["information_ratio"],
                    metrics["upside_capture"], metrics["downside_capture"], metrics["win_rate"], metrics["composite_score"]
                ))
                conn.commit()
                print(f"  ✅ {name} ({window_str}) scored. Composite Score: {metrics['composite_score']:.2f}")
            except Exception as e:
                print(f"  💥 Error scoring {name} ({window_str}): {e}")
                
    conn.close()
    return end_date_str

if __name__ == "__main__":
    run_scoring_engine_for_all()
