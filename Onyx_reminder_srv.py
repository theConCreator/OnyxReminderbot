import os
import logging
import re
from datetime import datetime, timedelta
from telegram import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
)
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)
import asyncio

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)

logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")

# Этапы разговора
(
    START_MENU,
    INPUT_DESCRIPTION,
    INPUT_TIME,
    SHOW_REMINDERS,
    CONFIRM_DELETE,
) = range(5)

reminders = {}  # {user_id: [{'desc': str, 'time': datetime, 'job_id': str}]}

# Главное меню с кнопками
def get_main_menu():
    keyboard = [
        [InlineKeyboardButton("➕ Новое напоминание", callback_data="new_reminder")],
        [InlineKeyboardButton("📋 Список напоминаний", callback_data="list_reminders")],
    ]
    return InlineKeyboardMarkup(keyboard)

# Кнопка отмены для ввода описания
def get_cancel_keyboard():
    keyboard = [[KeyboardButton("❌ Отмена")]]
    return ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)

# Кнопки для времени отмены (скрываем кнопки)
def get_remove_keyboard():
    return ReplyKeyboardRemove()

# Функция парсинга времени (упрощённая, можно расширять)
def parse_time_string(s: str) -> datetime | None:
    s = s.strip().lower()
    now = datetime.now()

    # Паттерны для "через N минут/часов/дней"
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

    # Формат HH:MM
    m = re.match(r"^(\d{1,2}):(\d{2})$", s)
    if m:
        hr, mn = map(int, m.groups())
        dt = now.replace(hour=hr, minute=mn, second=0, microsecond=0)
        if dt < now:
            dt += timedelta(days=1)
        return dt

    return None

# Стартовое сообщение и меню
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.reply_text(
            "Привет! Выбери действие:",
            reply_markup=get_main_menu()
        )
    else:
        await update.callback_query.answer()
        await update.callback_query.message.edit_text(
            "Привет! Выбери действие:",
            reply_markup=get_main_menu()
        )
    return START_MENU

# Обработка нажатий главного меню
async def start_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "new_reminder":
        await query.message.reply_text(
            "Отправь описание напоминания:",
            reply_markup=get_cancel_keyboard()
        )
        return INPUT_DESCRIPTION

    elif query.data == "list_reminders":
        user_id = query.from_user.id
        user_reminders = reminders.get(user_id, [])

        if not user_reminders:
            await query.message.edit_text(
                "У тебя пока нет напоминаний.",
                reply_markup=get_main_menu()
            )
            return START_MENU

        # Создаем кнопки с напоминаниями для удаления
        buttons = [
            [InlineKeyboardButton(f"{idx+1}. {r['desc']} - {r['time'].strftime('%Y-%m-%d %H:%M')}", callback_data=f"del_{idx}")]
            for idx, r in enumerate(user_reminders)
        ]
        buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_to_menu")])
        await query.message.edit_text(
            "Твои напоминания (нажми чтобы удалить):",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return SHOW_REMINDERS

    elif query.data == "back_to_menu":
        await query.message.edit_text(
            "Выбери действие:",
            reply_markup=get_main_menu()
        )
        return START_MENU

# Отмена ввода описания
async def cancel_input_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "❌ Отмена":
        await update.message.reply_text(
            "Создание напоминания отменено.",
            reply_markup=get_main_menu()
        )
        return START_MENU
    else:
        # Если не отмена — это описание
        context.user_data['description'] = text
        await update.message.reply_text(
            "Введите время напоминания.\nПримеры:\n- через 10 минут\n- 14:30\n- через 2 часа",
            reply_markup=get_remove_keyboard()
        )
        return INPUT_TIME

# Ввод времени напоминания
async def input_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    dt = parse_time_string(text)
    if not dt:
        await update.message.reply_text(
            "Не смог распознать время. Попробуй еще раз.\nПример: через 10 минут, 14:30, через 2 часа"
        )
        return INPUT_TIME

    desc = context.user_data.get('description')
    user_id = update.message.from_user.id

    # Сохраняем напоминание
    reminder = {'desc': desc, 'time': dt}
    reminders.setdefault(user_id, []).append(reminder)

    await update.message.reply_text(
        f"Напоминание сохранено:\n«{desc}» в {dt.strftime('%Y-%m-%d %H:%M')}",
        reply_markup=get_main_menu()
    )
    return START_MENU

# Удаление напоминания по кнопке
async def delete_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user_reminders = reminders.get(user_id, [])

    # Индекс напоминания в callback_data: del_0, del_1 ...
    idx = int(query.data.split('_')[1])

    if 0 <= idx < len(user_reminders):
        removed = user_reminders.pop(idx)
        await query.message.edit_text(
            f"Удалено напоминание:\n«{removed['desc']}»",
            reply_markup=get_main_menu()
        )
    else:
        await query.message.edit_text(
            "Ошибка: напоминание не найдено.",
            reply_markup=get_main_menu()
        )

    return START_MENU

async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Я не понимаю эту команду.")

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CallbackQueryHandler(start_menu_handler, pattern="^(new_reminder|list_reminders|back_to_menu)$")
        ],
        states={
            START_MENU: [
                CallbackQueryHandler(start_menu_handler, pattern="^(new_reminder|list_reminders|back_to_menu)$"),
            ],
            INPUT_DESCRIPTION: [
                MessageHandler(filters.TEXT & (~filters.COMMAND), cancel_input_description)
            ],
            INPUT_TIME: [
                MessageHandler(filters.TEXT & (~filters.COMMAND), input_time)
            ],
            SHOW_REMINDERS: [
                CallbackQueryHandler(delete_reminder, pattern="^del_\\d+$"),
                CallbackQueryHandler(start_menu_handler, pattern="^back_to_menu$")
            ],
        },
        fallbacks=[
            CommandHandler("start", start)  # 👈 добавили сюда
        ],
        per_message=False,
    )

    app.add_handler(conv_handler)

    # Глобальный обработчик неизвестных команд
    app.add_handler(MessageHandler(filters.COMMAND, unknown))

    print("Bot started...")
    app.run_polling()


if __name__ == "__main__":
    main()

