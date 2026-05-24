import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import sqlite3
import os
import asyncio
from datetime import datetime
import pandas as pd
import numpy as np

# Set test database name before importing target modules
TEST_DB_NAME = "test_active_fund_advisor.db"

import daily_fetch
import scoring_engine
import weekly_advisor
import import_groww

# Point all modules to the test database
daily_fetch.DB_NAME = TEST_DB_NAME
scoring_engine.DB_NAME = TEST_DB_NAME
weekly_advisor.DB_NAME = TEST_DB_NAME
import_groww.DB_NAME = TEST_DB_NAME

class AsyncContextMock(MagicMock):
    """Helper to mock async context managers like telegram.Bot in v20+"""
    async def __aenter__(self):
        return self
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

class TestDailyFetch(unittest.TestCase):
    def setUp(self):
        # Initialize test database
        self.conn = daily_fetch.init_db()
        daily_fetch.seed_funds(self.conn)
        
    def tearDown(self):
        self.conn.close()
        if os.path.exists(TEST_DB_NAME):
            os.remove(TEST_DB_NAME)
            
    def test_parse_date(self):
        # Test standard format
        self.assertEqual(daily_fetch.parse_date("22-05-2026"), "2026-05-22")
        self.assertEqual(daily_fetch.parse_date("02-01-2013"), "2013-01-02")
        
        # Test alphabetical month format
        self.assertEqual(daily_fetch.parse_date("22-May-2026"), "2026-05-22")
        self.assertEqual(daily_fetch.parse_date("02-Jan-2013"), "2013-01-02")
        
        # Test invalid date
        self.assertIsNone(daily_fetch.parse_date("invalid-date"))
        
    def test_init_db_and_seed(self):
        cursor = self.conn.cursor()
        # Verify table existence
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        self.assertIn("funds", tables)
        self.assertIn("nav_history", tables)
        self.assertIn("holdings", tables)
        self.assertIn("scores", tables)
        
        # Verify seed records
        cursor.execute("SELECT COUNT(*) FROM funds")
        self.assertEqual(cursor.fetchone()[0], 40)
        
    @patch('daily_fetch.Mftool')
    def test_fetch_and_store_nav_incremental(self, mock_mftool_cls):
        mock_mftool = mock_mftool_cls.return_value
        
        # Mock historical NAV payload
        mock_mftool.get_scheme_historical_nav.return_value = {
            'data': [
                {'date': '24-05-2026', 'nav': '102.5'},
                {'date': '23-05-2026', 'nav': '101.0'},
                {'date': '22-05-2026', 'nav': '100.0'}
            ]
        }
        
        original_config = daily_fetch.FUND_CONFIG
        daily_fetch.FUND_CONFIG = [{"code": "120843", "name": "quant Flexi Cap Fund", "category": "Flexi-Cap", "benchmark_code": "120716"}]
        
        try:
            daily_fetch.fetch_and_store_nav(self.conn)
            
            cursor = self.conn.cursor()
            cursor.execute("SELECT date, nav FROM nav_history WHERE scheme_code = '120843' ORDER BY date ASC")
            records = cursor.fetchall()
            self.assertEqual(len(records), 3)
            self.assertEqual(records[0], ("2026-05-22", 100.0))
            
            # Test incremental logic
            mock_mftool.get_scheme_historical_nav.return_value = {
                'data': [
                    {'date': '25-05-2026', 'nav': '103.0'}, # New
                    {'date': '24-05-2026', 'nav': '102.5'},
                    {'date': '23-05-2026', 'nav': '101.0'},
                    {'date': '22-05-2026', 'nav': '100.0'}
                ]
            }
            
            daily_fetch.fetch_and_store_nav(self.conn)
            
            # Ensure only 1 new record was inserted
            cursor.execute("SELECT COUNT(*) FROM nav_history WHERE scheme_code = '120843'")
            self.assertEqual(cursor.fetchone()[0], 4)
            
        finally:
            daily_fetch.FUND_CONFIG = original_config


