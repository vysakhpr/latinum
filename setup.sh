#!/bin/bash
# Latinum (Stateless Active Fund Advisor) VM Setup Automation
set -e

# Verification of command line arguments
if [ "$#" -ne 2 ]; then
    echo "Error: Missing arguments."
    echo "Usage: $0 <TELEGRAM_BOT_TOKEN> <TELEGRAM_CHAT_ID>"
    exit 1
fi

BOT_TOKEN=$1
CHAT_ID=$2

echo "🚀 Starting Latinum Server Configuration..."

# 1. Clone repository
if [ ! -d "/opt/mutual-fund-screener/.git" ]; then
    echo "📥 Cloning Latinum codebase..."
    git clone https://github.com/vysakhpr/latinum.git /opt/mutual-fund-screener
else
    echo "🔄 Codebase already exists."
fi

cd /opt/mutual-fund-screener

# 2. Setup Virtual Environment
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
fi

echo "🔌 Installing Python dependencies..."
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

# 3. Create .env Configuration
echo "⚙️ Writing environment variables..."
cat <<EOF > .env
TELEGRAM_BOT_TOKEN="$BOT_TOKEN"
TELEGRAM_CHAT_ID="$CHAT_ID"
EOF

# 4. Create Systemd Service File
echo "🔧 Registering systemd bot service daemon..."
sudo tee /etc/systemd/system/fund-advisor-bot.service > /dev/null <<EOF
[Unit]
Description=Stateless Active Fund Advisor Telegram Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/mutual-fund-screener
ExecStart=/opt/mutual-fund-screener/venv/bin/python telegram_bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# 5. Start and Enable service
echo "⚡ Starting background service..."
sudo systemctl daemon-reload
sudo systemctl enable fund-advisor-bot.service
sudo systemctl start fund-advisor-bot.service

# 6. Configure Cron Jobs (Daily fetch at 9:00 PM IST / Weekly report Friday 10:00 PM IST)
echo "⏰ Installing cron jobs..."

# Install cron package if it's missing (common in minimal cloud images)
if ! command -v crontab &> /dev/null; then
    echo "📦 cron utility not found. Installing cron..."
    sudo apt-get update && sudo apt-get install -y cron
    sudo systemctl enable cron
    sudo systemctl start cron
fi

# Extract existing cron jobs, filter out any previous latinum entries, and append the new ones
(crontab -l 2>/dev/null | grep -v "daily_fetch" | grep -v "weekly_advisor" || true; \
 echo "30 15 * * * cd /opt/mutual-fund-screener && /opt/mutual-fund-screener/venv/bin/python daily_fetch.py >> /var/log/fund-advisor-fetch.log 2>&1"; \
 echo "30 16 * * 5 cd /opt/mutual-fund-screener && /opt/mutual-fund-screener/venv/bin/python weekly_advisor.py >> /var/log/fund-advisor-weekly.log 2>&1") | crontab -

echo "✅ Latinum installation completed successfully!"
echo "-----------------------------------------------"
sudo systemctl status fund-advisor-bot.service --no-pager
