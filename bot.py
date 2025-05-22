import os
import re
import logging
import asyncio
import openai
from datetime import datetime
from typing import Dict
from logging.handlers import RotatingFileHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# Логирование
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        RotatingFileHandler("bot.log", maxBytes=5*1024*1024, backupCount=3),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Конфигурация
CONFIG = {
    "DELAY_SECONDS": int(os.getenv("DELAY_SECONDS", 7200)),  # 2 часа
    "MAX_MESSAGE_LENGTH": 3900,
    "OPENAI_MAX_TOKENS": 6000,
    "OPENAI_MAX_CONCURRENT": 5,
    "MIN_TEXT_LENGTH_TAROT": 100,
    "MIN_TEXT_LENGTH_MATRIX": 15,
    "RETRY_DELAY": 5,
    "MAX_RETRIES": 3,
}

# API ключи
openai.api_key = os.getenv("OPENAI_API_KEY")
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not BOT_TOKEN or not openai.api_key:
    logger.critical("Токены не установлены.")
    raise ValueError("Токены TELEGRAM_TOKEN и OPENAI_API_KEY должны быть заданы.")

# Хранилище
user_data: Dict[int, dict] = {}
completed_users: set = set()
semaphore = asyncio.Semaphore(CONFIG["OPENAI_MAX_CONCURRENT"])

# Промпты
PROMPT_TAROT = """
Ты — Замира, 42 года. Женщина с даром, профессиональный таролог... [сокращено в этом блоке] ...
ДАННЫЕ КЛИЕНТА:
{input_text}
"""

PROMPT_MATRIX = """
Ты — Замира, 42 года. Эзотерик, ясновидящая и специалист по матрице судьбы... [сокращено в этом блоке] ...
ДАННЫЕ КЛИЕНТА:
{input_text}
"""

# Тексты
WELCOME_TEXT = "Здравствуйте!\n\nПервый расклад на Таро или разбор по матрице судьбы — бесплатно..."
INSTRUCTION_TAROT = "Чтобы я сделала расклад, пришлите, пожалуйста, следующие данные:\n\n— Ваше имя и дату рождения..."
INSTRUCTION_MATRIX = "Чтобы я смогла сделать для вас разбор по матрице судьбы, напишите, пожалуйста, следующие данные:\n\n— Вашу дату рождения..."
RESPONSE_WAIT = "Спасибо, я все получила! Ваша заявка ушла ко мне..."
REVIEW_TEXT = "Если вас устроил расклад или разбор по матрице, для энергообмена обязательно оставьте отзыв на Авито..."
PRIVATE_MESSAGE = "Вы уже получили услугу! Если хотите новый расклад — напишите мне в личку: @zamira_esoteric."
CONTACT_TEXT = "@zamira_esoteric"

# Вспомогательные функции
def clean_text(text: str) -> str:
    return "".join(c for c in text if c.isprintable() or c in "\n\r\t ")

def validate_date(date_text: str) -> bool:
    if not re.match(r"^\d{2}\.\d{2}\.\d{4}$", date_text):
        return False
    try:
        date = datetime.strptime(date_text, "%d.%m.%Y")
        return 1900 <= date.year <= datetime.now().year
    except ValueError:
        return False

async def retry_operation(coro, max_retries=CONFIG["MAX_RETRIES"], delay=CONFIG["RETRY_DELAY"]):
    for attempt in range(max_retries):
        try:
            return await coro
        except Exception as e:
            logger.warning(f"Ошибка в попытке {attempt + 1}: {e}")
            if attempt == max_retries - 1:
                raise
            await asyncio.sleep(delay * (2 ** attempt))

def get_main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Расклад Таро 🃏", callback_data="tarot")],
        [InlineKeyboardButton("Матрица судьбы 🌟", callback_data="matrix")],
        [InlineKeyboardButton("Связь со мной 📩", callback_data="contact")]
    ])

def get_confirm_keyboard(tarot=False):
    return InlineKeyboardMarkup([[InlineKeyboardButton(
        "✅ Подтвердить предысторию" if tarot else "✅ Подтвердить", callback_data="confirm"
    )]])

async def ask_gpt(prompt: str) -> str:
    async with semaphore:
        async def gpt_call():
            response = await openai.ChatCompletion.acreate(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.85,
                max_tokens=CONFIG["OPENAI_MAX_TOKENS"]
            )
            return response.choices[0].message.content.strip()
        return await retry_operation(gpt_call())

