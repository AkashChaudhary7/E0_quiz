# bot.py
import os
import json
import random
import logging
import asyncio
from pathlib import Path

import requests
import pytz
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- CONFIG (from environment variables) ----------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")  # put as string or integer
QUESTIONS_PER_RUN = int(os.environ.get("QUESTIONS_PER_RUN", "1"))
SCHEDULE_HOUR = int(os.environ.get("SCHEDULE_HOUR", "10"))       # default 10
SCHEDULE_MINUTE = int(os.environ.get("SCHEDULE_MINUTE", "0"))    # default 0
TIMEZONE = os.environ.get("TIMEZONE", "Asia/Kolkata")
MODE = os.environ.get("MODE", "random")  # 'random' or 'sequential' (only random implemented)
GITHUB_RAW_BASE = os.environ.get("GITHUB_RAW_BASE", "").rstrip("/") + "/" if os.environ.get("GITHUB_RAW_BASE") else ""
LOCAL_QUESTIONS_DIR = Path("questions")
# --------------------------------------------------------

if not BOT_TOKEN:
    logger.error("BOT_TOKEN is not set in environment. Exiting.")
    raise SystemExit("Set BOT_TOKEN environment variable")

if not CHAT_ID:
    logger.error("CHAT_ID is not set in environment. Exiting.")
    raise SystemExit("Set CHAT_ID environment variable")

# Utility: discover chapters
def discover_chapters():
    if LOCAL_QUESTIONS_DIR.exists() and any(LOCAL_QUESTIONS_DIR.glob("*.json")):
        return [p.stem for p in sorted(LOCAL_QUESTIONS_DIR.glob("*.json"))]
    # fallback to GITHUB_RAW_BASE: need a CHAPTERS env var then (not implemented)
    return []

CHAPTERS = os.environ.get("CHAPTERS")  # comma-separated optional
if CHAPTERS:
    CHAPTERS = [c.strip() for c in CHAPTERS.split(",") if c.strip()]
else:
    CHAPTERS = discover_chapters()

if not CHAPTERS:
    logger.error("No chapters found. Put json files in /questions or set CHAPTERS env var.")
    raise SystemExit("No chapters found")

logger.info("Chapters: %s", CHAPTERS)

# Load questions (prefer local file in repo)
def load_questions(chapter):
    local = LOCAL_QUESTIONS_DIR / f"{chapter}.json"
    if local.exists():
        with open(local, "r", encoding="utf-8") as f:
            return json.load(f)
    # fallback: try raw GitHub
    if GITHUB_RAW_BASE:
        url = f"{GITHUB_RAW_BASE}{chapter}.json"
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        return r.json()
    raise FileNotFoundError(f"Chapter file for '{chapter}' not found locally or via GITHUB_RAW_BASE")

# Build inline keyboard for a question
def build_keyboard(question, chapter):
    buttons = []
    for i, opt in enumerate(question["options"]):
        # callback_data: chapter|qid|opt_index
        cb = f"{chapter}|{question['id']}|{i}"
        buttons.append([InlineKeyboardButton(opt["text"], callback_data=cb)])
    return InlineKeyboardMarkup(buttons)

# Format results showing correct / incorrect after answer
def format_result_text(question, selected_index):
    lines = [f"<b>❓ {question['question']}</b>\n"]
    correct_index = next((i for i, o in enumerate(question["options"]) if o.get("correct")), None)

    for i, opt in enumerate(question["options"]):
        prefix = ""
        if i == correct_index:
            prefix = "✅"  # correct answer
        elif i == selected_index:
            prefix = "❌"  # user chose wrong
        else:
            prefix = "▫️"
        lines.append(f"{prefix} {opt['text']}")
    # explanation: show explanation of selected option (or of correct option if you prefer)
    selected_expl = question["options"][selected_index].get("explanation", "")
    lines.append(f"\n💡 {selected_expl}")
    return "\n".join(lines)

# Async function to send one question
async def send_one_question(application, chapter):
    try:
        questions = load_questions(chapter)
        q = random.choice(questions)
        text = f"📚 <b>{chapter}</b>\n\n❓ {q['question']}"
        await application.bot.send_message(
            chat_id=CHAT_ID,
            text=text,
            parse_mode="HTML",
            reply_markup=build_keyboard(q, chapter)
        )
        logger.info("Sent question id=%s from chapter=%s", q.get("id"), chapter)
    except Exception as e:
        logger.exception("Failed to send question for chapter %s: %s", chapter, e)

# Wrapper to send multiple questions in a run
async def send_questions(application):
    for _ in range(max(1, QUESTIONS_PER_RUN)):
        chapter = random.choice(CHAPTERS) if MODE == "random" else CHAPTERS[0]
        await send_one_question(application, chapter)
        await asyncio.sleep(0.6)  # small delay between messages

# Telegram handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot active ✅. You'll get scheduled quizzes at configured time.")

async def answer_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()  # removes 'loading' state on button
    try:
        chapter, qid, opt_index = query.data.split("|")
        qid = int(qid); opt_index = int(opt_index)
        questions = load_questions(chapter)
        question = next((qq for qq in questions if int(qq.get("id")) == qid), None)
        if not question:
            await query.edit_message_text("Question data not found.")
            return
        new_text = format_result_text(question, opt_index)
        await query.edit_message_text(text=new_text, parse_mode="HTML")
    except Exception as e:
        logger.exception("Error processing callback: %s", e)
        await query.edit_message_text("An error occurred while processing your answer.")

# Main
def main():
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(answer_callback))

    # Scheduler (async)
    tz = pytz.timezone(TIMEZONE)
    scheduler = AsyncIOScheduler(timezone=tz)
    scheduler.start()
    # schedule daily job
    scheduler.add_job(lambda: asyncio.create_task(send_questions(application)),
                      'cron', hour=SCHEDULE_HOUR, minute=SCHEDULE_MINUTE)
    logger.info("Scheduler set for %02d:%02d %s", SCHEDULE_HOUR, SCHEDULE_MINUTE, TIMEZONE)

    logger.info("Starting bot polling...")
    application.run_polling()

if __name__ == "__main__":
    main()
