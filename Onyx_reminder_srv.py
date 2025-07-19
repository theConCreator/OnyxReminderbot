import os
import re
import asyncio
import logging
from datetime import datetime, timedelta
from threading import Thread

from flask import Flask
from apscheduler.schedulers.background import BackgroundScheduler
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.environ.get("PORT", 10000))

logging.basicConfig(level=logging.INFO)

# Flask app for fake port binding
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "Bot is alive!"

def run_flask():
    flask_app.run(host='0.0.0.0', port=PORT)

# Scheduler
scheduler = BackgroundScheduler()
scheduler.start()

# In-memory store
user_reminders = {}

# States
ASK_DESCRIPTION, ASK_TIME = range(2)

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

    return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("➕ Новое напоминание", callback_data="new_reminder")],
        [InlineKeyboardButton("📋 Мои напоминания", callback_data="list_reminders")]
    ]
    await update.message.reply_text(
        "👋 Добро пожаловать! Выберите действие:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "new_reminder":
        keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data="cancel_creation")]]
        await query.message.reply_text(
            "✏️ Введите описание напоминания:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return ASK_DESCRIPTION
    elif query.data == "list_reminders":
        user_id = query.from_user.id
        reminders = user_reminders.get(user_id, [])
        if not reminders:
            await query.message.reply_text("🗒 У вас пока нет напоминаний.")
        else:
            buttons = [
                [InlineKeyboardButton(f"🗑 {text}", callback_data=f"del_{i}")]
                for i, (text, _) in enumerate(reminders)
            ]
            await query.message.reply_text("Ваши напоминания:", reply_markup=InlineKeyboardMarkup(buttons))
        return ConversationHandler.END
    elif query.data.startswith("del_"):
        idx = int(query.data.split("_")[1])
        user_id = query.from_user.id
        if user_id in user_reminders and idx < len(user_reminders[user_id]):
            removed = user_reminders[user_id].pop(idx)
            await query.message.reply_text(f"✅ Напоминание удалено: {removed[0]}")
        return ConversationHandler.END
    elif query.data == "cancel_creation":
        await query.message.reply_text("❌ Создание напоминания отменено.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

async def receive_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["reminder_text"] = update.message.text
    await update.message.reply_text("⏰ Теперь укажите время (например, `через 10 минут` или `14:30`):",
                                    reply_markup=ReplyKeyboardRemove())
    return ASK_TIME

async def receive_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reminder_text = context.user_data.get("reminder_text")
    reminder_time = parse_time_string(update.message.text)
    if not reminder_time:
        await update.message.reply_text("❗️Не удалось распознать время. Попробуйте снова.")
        return ASK_TIME

    user_id = update.message.from_user.id
    user_reminders.setdefault(user_id, []).append((reminder_text, reminder_time))

    scheduler.add_job(
        lambda: asyncio.run(send_reminder(context, user_id, reminder_text)),
        trigger='date', run_date=reminder_time
    )

    await update.message.reply_text(f"✅ Напоминание установлено: \"{reminder_text}\" в {reminder_time.strftime('%H:%M %d.%m')}")
    return ConversationHandler.END

async def send_reminder(context: ContextTypes.DEFAULT_TYPE, user_id: int, text: str):
    try:
        await context.bot.send_message(chat_id=user_id, text=f"🔔 Напоминание: {text}")
    except Exception as e:
        logging.error(f"Ошибка при отправке напоминания: {e}")

async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 Извините, я не понимаю эту команду.")

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
    asyncio.run(main())  # Запускаем цикл событий с помощью asyncio.run

