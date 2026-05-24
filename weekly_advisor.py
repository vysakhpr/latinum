import sqlite3
import os
import asyncio
import sys
from datetime import datetime, timedelta
import pandas as pd
from dotenv import load_dotenv
from telegram import Bot

# Load environment variables from .env file
load_dotenv()

DB_NAME = "active_fund_advisor.db"

# Import scoring functions dynamically or directly from scoring_engine
import scoring_engine

async def send_telegram(messages):
    """Sends a list of text messages to the private Telegram chat (100% Free)."""
    if isinstance(messages, str):
        messages = [messages]
        
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    if not token or not chat_id:
        print("\n[Warning] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not configured in .env file.")
        print("Printing report to console instead:\n")
        for text in messages:
            print(text)
            print("-" * 40)
        return
        
    try:
        bot = Bot(token=token)
        async with bot:
            for text in messages:
                await bot.send_message(chat_id=chat_id, text=text, parse_mode='HTML')
        print("\n✅ Report successfully pushed to Telegram.")
    except Exception as e:
        print(f"\n💥 Error pushing to Telegram: {e}")
        print("Printing report to console:\n")
        for text in messages:
            print(text)
            print("-" * 40)

def get_latest_date():
    """Gets the latest date in the database."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT MAX(date) FROM nav_history")
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None

def get_historical_date(latest_date_str, days_ago=180):
    """Gets the closest trading date in the DB around N days ago."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT date FROM nav_history
        WHERE date <= date(?, ?)
        ORDER BY date DESC LIMIT 1
    """, (latest_date_str, f"-{days_ago} days"))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None

def get_fund_name(scheme_code):
    """Gets the fund name from its scheme code."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT scheme_name FROM funds WHERE scheme_code = ?", (scheme_code,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else scheme_code

def calculate_drawdown_alert(latest_date_str):
    """
    Checks if Nifty Midcap 150 (code 147622) is in a drawdown (>5% off 52-week highs).
    Calculates the recommended deployment tranche.
    """
    conn = sqlite3.connect(DB_NAME)
    # Fetch last 365 calendar days
    df = pd.read_sql_query("""
        SELECT date, nav FROM nav_history
        WHERE scheme_code = '147622' AND date >= date(?, '-365 days')
        ORDER BY date ASC
    """, conn, params=(latest_date_str,))
    conn.close()
    
    if df.empty or len(df) < 100:
        return "⚠️ Tactical Drawdown check: Insufficient index data."
        
    max_nav = df['nav'].max()
    current_nav = df['nav'].iloc[-1]
    drawdown = (max_nav - current_nav) / max_nav
    
    report = f"📊 <b>Tactical Deployment Check (Nifty Midcap 150 TRI Proxy):</b>\n"
    report += f"  • Current NAV: {current_nav:.2f}\n"
    report += f"  • 52-Week High NAV: {max_nav:.2f}\n"
    report += f"  • Drawdown: {drawdown * 100.0:.2f}%\n"
    
    if drawdown > 0.05:
        # Calculate recommended tranche multiplier
        multiplier = 1.0 + drawdown * 10.0
        report += f"  • 🚨 <b>DRAWDOWN ACTIVE (&gt;5%)!</b>\n"
        report += f"  • <b>Action:</b> Deploy tactical tranche.\n"
        report += f"  • <b>Multiplier:</b> {multiplier:.2f}x of Standard Tranche (or {(drawdown * 100.0):.1f}% extra cash allocation).\n"
    else:
        report += f"  • ✅ Drawdown is normal (&lt;= 5%). No tactical action required. Continue standard SIP.\n"
        
    return report

def evaluate_holdings_and_warnings(latest_date_str, top_candidates, holdings_list=None):
    """
    Evaluates current holdings for:
    1. Alpha Decay (3y Jensen's Alpha & Information Ratio compared to 6 months ago)
    2. Rotation tax friction (12.5% LTCG / 20% STCG) vs candidate performance gains
    """
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    if holdings_list is None:
        cursor.execute("SELECT scheme_code, units, avg_purchase_price, purchase_date FROM holdings")
        holdings_list = cursor.fetchall()
    
    if not holdings_list:
        conn.close()
        return [], ["  • No active fund holdings configured."]
        
    detailed_fund_blocks = []
    summary_items = []
    
    # Target date 6 months ago for decay check
    hist_date_str = get_historical_date(latest_date_str, 180)
    
    for scheme_code, units, avg_price, purchase_date in holdings_list:
        fund_name = get_fund_name(scheme_code)
        
        # Get category of the fund from funds table to identify benchmarks
        cursor.execute("SELECT category FROM funds WHERE scheme_code = ?", (scheme_code,))
        cat_row = cursor.fetchone()
        fund_category = cat_row[0] if cat_row else "Unknown"
        
        # Determine plan type and classification
        name_lower = fund_name.lower()
        is_direct = "direct" in name_lower
        is_index = (fund_category in ("Benchmark", "Debt")) or ("index" in name_lower) or ("nifty" in name_lower) or ("sensex" in name_lower)
        
        if is_direct:
            if is_index:
                if fund_category == "Debt":
                    fund_type = "Direct Debt"
                else:
                    fund_type = "Direct Index"
            else:
                fund_type = "Direct Non-Index"
        else:
            fund_type = "Regular Fund"
            
        # Direct Index and Debt funds are completely skipped from active satellite management
        if fund_type in ("Direct Index", "Direct Debt"):
            continue
            
        # Get current NAV
        cursor.execute("SELECT nav FROM nav_history WHERE scheme_code = ? AND date = ?", (scheme_code, latest_date_str))
        curr_nav_row = cursor.fetchone()
        if not curr_nav_row:
            detailed_fund_blocks.append(f"\n  ❌ {fund_name}: Could not fetch current NAV.\n")
            continue
        curr_nav = curr_nav_row[0]
        
        # 1. Calculate holding value and capital gains
        curr_value = units * curr_nav
        purchase_cost = units * avg_price
        gains = curr_value - purchase_cost
        
        # Check holding period for tax rate
        p_dt = datetime.strptime(purchase_date, "%Y-%m-%d")
        l_dt = datetime.strptime(latest_date_str, "%Y-%m-%d")
        days_held = (l_dt - p_dt).days
        
        if days_held > 365:
            tax_rate = 0.125  # 12.5% LTCG
            tax_type = "LTCG"
        else:
            tax_rate = 0.20   # 20.0% STCG
            tax_type = "STCG"
            
        tax_amount = max(0.0, gains * tax_rate)
        net_value_after_sale = curr_value - tax_amount
        friction_ratio = tax_amount / curr_value if curr_value > 0 else 0.0
        
        # 2. Performance Metric Calculations & Decay Check
        # Fetch current 3y metrics
        cursor.execute("SELECT benchmark_code FROM funds WHERE scheme_code = ?", (scheme_code,))
        bench_row = cursor.fetchone()
        bench_code = bench_row[0] if (bench_row and bench_row[0]) else "120716"
        
        curr_metrics = scoring_engine.calculate_metrics_for_window(scheme_code, bench_code, latest_date_str, 3)
        
        # Fetch or compute metrics from 6 months ago
        hist_metrics = None
        if hist_date_str:
            hist_metrics = scoring_engine.calculate_metrics_for_window(scheme_code, bench_code, hist_date_str, 3)
            
        f_block = f"\n  <b>• {fund_name} ({scheme_code}):</b>\n"
        f_block += f"    - Type: {fund_type}\n"
        if fund_type == "Regular Fund":
            f_block += "    - ⚠️ <b>REGULAR PLAN WARNING:</b> Regular plans pay broker commissions that reduce returns by 0.5%–1.5% annually. <b>Recommendation:</b> Convert this fund to its Direct plan counterpart to maximize returns.\n"
        f_block += f"    - Current Value: ₹{curr_value:,.2f} | Gains: ₹{gains:,.2f} ({tax_type})\n"
        f_block += f"    - Estimated Sell Tax Friction: ₹{tax_amount:,.2f} ({friction_ratio * 100.0:.2f}% of asset value)\n"
        
        if curr_metrics:
            f_block += f"    - Current 3y Metrics: Alpha = {curr_metrics['jensen_alpha']*100:.2f}%, IR = {curr_metrics['information_ratio']:.2f}, Composite Score = {curr_metrics['composite_score']:.2f}\n"
            
            # Decay Check
            if hist_metrics:
                alpha_diff = (curr_metrics['jensen_alpha'] - hist_metrics['jensen_alpha']) * 100.0
                ir_diff = curr_metrics['information_ratio'] - hist_metrics['information_ratio']
                
                f_block += f"    - 6-Month Change: Alpha = {alpha_diff:+.2f}%, IR = {ir_diff:+.2f}\n"
                
                # Check for decay warnings
                if alpha_diff < -1.5 or ir_diff < -0.3:
                    f_block += f"    - ⚠️ <b>ALPHA DECAY DETECTED!</b> (Alpha fell by &gt;1.5% or IR by &gt;0.3 over 6 months)\n"
            else:
                f_block += "    - 6-Month Change: No historical baseline in database yet.\n"
        else:
            f_block += "    - Current 3y Metrics: Insufficient data.\n"
            
        # 3. Rotation Evaluation against top growth candidates
        if top_candidates:
            # We compare with the absolute best candidate
            best_candidate = top_candidates[0]
            cand_code = best_candidate["code"]
            cand_name = best_candidate["name"]
            cand_3y_cagr = best_candidate["cagr_3y"]
            
            if cand_code == scheme_code:
                f_block += f"    - 🎉 <b>Holding Status:</b> Already holding the top-ranked candidate. Keep holding.\n"
                summary_items.append(f"  • 🎉 <b>HOLD:</b> {fund_name} (Top-Ranked)")
                detailed_fund_blocks.append(f_block)
                continue
                
            held_3y_cagr = curr_metrics["cagr"] if curr_metrics else 0.0
            
            # Switch Projection over next 3 years
            v_stay = curr_value * ((1 + held_3y_cagr) ** 3)
            v_switch = net_value_after_sale * ((1 + cand_3y_cagr) ** 3)
            projected_gain = v_switch - v_stay
            
            f_block += f"    - 🔄 <b>Rotation Check vs Top Candidate ({cand_name}):</b>\n"
            f_block += f"      * Stay Projection (3y): ₹{v_stay:,.2f} (at {held_3y_cagr*100:.1f}% CAGR)\n"
            f_block += f"      * Switch Projection (3y): ₹{v_switch:,.2f} (at {cand_3y_cagr*100:.1f}% CAGR, net of tax friction)\n"
            
            if projected_gain > 0:
                f_block += f"      * 🚨 <b>RECOMMEND ROTATION!</b> Switching is projected to net an extra ₹{projected_gain:,.2f} over 3y (outperformance covers tax friction).\n"
                summary_items.append(f"  • 🚨 <b>ROTATE (Sell):</b> {fund_name} ➡️ {cand_name}")
            else:
                f_block += f"      * 🛡️ <b>HOLD!</b> Rotation is NOT mathematically justified. Tax friction drag (₹{tax_amount:,.2f}) exceeds projected outperformance by ₹{-projected_gain:,.2f}.\n"
                summary_items.append(f"  • 🛡️ <b>HOLD:</b> {fund_name}")
        detailed_fund_blocks.append(f_block)
                
    conn.close()
    return detailed_fund_blocks, summary_items

def generate_report(holdings_list=None):
    """Compiles the weekly advisor report and returns the message blocks.
    
    If holdings_list is provided, it evaluates those holdings in-memory.
    """
    latest_date_str = get_latest_date()
    if not latest_date_str:
        print("Error: Database has no data. Run daily_fetch.py first.")
        return []
        
    print(f"Generating Weekly Advisor Report for date: {latest_date_str}...")
    
    # 1. Run scoring engine to ensure database is up to date
    scoring_engine.run_scoring_engine_for_all(latest_date_str)
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Load all active funds and compute overall weighted score: 0.6 * score_3y + 0.4 * score_5y
    # Filter out benchmark category
    cursor.execute("""
        SELECT f.scheme_code, f.scheme_name, f.category, 
               s3.composite_score as score_3y, s3.cagr as cagr_3y, s3.jensen_alpha as alpha_3y, s3.information_ratio as ir_3y,
               s5.composite_score as score_5y, s5.cagr as cagr_5y
        FROM funds f
        JOIN scores s3 ON f.scheme_code = s3.scheme_code AND s3.rolling_window = '3y' AND s3.date = ?
        JOIN scores s5 ON f.scheme_code = s5.scheme_code AND s5.rolling_window = '5y' AND s5.date = ?
        WHERE f.category != 'Benchmark'
    """, (latest_date_str, latest_date_str))
    
    rows = cursor.fetchall()
    conn.close()
    
    candidates = []
    for code, name, category, s3, c3, a3, ir3, s5, c5 in rows:
        overall_score = 0.6 * s3 + 0.4 * s5
        candidates.append({
            "code": code,
            "name": name,
            "category": category,
            "score": overall_score,
            "score_3y": s3,
            "score_5y": s5,
            "cagr_3y": c3,
            "alpha_3y": a3,
            "ir_3y": ir3,
            "cagr_5y": c5
        })
        
    # Sort candidates by overall score descending
    candidates.sort(key=lambda x: x["score"], reverse=True)
    
    # 2. Get top 2 candidates
    top_candidates = candidates[:2]
    
    # C) Portfolio Holdings, Alpha Decay & Friction Section
    detailed_fund_blocks, summary_items = evaluate_holdings_and_warnings(latest_date_str, top_candidates, holdings_list)
    
    # Construct Report Blocks
    blocks = []
    
    # Block 1: Header + Action Summary
    b1 = f"🚀 <b>ACTIVE FUND ADVISOR WEEKLY REPORT</b> 🚀\n"
    b1 += f"📅 Date: {latest_date_str} (Friday Close)\n"
    b1 += f"=========================================\n\n"
    b1 += f"⚡️ <b>PORTFOLIO ACTION SUMMARY (TL;DR):</b>\n"
    if not summary_items or (len(summary_items) == 1 and "No active fund holdings" in summary_items[0]):
        b1 += "  • No active fund holdings to analyze.\n\n"
    else:
        b1 += "\n".join(summary_items) + "\n\n"
    b1 += f"========================================="
    blocks.append(b1)
    
    # Block 2: Top Candidates
    b2 = f"🏆 <b>Top Growth / Satellite Candidates (Alpha Hunt):</b>\n"
    for i, cand in enumerate(top_candidates, 1):
        b2 += f"<b>{i}. {cand['name']} ({cand['category']})</b>\n"
        b2 += f"  • Overall Score: {cand['score']:.2f} (3y Score: {cand['score_3y']:.2f} | 5y Score: {cand['score_5y']:.2f})\n"
        b2 += f"  • Returns: 3y CAGR = {cand['cagr_3y']*100:.2f}% | 5y CAGR = {cand['cagr_5y']*100:.2f}%\n"
        b2 += f"  • Risk Metrics (3y): Jensen's Alpha = {cand['alpha_3y']*100:.2f}%, IR = {cand['ir_3y']:.2f}\n\n"
    b2 += f"========================================="
    blocks.append(b2)
    
    # Block 3: Tactical Drawdown
    drawdown_alert = calculate_drawdown_alert(latest_date_str)
    b3 = drawdown_alert + "\n" + f"========================================="
    blocks.append(b3)
    
    # Block 4: Detailed holdings section header + funds
    b4 = "💼 <b>Current Portfolio Analysis & Rotation Alerts:</b>\n"
    if not detailed_fund_blocks:
        b4 += "  • No active fund holdings configured in the database.\n"
        blocks.append(b4)
    else:
        # Group detailed fund blocks so they stay within character limits (max 4000)
        curr_holding_msg = b4
        for f_block in detailed_fund_blocks:
            if len(curr_holding_msg) + len(f_block) + 1 > 4000:
                blocks.append(curr_holding_msg)
                curr_holding_msg = "💼 <b>Current Portfolio Analysis (Continued):</b>\n" + f_block
            else:
                curr_holding_msg += f_block
        if curr_holding_msg:
            blocks.append(curr_holding_msg)
            
    return blocks

def suggest_lumpsum_allocation(amount_inr):
    """Suggests a diversified lumpsum allocation of INR among the top-rated active funds."""
    if amount_inr < 500:
        return "❌ <b>Error:</b> The minimum lump sum investment amount for Indian Mutual Funds is <b>₹500</b>. Please specify an amount of ₹500 or more."

    latest_date_str = get_latest_date()
    if not latest_date_str:
        return "❌ Error: Database has no data. Please run daily_fetch.py first."
        
    # Run scoring engine to ensure database is up to date for this date
    scoring_engine.run_scoring_engine_for_all(latest_date_str)
        
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Load all candidates with their overall scores
    cursor.execute("""
        SELECT f.scheme_code, f.scheme_name, f.category, 
               s3.composite_score as score_3y, s3.cagr as cagr_3y,
               s5.composite_score as score_5y, s5.cagr as cagr_5y
        FROM funds f
        JOIN scores s3 ON f.scheme_code = s3.scheme_code AND s3.rolling_window = '3y' AND s3.date = ?
        JOIN scores s5 ON f.scheme_code = s5.scheme_code AND s5.rolling_window = '5y' AND s5.date = ?
        WHERE f.category IN ('Flexi-Cap', 'Mid-Cap', 'Small-Cap')
    """, (latest_date_str, latest_date_str))
    
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        return "❌ Error: No scored Flexi-Cap, Mid-Cap, or Small-Cap candidates found."
        
    # Group and rank by category
    candidates_by_cat = {"Flexi-Cap": [], "Mid-Cap": [], "Small-Cap": []}
    for code, name, category, s3, c3, s5, c5 in rows:
        overall_score = 0.6 * s3 + 0.4 * s5
        candidates_by_cat[category].append({
            "code": code,
            "name": name,
            "score": overall_score,
            "cagr_3y": c3
        })
        
    # Find the top candidate in each category
    top_by_cat = {}
    for cat in ["Flexi-Cap", "Mid-Cap", "Small-Cap"]:
        if candidates_by_cat[cat]:
            candidates_by_cat[cat].sort(key=lambda x: x["score"], reverse=True)
            top_by_cat[cat] = candidates_by_cat[cat][0]
            
    # Check if we have at least one top candidate
    if not top_by_cat:
        return "❌ Error: No candidates available to generate allocation."
        
    # Get Midcap 150 Drawdown
    drawdown_pct = 0.0
    conn = sqlite3.connect(DB_NAME)
    df_index = pd.read_sql_query("""
        SELECT nav FROM nav_history
        WHERE scheme_code = '147622' AND date >= date(?, '-365 days')
        ORDER BY date ASC
    """, conn, params=(latest_date_str,))
    conn.close()
    
    if not df_index.empty:
        max_nav = df_index['nav'].max()
        current_nav = df_index['nav'].iloc[-1]
        drawdown_pct = (max_nav - current_nav) / max_nav
        
    # Build suggestion message
    blocks = []
    blocks.append(f"💰 <b>Lump-Sum Investment Allocation Plan</b>\n"
                  f"💵 Total Amount: <b>₹{amount_inr:,.2f}</b>\n"
                  f"📅 Pricing Date: {latest_date_str}\n"
                  f"=========================================\n\n")
                  
    # Determine strategy based on amount
    if amount_inr < 10000:
        # For small amounts, put 100% in top overall candidate to minimize transaction friction
        all_tops = [top_by_cat[cat] for cat in top_by_cat]
        all_tops.sort(key=lambda x: x["score"], reverse=True)
        best_fund = all_tops[0]
        
        blocks.append(
            f"🎯 <b>Recommendation: Single Fund Focus (Amount &lt; ₹10,000)</b>\n"
            f"To avoid over-diversification and transaction friction, deploy 100% of the funds into the single highest-ranking candidate:\n\n"
            f"• <b>{best_fund['name']}</b>\n"
            f"  - Allocation: 100% (<b>₹{amount_inr:,.2f}</b>)\n"
            f"  - Category Score: {best_fund['score']:.2f} | 3y CAGR: {best_fund['cagr_3y'] * 100.0:.2f}%\n\n"
        )
    else:
        # Standard diversified split: 40% Flexi, 30% Mid, 30% Small
        alloc_weights = {"Flexi-Cap": 0.40, "Mid-Cap": 0.30, "Small-Cap": 0.30}
        
        blocks.append("🎯 <b>Recommended Diversified Allocation (Satellite Growth):</b>\n\n")
        
        for cat in ["Flexi-Cap", "Mid-Cap", "Small-Cap"]:
            if cat in top_by_cat:
                weight = alloc_weights[cat]
                fund_amt = amount_inr * weight
                fund = top_by_cat[cat]
                
                blocks.append(
                    f"• <b>{cat}</b> ({weight * 100.0:.0f}%): <b>₹{fund_amt:,.2f}</b>\n"
                    f"  👉 Fund: {fund['name']}\n"
                    f"  • Score: {fund['score']:.2f} | 3y CAGR: {fund['cagr_3y'] * 100.0:.2f}%\n\n"
                )
                
    # Market Context & Staggering Advice
    blocks.append("=========================================\n"
                  "📈 <b>Tactical Market Context:</b>\n")
    if drawdown_pct > 0.05:
        blocks.append(
            f"🚨 <b>Drawdown Active ({drawdown_pct * 100.0:.2f}%):</b>\n"
            f"Nifty Midcap 150 is trading at a discount. Markets are correction-priced. "
            f"<b>Recommendation:</b> Deploy the entire lump sum immediately to capture value compounding."
        )
    else:
        blocks.append(
            f"✅ <b>Drawdown is Normal ({drawdown_pct * 100.0:.2f}%):</b>\n"
            f"Markets are trading near highs. "
            f"<b>Recommendation:</b> To minimize timing risk, stagger this lump sum in **3 to 4 weekly tranches** (e.g., via weekly SIP/STP) rather than deploying all at once."
        )
        
    return "".join(blocks)

async def fetch_holdings_from_telegram():
    """Fetches the latest file ID from database and downloads/parses the file from Telegram."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        return None
        
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # Check if bot_state table exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='bot_state'")
    if not cursor.fetchone():
        conn.close()
        return None
        
    cursor.execute("SELECT value FROM bot_state WHERE key = 'latest_file_id'")
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        return None
        
    file_id = row[0]
    try:
        bot = Bot(token=token)
        async with bot:
            telegram_file = await bot.get_file(file_id)
            file_bytes = await telegram_file.download_as_bytearray()
            import import_groww
            return import_groww.parse_groww_sheet_in_memory(file_bytes)
    except Exception as e:
        print(f"Error fetching holdings from Telegram: {e}")
        # Remove invalid reference
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM bot_state WHERE key = 'latest_file_id'")
        conn.commit()
        conn.close()
        return None

async def run_weekly_job():
    """Runs the weekly analysis job. If no Groww report is uploaded, it sends a reminder."""
    holdings_list = await fetch_holdings_from_telegram()
    
    if not holdings_list:
        reminder_msg = (
            "⚠️ <b>Weekly Active Fund Advisor Alert</b>\n\n"
            "Your weekly report could not be generated because <b>no active Groww holdings statement is available</b> on Telegram.\n\n"
            "📤 Please upload your Groww holdings statement (.xlsx) to the bot to resume automated screening."
        )
        await send_telegram(reminder_msg)
    else:
        # Run report generation in executor because it contains synchronous calculations and DB reads
        loop = asyncio.get_running_loop()
        report_blocks = await loop.run_in_executor(
            None, generate_report, holdings_list
        )
        if report_blocks:
            await send_telegram(report_blocks)

if __name__ == "__main__":
    asyncio.run(run_weekly_job())
