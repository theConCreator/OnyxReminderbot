import os
import re
import sqlite3
import logging
import asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

# === Load env vars ===
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
DB_FILE = "reminders.db"

# === Logging ===
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === Conversation states ===
GET_TEXT, GET_TIME, GET_EFFECT = range(3)

# === Scheduler ===
scheduler = AsyncIOScheduler()

# === Keyboards ===
start_menu = InlineKeyboardMarkup([
    [InlineKeyboardButton("📝 Создать напоминание", callback_data="new")],
    [InlineKeyboardButton("📋 Мои напоминания", callback_data="list")]
])

persistent_kb = ReplyKeyboardMarkup(
    [["📝 Новое", "📋 Список"]],
    resize_keyboard=True
)

# === DB init ===
def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                text TEXT,
                time TEXT,
                effect TEXT
            )
            """
        )

# === Save to DB ===
def save_reminder(user_id, text, iso_time, effect):
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.execute(
            "INSERT INTO reminders (user_id, text, time, effect) VALUES (?, ?, ?, ?)",
            (user_id, text, iso_time, effect)
        )
        conn.commit()
        return cur.lastrowid

# === Parse time input ===
def parse_time_string(s: str) -> datetime | None:
    s = s.strip().lower()
    now = datetime.now()

    patterns = [
        (r"через\s+(\d+)\s*(дней|дня|дн)", 'days'),
        (r"через\s+(\d+)\s*(часов|часа|ч)", 'hours'),
        (r"через\s+(\d+)\s*(минут|минуты|мин|м)", 'minutes'),
        (r"in\s+(\d+)\s*(days?|d)", 'days'),
        (r"in\s+(\d+)\s*(hours?|h)", 'hours'),
        (r"in\s+(\d+)\s*(minutes?|mins?|m)", 'minutes'),
    ]
    for patt, unit in patterns:
        m = re.search(patt, s)
        if m:
            val = int(m.group(1))
            return now + timedelta(**{unit: val})

    m = re.match(r"^(\d{1,2}):(\d{2})$", s)
    if m:
        hr, mn = map(int, m.groups())
        dt = now.replace(hour=hr, minute=mn, second=0, microsecond=0)
        if dt < now:
            dt += timedelta(days=1)
        return dt

    m = re.match(r"^(\d{1,2})\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+(\d{1,2}):(\d{2})$", s)
    if m:
        day = int(m.group(1))
        month_str = m.group(2)
        hour = int(m.group(3))
        minute = int(m.group(4))
        month_map = {
            "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
            "мая": 5, "июня": 6, "июля": 7, "августа": 8,
            "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12
        }
        month = month_map[month_str]
        year = now.year
        dt = datetime(year, month, day, hour, minute)
        if dt < now:
            dt = dt.replace(year=year + 1)
        return dt

    return None

# === Handlers ===

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Привет! Что вы хотите сделать?", reply_markup=start_menu)

async def handle_start_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "new":
        return await new_reminder(update, context)
    return await list_reminders(update, context)

async def new_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        msg = update.callback_query.message
    else:
        msg = update.message
    await msg.reply_text("✍️ Введите текст напоминания:", reply_markup=persistent_kb)
    return GET_TEXT

async def get_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text_input = update.message.text
    if text_input in ["📋 Список", "📝 Новое"]:
        return await list_reminders(update, context)
    context.user_data['text'] = text_input
    await update.message.reply_text("⏱ Введите время (например, 'через 20 минут', '14:30', '1 июля 13:00'):", reply_markup=persistent_kb)
    return GET_TIME

async def get_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dt = parse_time_string(update.message.text)
    if not dt:
        await update.message.reply_text("❌ Не удалось распознать время. Попробуйте ещё раз:")
        return GET_TIME
    context.user_data['time'] = dt

    effects = ["⏰","📌","🔥","🎯","💡","🚀","✅","📞","🧠"]
    rows = [[InlineKeyboardButton(e, callback_data=f"effect_{e}") for e in effects[i:i+3]] for i in range(0, len(effects), 3)]
    kb = InlineKeyboardMarkup(rows)
    await update.message.reply_text("Выберите эффект:", reply_markup=kb)
    return GET_EFFECT

async def get_effect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    effect = query.data.split("_", 1)[1]
    user_id = query.from_user.id
    text = context.user_data['text']
    dt = context.user_data['time']
    iso = dt.isoformat()
    save_reminder(user_id, text, iso, effect)

    async def job():
        await context.bot.send_message(user_id, f"{effect} Напоминание: {text}")

    def job_wrapper():
        asyncio.create_task(job())

    scheduler.add_job(job_wrapper, 'date', run_date=dt)
    await query.edit_message_text("✅ Напоминание установлено!")
    return ConversationHandler.END

async def list_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.callback_query.message if update.callback_query else update.message
    if update.callback_query:
        await update.callback_query.answer()
    user_id = msg.chat.id
    now = datetime.now().isoformat()
    rows = []
    with sqlite3.connect(DB_FILE) as conn:
        for rid, text, t, effect in conn.execute("SELECT id,text,time,effect FROM reminders WHERE user_id=? AND time>? ORDER BY time", (user_id, now)):
            rows.append((rid, text, datetime.fromisoformat(t), effect))
    if not rows:
        await msg.reply_text("📭 Напоминаний нет.", reply_markup=start_menu)
        return ConversationHandler.END
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{text} в {dt.strftime('%H:%M')}", callback_data=f"view_{rid}")]
        for rid, text, dt, effect in rows
    ])
    await msg.reply_text("📝 Ваши напоминания:", reply_markup=kb)


# === Main function ===

async def main():
	init_db()
	application = Application.builder().token(TOKEN).build()

application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(handle_start_menu))
application.add_handler(ConversationHandler(
    entry_points=[CommandHandler("new", new_reminder), MessageHandler(filters.TEXT, get_text)],
    states={GET_TEXT: [MessageHandler(filters.TEXT, get_text)],
            GET_TIME: [MessageHandler(filters.TEXT, get_time)],
            GET_EFFECT: [CallbackQueryHandler(get_effect)]},
    fallbacks=[],
))

scheduler.start()
await application.run_polling()

if __name__ == '__main__':
	asyncio.run(main())

