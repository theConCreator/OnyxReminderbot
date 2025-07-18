import asyncio
import sqlite3
import logging
import os
import re
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

    # 1. Relative Russian/English
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

    # 2. Time only HH:MM
    m = re.match(r"^(\d{1,2}):(\d{2})$", s)
    if m:
        hr, mn = map(int, m.groups())
        dt = now.replace(hour=hr, minute=mn, second=0, microsecond=0)
        if dt < now:
            dt += timedelta(days=1)
        return dt

    # 3. ISO: YYYY-MM-DD HH:MM
    m = re.match(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})$", s)
    if m:
        return datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}")

    # 4. Natural language: 1 июня 14:30 or 1 june 14:30
    month_names = {
        # Russian
        "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
        "мая": 5, "июня": 6, "июля": 7, "августа": 8,
        "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
        # English
        "january": 1, "february": 2, "march": 3, "april": 4,
        "may": 5, "june": 6, "july": 7, "august": 8,
        "september": 9, "october": 10, "november": 11, "december": 12,
    }

    m = re.match(r"(\d{1,2})\s+([a-zа-яё]+)\s+(\d{1,2}):(\d{2})", s)
    if m:
        day, mon_str, hour, minute = m.groups()
        month = month_names.get(mon_str)
        if not month:
            return None
        try:
            year = now.year
            dt = datetime(year, month, int(day), int(hour), int(minute))
            if dt < now:
                dt = dt.replace(year=year + 1)
            return dt
        except ValueError:
            return None

    return None


# === HANDLERS ===

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я - Onyx Reminder, что вы хотите сделать?",
        reply_markup=start_menu
    )

async def handle_start_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "new":
        # Передаём весь update, чтобы new_reminder работал корректно
        return await new_reminder(update, context)
    else:
        return await list_reminders(update, context)

# ---- New reminder ----
async def new_reminder(update, context: ContextTypes.DEFAULT_TYPE):
    # Обрабатываем update или callback_query корректно
    if isinstance(update, Update):
        if update.callback_query:
            msg = update.callback_query.message
            await update.callback_query.answer()
        else:
            msg = update.message
    else:
        # Если update - это уже callback_query (редко), можно попытаться так:
        msg = update.message
        await context.bot.answer_callback_query(update.id)
    await msg.reply_text("✍️ Введите текст напоминания:", reply_markup=persistent_kb)
    return GET_TEXT

async def get_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text_input = update.message.text.strip()
    # Защита: если пользователь ввёл команду списка или нового вместо текста
    if text_input in ["📋 Список", "📝 Новое"]:
        # Выполняем соответствующую команду вместо сохранения
        if text_input == "📋 Список":
            return await list_reminders(update, context)
        elif text_input == "📝 Новое":
            return await new_reminder(update, context)
    context.user_data['text'] = text_input
    await update.message.reply_text(
        "⏱ Введите время (например 1 июня 10:00, 14:30 (поставится на ближайшее), через 20 минут, in 2h, и т.д.):",
        reply_markup=persistent_kb
    )
    return GET_TIME

async def get_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dt = parse_time_string(update.message.text)
    if not dt:
        await update.message.reply_text(
            "❌ Не удалось распознать время или время указано без часов и минут. Попробуйте ещё раз:",
            reply_markup=persistent_kb
        )
        return GET_TIME
    context.user_data['time'] = dt
    # 9 эмодзи эффектов
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
    # Используем InlineKeyboardMarkup с пустым списком, чтобы избежать ошибки "Inline keyboard expected"
    await query.edit_message_text("✅ Напоминание установлено!", reply_markup=InlineKeyboardMarkup([]))
    return ConversationHandler.END

# ---- List reminders ----
async def list_reminders(update, context: ContextTypes.DEFAULT_TYPE):
    if hasattr(update, "callback_query") and update.callback_query:
        msg = update.callback_query.message
        await update.callback_query.answer()
    else:
        msg = update.message
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
        await msg.reply_text("📭 Активных напоминаний нет.", reply_markup=start_menu)
        return ConversationHandler.END
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{effect} {text} в {dt.strftime('%d.%m %H:%M')}", callback_data=f"view_{rid}")]
        for rid, text, dt, effect in rows
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
        conn.execute("DELETE FROM reminders WHERE id=?", (rid,))
        conn.commit()
    await query.edit_message_text("❌ Напоминание удалено.", reply_markup=start_menu)

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
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)],
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



