import logging
import threading
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, ContextTypes, filters
)
from apscheduler.schedulers.background import BackgroundScheduler

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Flask web server to keep Render happy
app = Flask(__name__)

@app.route('/')
def home():
    return "Бот работает!"

def run_web():
    app.run(host="0.0.0.0", port=10000)

# Conversation states
CHOOSE_ACTION, SET_REMINDER = range(2)

# Dictionary to store reminders (in-memory)
user_reminders = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("➕ Новое напоминание", callback_data='new')],
        [InlineKeyboardButton("📋 Мои напоминания", callback_data='list')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Привет! Что вы хотите сделать?", reply_markup=reply_markup)
    return CHOOSE_ACTION

async def handle_start_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        if query.data == 'new':
            await query.edit_message_text("Введите текст напоминания:")
            return SET_REMINDER
        elif query.data == 'list':
            user_id = query.from_user.id
            reminders = user_reminders.get(user_id, [])
            if not reminders:
                await query.edit_message_text("У вас нет напоминаний.")
            else:
                text = "\n".join(f"{i+1}. {r}" for i, r in enumerate(reminders))
                await query.edit_message_text(f"Ваши напоминания:\n{text}")
            return ConversationHandler.END
    return ConversationHandler.END

async def handle_reminder_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    text = update.message.text
    user_reminders.setdefault(user_id, []).append(text)
    await update.message.reply_text(f"✅ Напоминание добавлено: {text}")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Действие отменено.")
    return ConversationHandler.END

def main():
    app_thread = threading.Thread(target=run_web)
    app_thread.start()

    application = ApplicationBuilder().token("YOUR_BOT_TOKEN").build()

    # Scheduler (можно подключить задачи)
    scheduler = BackgroundScheduler()
    scheduler.start()

    # Conversation handler
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHOOSE_ACTION: [
                CallbackQueryHandler(handle_start_menu, pattern='^(new|list)$')
            ],
            SET_REMINDER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_reminder_input)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=True
    )

    application.add_handler(conv_handler)
    application.run_polling()

if __name__ == '__main__':
    main()

