# Active Fund Advisor Pipeline

A lightweight, automated quantitative mutual fund screening and portfolio analysis pipeline tailored for deployment on an OCI Free Tier instance (Ubuntu Linux). It fetches historical and daily NAVs, calculates risk-adjusted outperformance metrics (annualized CAGR, Volatility, Betas, Jensen's Alpha, Tracking Error, Information Ratio, and Capture Ratios), penalizes US Tech exposure and closet indexing, and outputs rotation recommendations factored for LTCG/STCG tax friction.

Reports are pushed directly to a private Telegram chat every Friday evening.

---

## Folder Structure

```
mutual-fund-screener/
├── active_fund_advisor.db   # Local SQLite Database (created automatically)
├── daily_fetch.py           # Daily data-ingestion script
├── scoring_engine.py        # Quantitative calculation engine
├── weekly_advisor.py        # Weekly report compiler and Telegram notifier
├── telegram_bot.py          # Interactive Telegram bot daemon (Stateless mode)
├── test_pipeline.py         # Automated pipeline validator (integration tests)
├── test_suite.py            # Unit test suite with mocks
├── import_groww.py          # Auto-importer and parser for Groww Excel statements
├── requirements.txt         # Pip dependency manifest
├── README.md                # Deployment instructions
└── .env                     # Telegram bot configuration (User created)
```

---

## Setup Instructions (OCI Free Tier)

### 1. Clone & Set Up Directory
Navigate to your target workspace on the Ubuntu instance and verify Python is installed:
```bash
sudo apt update
sudo apt install -y python3-venv python3-pip sqlite3
```

### 2. Create Virtual Environment & Install Dependencies
Run these commands to isolate dependencies:
```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Create Telegram Bot (100% Free)
1. Open Telegram, search for `@BotFather`, and start a conversation.
2. Send the `/newbot` command, follow the prompts, and copy the HTTP API **Token**.
3. Search for `@userinfobot` on Telegram and start it to get your private **Chat ID**.
4. Create a `.env` file in the root of the project:
   ```env
   TELEGRAM_BOT_TOKEN="your-telegram-bot-token-here"
   TELEGRAM_CHAT_ID="your-chat-id-here"
   ```

### 4. Initialize Database & Fetch Data
Run the daily ingestion script to fetch 10+ years of historical data for all active funds and benchmark indices. This may take 1-2 minutes on the first run:
```bash
python3 daily_fetch.py
```
This automatically initializes `active_fund_advisor.db` and downloads historical NAVs.

### 5. Run and Service Setup for Stateless Bot Daemon
The pipeline operates in **Stateless Mode** (no holdings or personal portfolio data is saved permanently on disk). The bot daemon processes uploaded spreadsheets in-memory.

To run the bot daemon interactively:
```bash
python3 telegram_bot.py
```

To run the bot daemon continuously as a systemd service in your OCI/GCP VM:
1. Create a systemd service file:
   ```bash
   sudo nano /etc/systemd/system/fund-advisor-bot.service
   ```
2. Paste the configuration (updating the paths and user name to match your system):
   ```ini
   [Unit]
   Description=Active Fund Advisor Telegram Bot Daemon
   After=network.target

   [Service]
   Type=simple
   User=ubuntu
   WorkingDirectory=/absolute/path/to/mutual-fund-screener
   ExecStart=/absolute/path/to/mutual-fund-screener/venv/bin/python telegram_bot.py
   Restart=always
   RestartSec=10

   [Install]
   WantedBy=multi-user.target
   ```
3. Start and enable the service:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable fund-advisor-bot.service
   sudo systemctl start fund-advisor-bot.service
   ```

### 6. Verify Installation
Run the unit tests to ensure that all components are functioning correctly:
```bash
python3 test_suite.py
```

### 7. Interactive Usage via Telegram
1. Upload your Groww Mutual Funds statement (`Mutual_Funds_*.xlsx`) directly into your Telegram chat with the bot.
2. The bot will automatically download the file, process it in-memory, save the temporary `file_id` reference in `bot_state` metadata, and reply with the complete weekly report immediately!
3. Send the command `/analyze` or `/report` at any time to run the analysis against your last uploaded statement.
4. **Stateless Privacy Guarantee**: If you delete the chat or delete the file from the chat, Telegram's servers expire the reference. The bot will automatically detect this next time you request analysis, clear the reference from `bot_state`, and prompt you to upload a new statement.

---

## Automation Scheduling (Cron)

Linux `cron` will automate the daily NAV updates and the weekly Friday report. 

Open the cron editor:
```bash
crontab -e
```

Paste the appropriate entries below.

### Option A: OCI Server in Indian Standard Time (IST)
If your server's timezone is set to Asia/Kolkata:
```cron
# 1. Daily NAV fetch: Run everyday at 9:00 PM IST (after AMFI publishes daily NAVs)
0 21 * * * /absolute/path/to/mutual-fund-screener/venv/bin/python /absolute/path/to/mutual-fund-screener/daily_fetch.py >> /absolute/path/to/mutual-fund-screener/daily_fetch.log 2>&1

# 2. Weekly Report: Run every Friday at 6:00 PM IST
0 18 * * 5 /absolute/path/to/mutual-fund-screener/venv/bin/python /absolute/path/to/mutual-fund-screener/weekly_advisor.py >> /absolute/path/to/mutual-fund-screener/weekly_advisor.log 2>&1
```

### Option B: OCI Server in Coordinated Universal Time (UTC)
*Highly Recommended: OCI VMs default to UTC.* 
* 9:00 PM IST is **3:30 PM UTC** (15:30)
* 6:00 PM IST is **12:30 PM UTC** (12:30)
```cron
# 1. Daily NAV fetch: Run everyday at 3:30 PM UTC (equivalent to 9:00 PM IST)
30 15 * * * /absolute/path/to/mutual-fund-screener/venv/bin/python /absolute/path/to/mutual-fund-screener/daily_fetch.py >> /absolute/path/to/mutual-fund-screener/daily_fetch.log 2>&1

# 2. Weekly Report: Run every Friday at 12:30 PM UTC (equivalent to 6:00 PM IST)
30 12 * * 5 /absolute/path/to/mutual-fund-screener/venv/bin/python /absolute/path/to/mutual-fund-screener/weekly_advisor.py >> /absolute/path/to/mutual-fund-screener/weekly_advisor.log 2>&1
```

*Replace `/absolute/path/to/mutual-fund-screener` with the actual path output from `pwd` inside your project directory.*

---

## Interactive Lumpsum Allocation Engine

The pipeline includes an intelligent lump-sum investment suggestion engine that helps allocate cash based on current market valuations and real-time fund performance scores:

*   **Commands**: Send `/lumpsum [amount]` or `/invest [amount]` (e.g., `/lumpsum 50000`).
*   **Small Investments (< ₹10,000)**: Recommends 100% allocation to the single highest-rated fund overall to minimize transaction complexity.
*   **Large Investments (>= ₹10,000)**: Allocates capital across top-performing growth categories using a core-satellite model:
    *   **Flexi-Cap (Core)**: 40%
    *   **Mid-Cap (Satellite)**: 30%
    *   **Small-Cap (Satellite)**: 30%
*   **Tactical Timing Context**: Checks the current drawdown of the **Nifty Midcap 150 Index** over the last 365 days:
    *   **Drawdown > 5% (Correction)**: Advises deploying the entire lump sum immediately to leverage discounted pricing.
    *   **Drawdown <= 5% (Market Highs)**: Advises staggering the capital over **3 to 4 weekly tranches** to mitigate market timing risk.

---

## Secure Collaboration & Deployment (Two-Repository Model)

If you are sharing this project with collaborators who have full Write/Admin access to the repository, do not add deployment workflows or credentials directly here. Instead, utilize the **Two-Repository Model**:

1.  **Shared Repository (This Codebase)**: Push this code for collaboration. Keep it free of deployment actions, API keys, and server details.
2.  **Private Deploy Repository**: Create a separate private repository under your personal account. Add your GCP SSH keys and VM IPs to its secrets, and configure a deployment workflow that pulls code updates from the shared repo and restarts your VM daemon.

For detailed VM provisioning, setup, and repository sync steps, see the [GCP Deployment Guide](file:///Users/vysakhpr/.gemini/antigravity/brain/26a2f801-8d1e-4724-831e-cce95ff71d81/deployment_guide.md) and the [Secure Collaboration Guide](file:///Users/vysakhpr/.gemini/antigravity/brain/26a2f801-8d1e-4724-831e-cce95ff71d81/github_sharing_guide.md).

---

## Scoring Logic Reference
The composite score is compiled over 3-year and 5-year rolling windows with the following weights:
*   **Jensen's Alpha** (30% weight): Excess risk-adjusted annualized returns relative to benchmark index (6% risk-free baseline).
*   **Information Ratio** (20% weight): Average active return over category benchmark divided by active tracking error.
*   **Divergence Score / Nifty 50 Tracking Error** (15% weight): Penalizes closet indexers with high active drag. Tracking Error against Nifty 50 < 4.0% triggers a steep `-30` points penalty.
*   **Upside Capture Ratio** (20% weight): Measures cumulative returns on benchmark up days. Rewards funds that capture >100% of benchmark rallies.
*   **3y Rolling Return Win Rate** (15% weight): The frequency (calculated daily over 5 years) that the fund outpaced the category benchmark over rolling 3-year periods.
*   **Tech/IT Overlap Filter** (Constraint): Statistical beta against the Nifty IT index fund proxy is calculated. If $\beta_{IT} > 0.4$, a massive **-500 points** penalty is applied, removing it from recommendations.

Overall ranking is weighted: `0.6 * score_3y + 0.4 * score_5y`.
