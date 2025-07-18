import os
import logging
import sqlite3
import re
import asyncio
from datetime import datetime, timedelta
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import nest_asyncio

# === CONFIG ===
TOKEN = os.getenv("BOT_TOKEN")
DB_FILE = "reminders.db"

# === LOGGING ===
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === STATES ===
GET_TEXT, GET_TIME, GET_EFFECT = range(3)

# === SCHEDULER ===
scheduler = AsyncIOScheduler()

# === KEYBOARDS ===
start_menu = InlineKeyboardMarkup([
    [InlineKeyboardButton("📝 Создать напоминание", callback_data="new")],
    [InlineKeyboardButton("📋 Мои напоминания", callback_data="list")],
])

persistent_kb = ReplyKeyboardMarkup(
    [["📝 Новое", "📋 Список"]],
    resize_keyboard=True
)

# === DATABASE ===
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

# === SAVE ===
def save_reminder(user_id, text, iso_time, effect):
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.execute(
            "INSERT INTO reminders (user_id, text, time, effect) VALUES (?, ?, ?, ?)",
            (user_id, text, iso_time, effect)
        )
        conn.commit()
        return cur.lastrowid

# === TIME PARSING ===
def parse_time_string(s: str) -> datetime | None:
    s = s.strip().lower()
    now = datetime.now()

    # 1. (DATE WITHOUT TIME NOT SUPPORTED HERE)  
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

    # 3. Time HH:MM
    m = re.match(r"^(\d{1,2}):(\d{2})$", s)
    if m:
        hr, mn = map(int, m.groups())
        dt = now.replace(hour=hr, minute=mn, second=0, microsecond=0)
        if dt < now:
            dt += timedelta(days=1)
        return dt

    # 4. Full date 'YYYY-MM-DD HH:MM'
    m = re.match(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})$", s)
    if m:
        return datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}")

    return None

# === HANDLERS ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Что вы хотите сделать?",
        reply_markup=start_menu
    )

async def handle_start_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "new":
        return await new_reminder(query, context)
    return await list_reminders(query, context)

# ---- New reminder ----
async def new_reminder(update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query is not None:
        msg = update.callback_query.message
        await update.callback_query.answer()
    else:
        msg = update.message
    await msg.reply_text("✍️ Введите текст напоминания:", reply_markup=persistent_kb)
    return GET_TEXT

async def get_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text_input = update.message.text
    if text_input in ["📋 Список", "📝 Новое"]:
        return await list_reminders(update, context)
    context.user_data['text'] = text_input
    await update.message.reply_text(
        "⏱ Введите время (17 июня, через 20 минут, 14:30, in 2h):",
        reply_markup=persistent_kb
    )
    return GET_TIME

async def get_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dt = parse_time_string(update.message.text)
    if not dt:
        await update.message.reply_text(
            "❌ Не удалось распознать время. Попробуйте ещё раз:",
            reply_markup=persistent_kb
        )
        return GET_TIME
    context.user_data['time'] = dt
    effects = ["⏰","📌","🔥","🎯","💡","🚀","✅","📞","🧠"]
    rows = []
    for i in range(0, len(effects), 3):
        row = [InlineKeyboardButton(e, callback_data=f"effect_{e}") for e in effects[i:i+3]]
        rows.append(row)
    kb = InlineKeyboardMarkup(rows)
    await update.message.reply_text("Выберите эффект:", reply_markup=kb)
    return GET_EFFECT

async def get_effect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    effect = query.data.split("_",1)[1]
    user_id = query.from_user.id
    text = context.user_data['text']
    dt = context.user_data['time']
    iso = dt.isoformat()
    save_reminder(user_id, text, iso, effect)

    async def job():
        await context.bot.send_message(user_id, f"{effect} Напоминание: {text}")

    scheduler.add_job(job, 'date', run_date=dt)
    await query.edit_message_text("✅ Напоминание установлено!")
    return ConversationHandler.END

# ---- List reminders ----
async def list_reminders(update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.callback_query.message if hasattr(update, 'callback_query') else update.message
    if hasattr(update, 'callback_query'):
        await update.callback_query.answer()
    user_id = msg.chat.id
    now = datetime.now().isoformat()
    rows = []
    with sqlite3.connect(DB_FILE) as conn:
        for rid, text, t, effect in conn.execute(
            "SELECT id,text,time,effect FROM reminders WHERE user_id=? AND time>? ORDER BY time",
            (user_id, now)
        ):
            rows.append((rid, text, datetime.fromisoformat(t), effect))
    if not rows:
        await msg.reply_text("📭 Активных нет.", reply_markup=start_menu)
        return ConversationHandler.END
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{text} в {dt.strftime('%H:%M')}", callback_data=f"view_{rid}")]
        for rid,text,dt,effect in rows
    ])
    await msg.reply_text("📋 Активные напоминания:", reply_markup=kb)
    return ConversationHandler.END

async def reminder_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    rid = int(query.data.split("_",1)[1])
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Удалить", callback_data=f"delete_{rid}")],
        [InlineKeyboardButton("↩️ Меню", callback_data="back")]
    ])
    await query.edit_message_text("Выберите действие:", reply_markup=kb)

async def delete_rem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Удалено.")
    rid = int(query.data.split("_",1)[1])
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("DELETE FROM reminders WHERE id=?",(rid,))
        conn.commit()
    await query.edit_message_text("❌ Удалено.", reply_markup=start_menu)

async def back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Главное меню:", reply_markup=start_menu)

# === MAIN ===
async def main():
    nest_asyncio.apply()
    init_db()
    scheduler.start()
    app = ApplicationBuilder().token(TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(handle_start_menu, pattern="^(new|list)$"),
            CommandHandler("start", start),
            MessageHandler(filters.Regex("^📝 Новое$"), new_reminder),
            MessageHandler(filters.Regex("^📋 Список$"), list_reminders),
        ],
        states={
            GET_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_text)],
            GET_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_time)],
            GET_EFFECT: [CallbackQueryHandler(get_effect, pattern="^effect_")],
        },
        fallbacks=[CommandHandler("cancel", lambda u,c: ConversationHandler.END)],
        per_chat=True
    )

    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(reminder_action, pattern="^view_"))
    app.add_handler(CallbackQueryHandler(delete_rem, pattern="^delete_"))
    app.add_handler(CallbackQueryHandler(back_to_menu, pattern="^back$"))

    logger.info("Bot starting...")
    await app.run_polling()

if __name__ == '__main__':
    asyncio.run(main())

