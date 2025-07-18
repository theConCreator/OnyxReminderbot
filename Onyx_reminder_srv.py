import asyncio
import logging
import os
import sqlite3
import re
from datetime import datetime, timedelta

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
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

# === SETTINGS ===
TOKEN = os.environ["BOT_TOKEN"]
DB_FILE = "reminders.db"
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()

# === STATES ===
GET_TEXT, GET_TIME, GET_EFFECT = range(3)

# === KEYBOARDS ===
start_menu = InlineKeyboardMarkup([
    [InlineKeyboardButton("📝 Создать напоминание", callback_data="new")],
    [InlineKeyboardButton("📋 Мои напоминания", callback_data="list")],
])

persistent_kb = ReplyKeyboardMarkup(
    [["📝 Новое", "📋 Список"]],
    resize_keyboard=True
)

# === DB ===
def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                text TEXT,
                time TEXT,
                effect TEXT
            )
        """)

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

    # HH:MM
    m = re.match(r"^(\d{1,2}):(\d{2})$", s)
    if m:
        hr, mn = map(int, m.groups())
        dt = now.replace(hour=hr, minute=mn, second=0, microsecond=0)
        if dt < now:
            dt += timedelta(days=1)
        return dt

    # YYYY-MM-DD HH:MM
    m = re.match(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})$", s)
    if m:
        return datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}")

    return None

# === HANDLERS ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Привет! Что вы хотите сделать?", reply_markup=start_menu)

async def handle_start_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "new":
        return await new_reminder(query, context)
    return await list_reminders(query, context)

async def new_reminder(update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
        msg = update.callback_query.message
    else:
        msg = update.message
    await msg.reply_text("✍️ Введите текст напоминания:", reply_markup=persistent_kb)
    return GET_TEXT

async def get_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text in ["📋 Список", "📝 Новое"]:
        return await list_reminders(update, context)
    context.user_data['text'] = text
    await update.message.reply_text("⏱ Введите время (напр. 20:30, через 2 часа, in 10 min):", reply_markup=persistent_kb)
    return GET_TIME

async def get_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dt = parse_time_string(update.message.text)
    if not dt:
        await update.message.reply_text("❌ Неверный формат времени. Попробуйте ещё раз:")
        return GET_TIME
    context.user_data['time'] = dt
    effects = ["⏰","📌","🔥","🎯","💡","🚀","✅","📞","🧠"]
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(e, callback_data=f"effect_{e}") for e in effects[i:i+3]]
        for i in range(0, len(effects), 3)
    ])
    await update.message.reply_text("Выберите эффект:", reply_markup=kb)
    return GET_EFFECT

async def get_effect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    effect = query.data.split("_", 1)[1]
    user_id = query.from_user.id
    text = context.user_data['text']
    dt = context.user_data['time']
    save_reminder(user_id, text, dt.isoformat(), effect)

    async def job():
        await context.bot.send_message(user_id, f"{effect} Напоминание: {text}")
    scheduler.add_job(job, 'date', run_date=dt)

    await query.edit_message_text("✅ Напоминание установлено!")
    return ConversationHandler.END

async def list_reminders(update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.callback_query.message if hasattr(update, 'callback_query') else update.message
    if hasattr(update, 'callback_query'):
        await update.callback_query.answer()
    user_id = msg.chat.id
    now = datetime.now().isoformat()
    with sqlite3.connect(DB_FILE) as conn:
        reminders = conn.execute(
            "SELECT id, text, time, effect FROM reminders WHERE user_id=? AND time>?", (user_id, now)
        ).fetchall()
    if not reminders:
        await msg.reply_text("📭 Напоминаний нет.", reply_markup=start_menu)
        return ConversationHandler.END
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{t} в {datetime.fromisoformat(tm).strftime('%H:%M')}", callback_data=f"view_{rid}")]
        for rid, t, tm, e in reminders
    ])
    await msg.reply_text("📋 Ваши напоминания:", reply_markup=kb)
    return ConversationHandler.END

async def reminder_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    rid = int(query.data.split("_")[1])
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Удалить", callback_data=f"delete_{rid}")],
        [InlineKeyboardButton("↩️ Назад", callback_data="back")]
    ])
    await query.edit_message_text("Выберите действие:", reply_markup=kb)

async def delete_rem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    rid = int(query.data.split("_")[1])
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("DELETE FROM reminders WHERE id=?", (rid,))
    await query.answer("Удалено")
    await query.edit_message_text("❌ Напоминание удалено.", reply_markup=start_menu)

async def back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text("📋 Меню", reply_markup=start_menu)

# === MAIN ===
async def main():
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
        fallbacks=[],
        per_chat=True
    )

    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(reminder_action, pattern="^view_"))
    app.add_handler(CallbackQueryHandler(delete_rem, pattern="^delete_"))
    app.add_handler(CallbackQueryHandler(back_to_menu, pattern="^back$"))

    logger.info("Bot started...")
    await app.run_polling()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())

