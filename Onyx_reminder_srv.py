import threading
import re
import os
from datetime import datetime, timedelta
from flask import Flask, request

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

# --- Flask для фейкового webhook ---
app = Flask(__name__)

@app.route('/', methods=['GET', 'POST'])
def index():
    return "OK", 200

def run_flask():
    # Запускаем flask на 0.0.0.0:8080
    app.run(host='0.0.0.0', port=8080)

# --- Функция для парсинга времени ---
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

# --- Обработчик команды /start ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Напиши время для напоминания, например 'через 5 минут' или '15:30'")

# --- Обработчик текстовых сообщений ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    dt = parse_time_string(text)
    if dt is None:
        await update.message.reply_text("Не удалось распознать время. Попробуй ещё раз.")
        return

    delta = dt - datetime.now()
    seconds = delta.total_seconds()
    if seconds <= 0:
        await update.message.reply_text("Время уже прошло, попробуй другое.")
        return

    await update.message.reply_text(f"Напоминание установлено на {dt.strftime('%Y-%m-%d %H:%M')}")

    # Запланируем отправку напоминания
    async def reminder():
        await context.bot.send_message(chat_id=update.message.chat_id, text=f"⏰ Напоминание: {text}")

    # Запускаем отложенный вызов
    context.application.create_task(asyncio.sleep(seconds))
    context.application.create_task(asyncio.ensure_future(asyncio.sleep(seconds)))
    # Лучше использовать JobQueue (но для простоты так)
    context.application.job_queue.run_once(lambda ctx: asyncio.create_task(reminder()), when=seconds)

import asyncio

if __name__ == '__main__':
    # Запускаем Flask в отдельном потоке
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # Запускаем бота
    BOT_TOKEN = os.getenv("BOT_TOKEN")

    application = ApplicationBuilder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    application.run_polling()