class TestScoringEngineAndAdvisor(unittest.TestCase):
    def setUp(self):
        # Initialize test database
        self.conn = daily_fetch.init_db()
        daily_fetch.seed_funds(self.conn)
        
        # Populate database with realistic simulated NAV data with daily random noise.
        # This avoids division by zero or infinite IR / high beta artifacts.
        dates = pd.date_range(start="2021-01-01", end="2026-05-22", freq="B")
        N = len(dates)
        
        # Seed generator for reproducibility
        np.random.seed(42)
        
        # Generate returns
        ret_n50 = np.random.normal(0.0004, 0.01, N)          # Nifty 50: mean 0.04% daily, vol 1%
        ret_it = np.random.normal(0.0003, 0.015, N)          # Nifty IT: mean 0.03% daily, vol 1.5%
        
        # Fund 122639 (Parag Parikh): Beta~0.9 with N50, Beta~0.2 with IT, Alpha~0.02%
        ret_fund1 = 0.9 * ret_n50 + 0.2 * ret_it + np.random.normal(0.0002, 0.005, N)
        
        # Fund 120843 (Quant Flexi): grows slightly faster
        ret_fund2 = 0.95 * ret_n50 + 0.2 * ret_it + np.random.normal(0.0004, 0.005, N)
        
        # Fund 127042 (Motilal Midcap): high IT overlap (Beta > 0.4 against IT index)
        ret_midcap = 0.3 * ret_n50 + 0.7 * ret_it + np.random.normal(0.0001, 0.005, N)
        
        # Midcap category benchmark (147622)
        ret_midcap_bench = 0.8 * ret_n50 + 0.2 * ret_it + np.random.normal(0.0001, 0.008, N)
        
        # Cumulate returns to generate NAV series
        nav_n50 = 10.0 * np.cumprod(1.0 + ret_n50)
        nav_it = 10.0 * np.cumprod(1.0 + ret_it)
        nav_fund1 = 10.0 * np.cumprod(1.0 + ret_fund1)
        nav_fund2 = 10.0 * np.cumprod(1.0 + ret_fund2)
        nav_midcap = 10.0 * np.cumprod(1.0 + ret_midcap)
        nav_midcap_bench = 10.0 * np.cumprod(1.0 + ret_midcap_bench)
        
        cursor = self.conn.cursor()
        to_insert = []
        for i, dt in enumerate(dates):
            date_str = dt.strftime("%Y-%m-%d")
            to_insert.append(("122639", date_str, float(nav_fund1[i])))
            to_insert.append(("120716", date_str, float(nav_n50[i])))
            to_insert.append(("150468", date_str, float(nav_it[i])))
            to_insert.append(("120843", date_str, float(nav_fund2[i])))
            to_insert.append(("127042", date_str, float(nav_midcap[i])))
            to_insert.append(("147622", date_str, float(nav_midcap_bench[i])))
            
        cursor.executemany("INSERT INTO nav_history (scheme_code, date, nav) VALUES (?, ?, ?)", to_insert)
        self.conn.commit()
        
    def tearDown(self):
        self.conn.close()
        if os.path.exists(TEST_DB_NAME):
            os.remove(TEST_DB_NAME)
            
    def test_load_data(self):
        df = scoring_engine.load_data("122639", "120716", "2026-05-22", 3)
        self.assertIsNotNone(df)
        self.assertIn("fund", df.columns)
        self.assertIn("bench", df.columns)
        self.assertIn("nifty50", df.columns)
        self.assertTrue(len(df) > 700)
        
    def test_calculate_beta_it(self):
        beta_it = scoring_engine.calculate_beta_it("122639", "2026-05-22", 3)
        self.assertTrue(isinstance(beta_it, float))
        self.assertTrue(0.0 < beta_it < 0.4)
        
    def test_calculate_metrics_for_window_and_it_beta_penalty(self):
        # 1. Test standard fund (no penalty)
        metrics = scoring_engine.calculate_metrics_for_window("122639", "120716", "2026-05-22", 3)
        self.assertIsNotNone(metrics)
        self.assertTrue(metrics["cagr"] > 0)
        self.assertTrue(metrics["jensen_alpha"] > -0.05)
        self.assertTrue(metrics["beta_it"] < 0.4)
        self.assertTrue(metrics["composite_score"] > 0)
        
        # 2. Test penalized fund: Motilal Oswal Midcap (127042) which tracks IT (Beta ~ 0.7)
        penalized_metrics = scoring_engine.calculate_metrics_for_window("127042", "147622", "2026-05-22", 3)
        self.assertIsNotNone(penalized_metrics)
        self.assertTrue(penalized_metrics["beta_it"] > 0.4)
        # Score must be deeply negative due to the -500 penalty
        self.assertTrue(penalized_metrics["composite_score"] < -100.0)
        
    def test_scoring_engine_for_all(self):
        scoring_engine.run_scoring_engine_for_all("2026-05-22")
        
        # Verify rows were populated in database (3 active funds with data * 2 windows)
        conn = sqlite3.connect(TEST_DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM scores WHERE date = '2026-05-22'")
        count = cursor.fetchone()[0]
        conn.close()
        self.assertEqual(count, 6)
        
    def test_calculate_drawdown_alert(self):
        cursor = self.conn.cursor()
        
        # Ensure final NAV is equal to peak to test normal check (Drawdown <= 5%)
        cursor.execute("SELECT MAX(nav) FROM nav_history WHERE scheme_code = '147622'")
        max_nav = cursor.fetchone()[0]
        
        cursor.execute("UPDATE nav_history SET nav = ? WHERE scheme_code = '147622' AND date = '2026-05-22'", (max_nav,))
        self.conn.commit()
        
        # Case 1: Drawdown is normal
        report_normal = weekly_advisor.calculate_drawdown_alert("2026-05-22")
        self.assertIn("Drawdown is normal", report_normal)
        
        # Case 2: Drop current NAV to trigger drawdown > 5% (to exactly 10% drawdown)
        cursor.execute("UPDATE nav_history SET nav = ? WHERE scheme_code = '147622' AND date = '2026-05-22'", (max_nav * 0.90,))
        self.conn.commit()
        
        report_drawdown = weekly_advisor.calculate_drawdown_alert("2026-05-22")
        self.assertIn("DRAWDOWN ACTIVE", report_drawdown)
        self.assertIn("Multiplier", report_drawdown)
        self.assertIn("2.00x", report_drawdown)
        
    def test_evaluate_holdings_and_warnings(self):
        cursor = self.conn.cursor()
        # Direct Non-Index Fund
        cursor.execute("""
            INSERT INTO holdings (scheme_code, units, avg_purchase_price, purchase_date)
            VALUES ('122639', 1000.0, 10.0, '2024-01-01')
        """)
        # Direct Index Fund (should be skipped completely)
        cursor.execute("""
            INSERT INTO holdings (scheme_code, units, avg_purchase_price, purchase_date)
            VALUES ('120716', 500.0, 150.0, '2024-01-01')
        """)
        self.conn.commit()
        
        top_candidates = [
            {"code": "120843", "name": "quant Flexi Cap Fund", "score": 105.0, "cagr_3y": 0.16, "score_3y": 105.0, "score_5y": 105.0, "cagr_5y": 0.16, "alpha_3y": 0.05, "ir_3y": 1.2}
        ]
        
        detailed_blocks, summary = weekly_advisor.evaluate_holdings_and_warnings("2026-05-22", top_candidates)
        report = "\n".join(detailed_blocks)
        self.assertIn("Type: Direct Non-Index", report)
        self.assertIn("Estimated Sell Tax Friction", report)
        self.assertIn("Rotation Check", report)
        self.assertIn("Stay Projection", report)
        self.assertIn("Switch Projection", report)
        
        # Check that the index fund is not mentioned anywhere in detailed blocks
        self.assertNotIn("UTI Nifty 50 Index Fund", report)

    @patch('weekly_advisor.Bot')
    def test_send_telegram_mock(self, mock_bot_cls):
        # Create an async context manager mock
        mock_bot = AsyncContextMock()
        mock_bot.send_message = AsyncMock()
        mock_bot_cls.return_value = mock_bot
        
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "mock_token", "TELEGRAM_CHAT_ID": "mock_chat_id"}):
            asyncio.run(weekly_advisor.send_telegram("Test Message"))
            # Assert send_message was awaited on the bot
            mock_bot.send_message.assert_called_once_with(chat_id="mock_chat_id", text="Test Message", parse_mode='HTML')

    def test_suggest_lumpsum_allocation(self):
        with patch('weekly_advisor.DB_NAME', TEST_DB_NAME):
            latest_date = weekly_advisor.get_latest_date()
            scoring_engine.run_scoring_engine_for_all(latest_date)
            
            res_small = weekly_advisor.suggest_lumpsum_allocation(5000)
            self.assertIn("Lump-Sum Investment Allocation Plan", res_small)
            self.assertIn("Single Fund Focus", res_small)
            
            res_large = weekly_advisor.suggest_lumpsum_allocation(50000)
            self.assertIn("Lump-Sum Investment Allocation Plan", res_large)
            self.assertIn("Recommended Diversified Allocation", res_large)
            self.assertIn("<b>Flexi-Cap</b> (40%): <b>₹20,000.00</b>", res_large)
            self.assertIn("<b>Mid-Cap</b> (30%): <b>₹15,000.00</b>", res_large)
            
            # Test below minimum limit (< 500)
            res_invalid = weekly_advisor.suggest_lumpsum_allocation(100)
            self.assertIn("minimum lump sum investment amount", res_invalid)

