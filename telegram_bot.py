import os
import sqlite3
import asyncio
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv

import import_groww
import weekly_advisor
import daily_fetch

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

DB_NAME = "active_fund_advisor.db"

if ALLOWED_CHAT_ID:
    try:
        ALLOWED_CHAT_ID = int(ALLOWED_CHAT_ID)
    except ValueError:
        logger.error("TELEGRAM_CHAT_ID in .env must be an integer.")

def get_latest_file_id():
    """Queries the database for the latest file_id metadata."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM bot_state WHERE key = 'latest_file_id'")
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

def set_latest_file_id(file_id):
    """Saves the latest file_id metadata to the database."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO bot_state (key, value) VALUES ('latest_file_id', ?)",
        (file_id,)
    )
    conn.commit()
    conn.close()

def delete_latest_file_id():
    """Deletes the latest file_id from database bot_state."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM bot_state WHERE key = 'latest_file_id'")
    conn.commit()
    conn.close()

def is_authorized(update: Update) -> bool:
    """Verifies that the message comes from the owner's chat ID."""
    if not ALLOWED_CHAT_ID:
        return True # If not set, allow all (fallback)
    return update.effective_chat.id == ALLOWED_CHAT_ID

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends a welcome message and requests a statement."""
    if not is_authorized(update):
        return
        
    welcome_text = (
        "👋 <b>Welcome to the Active Fund Advisor Bot!</b>\n\n"
        "This bot operates in <b>Stateless Mode</b>: no holding values, units, or personal financial details are stored on this server.\n\n"
        "📤 <b>To get started:</b>\n"
        "Send me your latest Groww Mutual Funds holdings statement (as an Excel <code>.xlsx</code> document).\n\n"
        "💡 <b>Available Commands:</b>\n"
        "• /analyze or /report - Run screening using your previously uploaded statement.\n"
        "• /invest [amount] - Suggest investment split across top Flexi, Mid, and Small Cap candidates.\n"
        "• /status - Check database parameters and date ranges."
    )
    await update.message.reply_text(welcome_text, parse_mode="HTML")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prints the current database status."""
    if not is_authorized(update):
        return
        
    latest_date = weekly_advisor.get_latest_date()
    file_id = get_latest_file_id()
    has_file = "Yes (Referenced on Telegram)" if file_id else "No (Not uploaded yet)"
    
    status_text = (
        "📊 <b>System Status:</b>\n"
        f"• Latest AMFI Data Date: {latest_date or 'N/A'}\n"
        f"• Active Groww Statement: {has_file}\n"
        "• Data Storage Policy: <b>Stateless (No personal data stored)</b>"
    )
    await update.message.reply_text(status_text, parse_mode="HTML")

async def run_report_for_file_id(update: Update, file_id: str):
    """Downloads the statement from Telegram, parses it in-memory, and sends the advisor report."""
    try:
        await update.message.reply_text("⏳ Downloading and parsing your Groww statement in-memory...")
        
        # Download file as byte array
        telegram_file = await update.message.via_bot.get_file(file_id)
        file_bytes = await telegram_file.download_as_bytearray()
        
        # Parse in-memory
        holdings_list = import_groww.parse_groww_sheet_in_memory(file_bytes)
        
        if not holdings_list:
            await update.message.reply_text(
                "❌ <b>Error:</b> Could not match or parse any active/growth funds from this statement.\n"
                "Please verify it is a valid Groww holdings export sheet."
            )
            return
            
        await update.message.reply_text(f"📈 Scored {len(holdings_list)} holdings. Running rotation analysis...")
        
        # Run report generation
        # Run in executor because generate_report has synchronous DB queries and computations
        loop = asyncio.get_running_loop()
        report_blocks = await loop.run_in_executor(
            None, weekly_advisor.generate_report, holdings_list
        )
        
        # Send blocks
        for block in report_blocks:
            await update.message.reply_text(block, parse_mode="HTML")
            
    except Exception as e:
        logger.error(f"Error processing file id: {e}")
        # If download fails due to file deletion or invalid reference
        if "file not found" in str(e).lower() or "bad request" in str(e).lower():
            delete_latest_file_id()
            await update.message.reply_text(
                "❌ <b>Your previous Groww statement is no longer available on Telegram</b> (or the chat history was deleted).\n\n"
                "Please upload a new Groww statement (.xlsx) to run analysis."
            )
        else:
            await update.message.reply_text(f"💥 <b>Analysis Error:</b> {e}")

async def analyze_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Triggers analysis using the saved statement reference."""
    if not is_authorized(update):
        return
        
    file_id = get_latest_file_id()
    if not file_id:
        await update.message.reply_text(
            "⚠️ <b>No Groww statement available!</b>\n\n"
            "Please upload your Groww Mutual Funds statement (.xlsx) first by sending it as a document to this chat."
        )
        return
        
    await run_report_for_file_id(update, file_id)

async def invest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Suggests an allocation for an investment amount."""
    if not is_authorized(update):
        return
        
    if not context.args:
        await update.message.reply_text(
            "⚠️ <b>Usage:</b>\n"
            "• <code>/invest [amount]</code> (e.g. <code>/invest 50000</code>)",
            parse_mode="HTML"
        )
        return
        
    amount_str = context.args[0].replace(",", "")
    try:
        amount = float(amount_str)
        if amount <= 0:
            raise ValueError()
    except ValueError:
        await update.message.reply_text(
            "❌ <b>Error:</b> Please specify a valid positive numeric amount (e.g., 50000)."
        )
        return
        
    await update.message.reply_text("📊 Calculating top candidate allocation plan...")
    
    try:
        loop = asyncio.get_running_loop()
        suggestion_text = await loop.run_in_executor(
            None, weekly_advisor.suggest_lumpsum_allocation, amount
        )
        await update.message.reply_text(suggestion_text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error generating investment allocation: {e}")
        await update.message.reply_text(f"💥 <b>Error generating allocation:</b> {e}")

async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processes document uploads."""
    if not is_authorized(update):
        return
        
    doc = update.message.document
    file_name = doc.file_name.lower()
    
    if not (file_name.endswith(".xlsx") or file_name.endswith(".xls")):
        await update.message.reply_text("❌ Please upload a valid Excel spreadsheet (.xlsx or .xls).")
        return
        
    await update.message.reply_text("📥 <b>New Groww statement received!</b>")
    
    # Save the file_id reference
    set_latest_file_id(doc.file_id)
    
    # Run immediate analysis
    await run_report_for_file_id(update, doc.file_id)

def main():
    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set in environment variables.")
        return
        
    # Verify DB initialized
    daily_fetch.main()
    
    logger.info("Starting Telegram Bot application...")
    application = Application.builder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", start_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("analyze", analyze_command))
    application.add_handler(CommandHandler("report", analyze_command))
    application.add_handler(CommandHandler("invest", invest_command))
    application.add_handler(CommandHandler("lumpsum", invest_command)) # Keep fallback
    application.add_handler(MessageHandler(filters.Document.ALL, document_handler))
    
    application.run_polling()

if __name__ == "__main__":
    main()
