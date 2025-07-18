import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)

# Включаем логирование
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# Состояния для ConversationHandler
DESC, = range(1)

# Хранилище напоминаний: {user_id: [{id, text}]}
reminders = {}

# Получаем токен из переменных окружения
TOKEN = os.getenv("BOT_TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Новое напоминание", callback_data="new_reminder")],
        [InlineKeyboardButton("Список напоминаний", callback_data="list_reminders")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.message:
        await update.message.reply_text("Главное меню:", reply_markup=reply_markup)
    elif update.callback_query:
        await update.callback_query.edit_message_text("Главное меню:", reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "new_reminder":
        await query.edit_message_text("Введите текст напоминания (или /cancel для отмены):")
        return DESC
    elif query.data == "list_reminders":
        user_id = query.from_user.id
        user_reminders = reminders.get(user_id, [])
        if not user_reminders:
            await query.edit_message_text("Список напоминаний пуст.")
            return ConversationHandler.END
        keyboard = [
            [InlineKeyboardButton(reminder['text'], callback_data=f"del_{reminder['id']}")]
            for reminder in user_reminders
        ]
        keyboard.append([InlineKeyboardButton("Назад", callback_data="back")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Ваши напоминания (нажмите для удаления):", reply_markup=reply_markup)
        return ConversationHandler.END
    elif query.data.startswith("del_"):
        reminder_id = int(query.data.split("_")[1])
        user_id = query.from_user.id
        user_reminders = reminders.get(user_id, [])
        reminders[user_id] = [r for r in user_reminders if r['id'] != reminder_id]
        await query.answer("Напоминание удалено")
        # Обновляем список после удаления
        user_reminders = reminders.get(user_id, [])
        if not user_reminders:
            await query.edit_message_text("Список напоминаний пуст.")
        else:
            keyboard = [
                [InlineKeyboardButton(reminder['text'], callback_data=f"del_{reminder['id']}")]
                for reminder in user_reminders
            ]
            keyboard.append([InlineKeyboardButton("Назад", callback_data="back")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text("Ваши напоминания (нажмите для удаления):", reply_markup=reply_markup)
        return ConversationHandler.END
    elif query.data == "back":
        return await start(update, context)

async def receive_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "/cancel":
        await update.message.reply_text("Создание напоминания отменено.")
        return ConversationHandler.END

    user_id = update.message.from_user.id
    user_reminders = reminders.setdefault(user_id, [])
    new_id = max([r['id'] for r in user_reminders], default=0) + 1
    user_reminders.append({'id': new_id, 'text': text})

    await update.message.reply_text(f"Напоминание сохранено: {text}")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Действие отменено.")
    return ConversationHandler.END

def main():
    application = ApplicationBuilder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler, pattern="^(new_reminder|list_reminders)$")],
        states={
            DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_description)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False,
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(button_handler))  # Для кнопок удаления и возврата

    application.run_polling()

if __name__ == "__main__":
    main()