class TestImportGroww(unittest.TestCase):
    def setUp(self):
        self.conn = daily_fetch.init_db()
        daily_fetch.seed_funds(self.conn)
        
    def tearDown(self):
        self.conn.close()
        if os.path.exists(TEST_DB_NAME):
            os.remove(TEST_DB_NAME)
            
    def test_clean_name(self):
        self.assertEqual(import_groww.clean_name("quant Flexi Cap Fund - Growth Option-Direct Plan"), {'quant', 'flexi', 'cap'})
        
    def test_find_best_match(self):
        db_funds = [
            ('120716', 'UTI Nifty 50 Index Fund - Growth Option- Direct'),
            ('120828', 'quant Small Cap Fund - Growth Option - Direct Plan'),
            ('118778', 'Nippon India Small Cap Fund - Direct Plan Growth Plan - Growth Option')
        ]
        
        # Exact direct match
        code, score = import_groww.find_best_match("Quant Small Cap Fund Growth", db_funds)
        self.assertEqual(code, '120828')
        
        # Conflict (Mid cap should not match Small cap)
        code, score = import_groww.find_best_match("Nippon India Growth Mid Cap Fund Growth", db_funds)
        self.assertIsNone(code)
        
        # Conflict (Nifty Next 50 should not match Nifty 50)
        code, score = import_groww.find_best_match("UTI Nifty Next 50 Index Fund Direct Growth", db_funds)
        self.assertIsNone(code)
        
    @patch('import_groww.get_latest_groww_file')
    @patch('import_groww.pd.read_excel')
    def test_import_portfolio(self, mock_read_excel, mock_get_latest):
        mock_get_latest.return_value = "/mock/downloads/Mutual_Funds_123.xlsx"
        
        # Mock DataFrame representation of Groww sheet with exactly 11 columns
        data = {
            "Unnamed: 0": [
                None, "Personal Details", "Name", None, None, "HOLDING SUMMARY", None, 
                "Total Investments", "100000", None, None, "HOLDINGS AS ON 2026-05-24", None, None,
                "Scheme Name", # Header
                "Quant Small Cap Fund Growth",
                "Nippon India Growth Mid Cap Fund Growth",
                "UTI Nifty 50 Index Fund Direct Growth"
            ],
            "Unnamed: 1": [
                None, None, "Vysakh", None, None, None, None,
                "Current Portfolio Value", "110000", None, None, None, None, None,
                "AMC", # Header
                "Quant Mutual Fund",
                "Nippon India Mutual Fund",
                "UTI Mutual Fund"
            ],
            "Col2": [None]*18,
            "Col3": [None]*18,
            "Col4": [None]*18,
            "Col5": [None]*18,
            "Unnamed: 6": [
                None, None, None, None, None, None, None,
                None, None, None, None, None, None, None,
                "Units", # Header
                "100.0",
                "10.0",
                "50.0"
            ],
            "Unnamed: 7": [
                None, None, None, None, None, None, None,
                None, None, None, None, None, None, None,
                "Invested Value", # Header
                "25000.0",
                "40000.0",
                "8000.0"
            ],
            "Col8": [None]*18,
            "Col9": [None]*18,
            "Col10": [None]*18
        }
        
        df = pd.DataFrame(data)
        mock_read_excel.return_value = df
        
        import_groww.import_portfolio()
        
        # Query holdings table in database
        conn = sqlite3.connect(TEST_DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT scheme_code, units, avg_purchase_price FROM holdings")
        holdings = cursor.fetchall()
        conn.close()
        
        # All 3 (Quant Small Cap, Nippon Mid Cap, and UTI Nifty 50) should match and import successfully.
        self.assertEqual(len(holdings), 3)
        holdings_dict = {h[0]: (h[1], h[2]) for h in holdings}
        self.assertIn("120828", holdings_dict)
        self.assertIn("120716", holdings_dict)
        self.assertIn("118668", holdings_dict)
        self.assertEqual(holdings_dict["120828"], (100.0, 250.0))
        self.assertEqual(holdings_dict["120716"], (50.0, 160.0))
        self.assertEqual(holdings_dict["118668"], (10.0, 4000.0))

    @patch('import_groww.pd.read_excel')
    def test_parse_groww_sheet_in_memory(self, mock_read_excel):
        data = {
            "Unnamed: 0": [
                None, "Personal Details", "Name", None, None, "HOLDING SUMMARY", None, 
                "Total Investments", "100000", None, None, "HOLDINGS AS ON 2026-05-24", None, None,
                "Scheme Name", # Header
                "Quant Small Cap Fund Growth",
                "Nippon India Growth Mid Cap Fund Growth",
                "UTI Nifty 50 Index Fund Direct Growth"
            ],
            "Unnamed: 1": [
                None, None, "Vysakh", None, None, None, None,
                "Current Portfolio Value", "110000", None, None, None, None, None,
                "AMC", # Header
                "Quant Mutual Fund",
                "Nippon India Mutual Fund",
                "UTI Mutual Fund"
            ],
            "Col2": [None]*18,
            "Col3": [None]*18,
            "Col4": [None]*18,
            "Col5": [None]*18,
            "Unnamed: 6": [
                None, None, None, None, None, None, None,
                None, None, None, None, None, None, None,
                "Units", # Header
                "100.0",
                "10.0",
                "50.0"
            ],
            "Unnamed: 7": [
                None, None, None, None, None, None, None,
                None, None, None, None, None, None, None,
                "Invested Value", # Header
                "25000.0",
                "40000.0",
                "8000.0"
            ],
            "Col8": [None]*18,
            "Col9": [None]*18,
            "Col10": [None]*18
        }
        df = pd.DataFrame(data)
        mock_read_excel.return_value = df
        
        with patch('import_groww.DB_NAME', TEST_DB_NAME):
            result = import_groww.parse_groww_sheet_in_memory(b"dummy_bytes")
            
        self.assertEqual(len(result), 3)
        holdings_dict = {item[0]: (item[1], item[2]) for item in result}
        self.assertIn("120828", holdings_dict)
        self.assertIn("120716", holdings_dict)
        self.assertIn("118668", holdings_dict)
        self.assertEqual(holdings_dict["120828"], (100.0, 250.0))
        self.assertEqual(holdings_dict["120716"], (50.0, 160.0))
        self.assertEqual(holdings_dict["118668"], (10.0, 4000.0))

if __name__ == "__main__":
    unittest.main()
