import os
import logging
import asyncio
from flask import Flask, request

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes,
    MessageHandler, ConversationHandler, filters
)

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

import dateparser
from datetime import datetime

# === Настройки ===
BOT_TOKEN = os.getenv("BOT_TOKEN")  # Установи в Render переменную окружения

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# === Scheduler ===
scheduler = AsyncIOScheduler()
scheduler.start()

# === Состояния ConversationHandler ===
SET_REMINDER_TEXT, SET_REMINDER_TIME = range(2)

# Хранилище напоминаний
user_reminders = {}

# === Команды ===

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("➕ Добавить напоминание", callback_data="add")],
        [InlineKeyboardButton("📋 Мои напоминания", callback_data="list")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Привет! Что хочешь сделать?", reply_markup=reply_markup)

async def handle_start_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        if query.data == "add":
            await query.edit_message_text("Введите текст напоминания:")
            return SET_REMINDER_TEXT
        elif query.data == "list":
            user_id = query.from_user.id
            reminders = user_reminders.get(user_id, [])
            if reminders:
                text = "Ваши напоминания:\n" + "\n".join(
                    f"🔔 {text} — {time.strftime('%Y-%m-%d %H:%M')}"
                    for text, time in reminders
                )
            else:
                text = "У вас пока нет напоминаний."
            await query.edit_message_text(text)
    return ConversationHandler.END

async def receive_reminder_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['reminder_text'] = update.message.text
    await update.message.reply_text("⏰ Когда напомнить? (например: 'через 10 минут', 'завтра в 9 утра')")
    return SET_REMINDER_TIME

async def receive_reminder_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    time_input = update.message.text
    reminder_time = dateparser.parse(time_input, settings={'PREFER_DATES_FROM': 'future'})

    if not reminder_time or reminder_time <= datetime.now():
        await update.message.reply_text("⛔ Не удалось распознать время или оно в прошлом. Попробуйте снова.")
        return SET_REMINDER_TIME

    user_id = update.message.from_user.id
    text = context.user_data['reminder_text']
    user_reminders.setdefault(user_id, []).append((text, reminder_time))

    # Планирование задачи
    scheduler.add_job(
        notify_user,
        trigger=DateTrigger(run_date=reminder_time),
        args=[user_id, text]
    )

    await update.message.reply_text(f"✅ Напоминание установлено на {reminder_time.strftime('%Y-%m-%d %H:%M')}")
    return ConversationHandler.END

async def notify_user(user_id, text):
    try:
        await application.bot.send_message(chat_id=user_id, text=f"🔔 Напоминание: {text}")
    except Exception as e:
        logging.error(f"Ошибка при отправке напоминания: {e}")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Операция отменена.")
    return ConversationHandler.END

# === Flask Webhook ===

@app.route(f"/webhook/{BOT_TOKEN}", methods=["POST"])
async def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    await application.process_update(update)
    return "ok"

# === Хендлеры ===

conv_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(handle_start_menu)],
    states={
        SET_REMINDER_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_reminder_text)],
        SET_REMINDER_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_reminder_time)],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
    per_message=True,
)

# === Приложение Telegram ===
application = Application.builder().token(BOT_TOKEN).build()
application.add_handler(CommandHandler("start", start))
application.add_handler(conv_handler)

# === Запуск ===

if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()

    async def setup_webhook():
        webhook_url = f"https://<your-render-subdomain>.onrender.com/webhook/{BOT_TOKEN}"
        await application.bot.set_webhook(webhook_url)
        logging.info(f"Webhook установлен: {webhook_url}")

    asyncio.run(setup_webhook())
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))