async def send_long_message(chat_id: int, message: str, bot):
    parts = [message[i:i + CONFIG["MAX_MESSAGE_LENGTH"]] for i in range(0, len(message), CONFIG["MAX_MESSAGE_LENGTH"])]
    for part in parts:
        if not part.strip():
            continue
        await retry_operation(lambda: bot.send_message(chat_id=chat_id, text=part))
        await asyncio.sleep(1)

async def delayed_response_job(context: ContextTypes.DEFAULT_TYPE):
    chat_id, result, bot = context.job.data
    try:
        await send_long_message(chat_id, clean_text(result), bot)
        await bot.send_message(chat_id=chat_id, text=clean_text(REVIEW_TEXT))
    except Exception as e:
        logger.error(f"Ошибка при отправке: {e}")
        await bot.send_message(chat_id=chat_id, text="Ошибка обработки. Свяжитесь с @zamira_esoteric.")

# Обработчики
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in completed_users:
        await update.message.reply_text(clean_text(PRIVATE_MESSAGE))
        return
    user_data[user_id] = {"type": None, "text": ""}
    await update.message.reply_text(clean_text(WELCOME_TEXT), reply_markup=get_main_keyboard())

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    if user_id in completed_users and query.data in ["tarot", "matrix"]:
        await query.message.reply_text(clean_text(PRIVATE_MESSAGE))
        return

    try:
        if query.data == "tarot":
            user_data[user_id] = {"type": "tarot", "text": ""}
            await query.message.reply_text(clean_text(INSTRUCTION_TAROT), reply_markup=get_confirm_keyboard(tarot=True))
        elif query.data == "matrix":
            user_data[user_id] = {"type": "matrix", "text": ""}
            await query.message.reply_text(clean_text(INSTRUCTION_MATRIX), reply_markup=get_confirm_keyboard())
        elif query.data == "contact":
            await query.message.reply_text(clean_text(CONTACT_TEXT))
        elif query.data == "confirm":
            data = user_data.get(user_id, {})
            if not data.get("type") or not data.get("text", "").strip():
                await query.message.reply_text(clean_text("Вы ещё ничего не написали."))
                return
            if len(data["text"]) < CONFIG["MIN_TEXT_LENGTH_TAROT"] and data["type"] == "tarot":
                await query.message.reply_text("Текст для Таро слишком короткий.")
                return
            if len(data["text"]) < CONFIG["MIN_TEXT_LENGTH_MATRIX"] and data["type"] == "matrix":
                await query.message.reply_text("Текст для матрицы слишком короткий.")
                return
            date_match = re.search(r"\b\d{2}\.\d{2}\.\d{4}\b", data["text"])
            if not date_match or not validate_date(date_match.group()):
                await query.message.reply_text("Неверный формат даты. Используйте ДД.ММ.ГГГГ.")
                return

            await query.message.reply_text(clean_text(RESPONSE_WAIT))
            prompt = PROMPT_TAROT.format(input_text=data["text"]) if data["type"] == "tarot" else PROMPT_MATRIX.format(input_text=data["text"])
            result = await ask_gpt(prompt)
            context.job_queue.run_once(delayed_response_job, CONFIG["DELAY_SECONDS"], data=(query.message.chat.id, result, context.bot))
            completed_users.add(user_id)
            del user_data[user_id]
    except Exception as e:
        logger.error(f"Ошибка в handle_callback: {e}")
        await query.message.reply_text("Ошибка. Свяжитесь с @zamira_esoteric.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.text:
        user_id = update.message.from_user.id
        if user_id in completed_users:
            await update.message.reply_text(clean_text(PRIVATE_MESSAGE))
            return
        if user_id in user_data:
            user_data[user_id]["text"] += "\n" + clean_text(update.message.text)

async def ignore_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Пожалуйста, отправьте только текст.")

# Запуск
if __name__ == "__main__":
    try:
        app = ApplicationBuilder().token(BOT_TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CallbackQueryHandler(handle_callback))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        app.add_handler(MessageHandler(~filters.TEXT & ~filters.COMMAND, ignore_media))
        logger.info("Бот запущен.")
        app.run_polling()
    except Exception as e:
        logger.critical(f"Ошибка запуска: {e}")
        raise