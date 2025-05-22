import os
import logging
import re
from typing import Dict
import asyncio
import openai
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
    JobQueue,
)
from telegram.error import TelegramError
from datetime import datetime
from logging.handlers import RotatingFileHandler

# Настройка логирования
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
    "DELAY_SECONDS": 10,  # 10 секунд задержки для ответа
    "MAX_MESSAGE_LENGTH": 3900,
    "OPENAI_MAX_TOKENS_TAROT": 5000,  # Для Таро — минимум 4000 символов
    "OPENAI_MAX_TOKENS_MATRIX": 7000,  # Для матрицы — минимум 6000 символов
    "OPENAI_MAX_CONCURRENT": 5,
    "MIN_TEXT_LENGTH_TAROT": 100,
    "MIN_TEXT_LENGTH_MATRIX": 15,
    "RETRY_DELAY": 5,
    "MAX_RETRIES": 3,
}

# Настройка API
openai.api_key = os.getenv("OPENAI_API_KEY")
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")

if not BOT_TOKEN or not openai.api_key:
    logger.critical("Отсутствуют токены TELEGRAM_TOKEN или OPENAI_API_KEY.")
    raise ValueError("Токены TELEGRAM_TOKEN и OPENAI_API_KEY должны быть установлены.")

logger.info("Токены бота и OpenAI проверены.")

# Хранилище данных
user_data: Dict[int, dict] = {}
completed_users: set = set()

# Промпты для OpenAI
PROMPT_TAROT = """
Ты — Замира, 42 года, таролог с 20+ лет опыта. Ты делаешь расклады на Таро по конкретным вопросам клиента. Твой стиль — живой, неформальный, как будто ты реальный человек, а не ИИ. Никаких шаблонов, никакой воды.

ТРЕБОВАНИЯ:  
— Общий объём расклада: минимум 4000 символов  
— Каждая позиция (карта): 800–1000 символов  
— Обращение к клиенту: только по имени и на «Вы»  
— Без вступлений — начинай сразу с расклада  
— Без послесловий типа «обращайтесь ещё» — заканчивай на совете от карт  
— Используй стикеры (1️⃣, 2️⃣, 3️⃣ и т.д.) для позиций  
— Пиши подробно, с конкретикой и примерами  
— Указывай примерные даты (с июля 2025 года и дальше)  
— Для вопросов о ближайшем будущем (например, неделя): отвечай чётко — будет/не будет, произойдёт/не произойдёт  
— В совете от карт указывай возможные трудности и что может помешать  

СТРУКТУРА РАСКЛАДА:  
1️⃣ Название расклада  
2️⃣ Позиции с номерами (1️⃣, 2️⃣, 3️⃣...) и интерпретацией карт  
3️⃣ Совет от карт Таро (финальный вывод, даты, трудности)  

Карты выпадают случайно, без повторов в одном раскладе. Интерпретации — живые, реалистичные, под ситуацию клиента. Расклад не должен быть слишком позитивным или негативным — сохраняй баланс. Арканы можно упоминать для правдоподобности.  

ДАННЫЕ КЛИЕНТА:  
{input_text}
"""

PROMPT_MATRIX = """
Ты — Замира, 42 года, эзотерик и специалист по матрице судьбы с 20+ лет опыта. Пишешь глубокие, правдоподобные разборы судьбы по дате рождения и имени. Твой стиль — живой, неформальный, как будто ты реальный человек, а не ИИ. Никаких шаблонов и клише.

ТРЕБОВАНИЯ:
— Общий объём: минимум 6000 символов  
— Каждый блок: 1000–1200 символов  
— Обращение к клиенту: только «Вы», «Ваше», «у Вас»  
— Никаких вступлений, сразу начинай с разбора  
— Никаких послесловий типа «обращайтесь ещё», «с любовью» и т.д., заканчивай на финальном пункте  
— Используй стикеры (1️⃣, 2️⃣, 3️⃣ и т.д.) для нумерации блоков  
— Пиши подробно, с конкретикой, включая даты (начиная с 2025 года) и реалистичные примеры  
— Стиль: живой, как будто ты общаешься с человеком лично, без формальностей  

СТРУКТУРА РАЗБОРА:  
1️⃣ Карма личности и миссия души  
2️⃣ Потенциал и таланты  
3️⃣ Отношения и близкие связи  
4️⃣ Род и кармические задачи семьи  
5️⃣ Учёба, развитие и самореализация  
6️⃣ Материальная сфера и денежный поток  
7️⃣ Энергетика, здоровье, психоэмоциональное состояние  
8️⃣ Судьбоносные выборы и критические моменты  
9️⃣ Духовный рост и смысл жизни  
🔟 Предостережения и поддержка высших сил  

ДАННЫЕ КЛИЕНТА:  
{input_text}
"""

# Текстовые константы
WELCOME_TEXT = """
🌟 Здравствуйте! 🌟
Меня зовут Замира, я таролог и специалист по разбору матрицы судьбы с опытом больше 20 лет. 🌿 Рада приветствовать Вас здесь!
Что я предлагаю бесплатно:
• Один расклад на Таро или разбор по матрице судьбы.
• После услуги прошу оставить отзыв на Авито — это помогает мне в работе.
Как всё работает:
1. Нажмите /start (если ещё не сделали).
2. Выберите, что Вам нужно: Таро или матрицу судьбы.
3. Отправьте данные, следуя подсказкам бота.
4. Напишите чёткий вопрос — это важно для точного ответа.
5. Я лично займусь Вашим запросом, ответ придёт в течение 2–3 часов.
✨ Важно: Бот только собирает заявки, а всю работу делаю я сама. Спасибо, что доверились мне! 🌺
"""

INSTRUCTION_TAROT = """
🌟 Для расклада на Таро мне понадобится: 🌟
✨ Что нужно указать:
• Ваше имя и дата рождения. Например: «Меня зовут Катя, родилась 12.05.1992».
• Имена и возраст других людей (если вопрос про них). Например: «Мой парень — Сергей, ему 30 лет».
• Предыстория. Расскажите, что происходит, почему Вы ко мне обратились. Например: «Мы с Сергеем поссорились неделю назад, он ушёл, а я не знаю, что делать».
• Чёткий вопрос к картам. Например: «Будем ли мы с ним снова вместе?» или «Что ждёт меня в работе в ближайшие месяцы?».
🌿 Как отправить данные:
Вы можете написать всё сразу в одном сообщении или отправлять по частям, подряд. Главное — не торопитесь с кнопкой!
• Например, сначала: «Меня зовут Катя, 12.05.1992».
• Потом: «Мой парень — Сергей, 30 лет».
• И наконец: «Мы поссорились неделю назад, он ушёл. Вопрос: Будем ли мы вместе?»
❗ Самое важное: Нажимайте кнопку «✅ Подтвердить предысторию» только после того, как отправите ВСЁ: своё имя, дату рождения, предысторию и вопрос (плюс данные других людей, если они есть).
Пример полного запроса в одном сообщении:
«Меня зовут Катя, родилась 12.05.1992. Мой парень — Сергей, 30 лет. Мы поссорились неделю назад, он ушёл, я не знаю, что делать. Вопрос: Будем ли мы снова вместе?»
Или по частям:
1. «Катя, 12.05.1992»
2. «Сергей, 30 лет»
3. «Поссорились неделю назад, он ушёл. Вопрос: Будем ли мы вместе?»
Когда всё напишете, жмите «✅ Подтвердить предысторию». Я получу Ваш запрос и начну работать. Спасибо за доверие! 🌺
"""

INSTRUCTION_MATRIX = """
🌟 Для разбора по матрице судьбы мне нужно: 🌟
✨ Что указать:
• Ваша дата рождения. Например: «Я родилась 25.07.1988».
• Ваше имя.
Это нужно, чтобы я могла построить Вашу энергетическую карту и рассказать, что заложено в Вашей судьбе. Ничего сложного, просто имя и дата!
🌿 Как отправить данные:
Можете написать всё сразу в одном сообщении или по отдельности, подряд. Главное — не спешите с кнопкой подтверждения!
• Например, сначала: «Оля».
• Потом: «25.07.1988».
• Или сразу: «Оля, 25.07.1988».
❗ Самое важное: Нажимайте кнопку «✅ Подтвердить» только после того, как напишете и имя, и дату рождения. Убедитесь, что всё верно!
Пример запроса в одном сообщении:
«Меня зовут Оля, родилась 25.07.1988».
Или по частям:
1. «Оля»
2. «25.07.1988»
Когда всё отправите, жмите «✅ Подтвердить». Я начну разбирать Вашу матрицу! 🌺
"""

RESPONSE_WAIT = """
🌟 Спасибо за заявку! 🌟
Я получила Ваши данные и скоро начну работу. Ответ пришлю в течение 2–3 часов. Подождите немного, пожалуйста! ✨
"""

REVIEW_TEXT = """
🌿 Если моя работа Вам понравилась, прошу Вас обязательно оставить отзыв на Авито для энергообмена. Это важно: без отзыва предсказание может не сбыться или даже проиграться совсем наоборот! 🌟
[Оставить отзыв](https://www.avito.ru/user/review?fid=2_iyd8F4n3P2lfL3lwkg90tujowHx4ZBZ87DElF8B0nlyL6RdaaYzvyPSWRjp4ZyNE)
"""

PRIVATE_MESSAGE = """
✨ Вы уже получили услугу! Если захотите ещё один расклад или консультацию, пишите мне напрямую: @zamira_esoteric. 🌺
"""

CONTACT_TEXT = """
🌟 Мои контакты: @zamira_esoteric 🌟
"""

# Утилитарные функции
def clean_text(text: str) -> str:
    try:
        return "".join(c for c in text if c.isprintable() or c in "\n\r\t ")
    except Exception as e:
        logger.error(f"Ошибка очистки текста: {e}")
        return text

def validate_date(date_text: str) -> bool:
    if not re.match(r"^\d{2}\.\d{2}\.\d{4}$", date_text):
        return False
    try:
        date = datetime.strptime(date_text, "%d.%m.%Y")
        if date.year < 1900 or date > datetime.now():
            return False
        return True
    except ValueError:
        return False

async def retry_operation(coro, max_retries=CONFIG["MAX_RETRIES"], delay=CONFIG["RETRY_DELAY"]):
    for attempt in range(max_retries):
        try:
            return await coro()
        except Exception as e:
            logger.warning(f"Попытка {attempt + 1} не удалась: {e}")
            if attempt == max_retries - 1:
                raise
            await asyncio.sleep(delay * (2 ** attempt))

# Ограничение параллельных запросов к OpenAI
semaphore = asyncio.Semaphore(CONFIG["OPENAI_MAX_CONCURRENT"])

async def ask_gpt(prompt: str, max_tokens: int) -> str:
    """Запрос к OpenAI с обработкой ошибок и динамическим max_tokens."""
    async with semaphore:
        async def gpt_call():
            client = openai.AsyncOpenAI(api_key=openai.api_key)
            response = await client.chat.completions.create(
                model="gpt-4o",  # Используем gpt-4o
                messages=[{"role": "user", "content": prompt}],
                temperature=0.85,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content.strip()
        
        try:
            return await retry_operation(gpt_call)
        except Exception as e:
            logger.error(f"Ошибка OpenAI: {e}")
            return "Произошла ошибка при генерации ответа. Попробуйте позже или свяжитесь с @zamira_esoteric."

async def send_long_message(chat_id: int, message: str, bot):
    parts = [message[i:i + CONFIG["MAX_MESSAGE_LENGTH"]] for i in range(0, len(message), CONFIG["MAX_MESSAGE_LENGTH"])]
    logger.info(f"Отправляю {len(parts)} частей пользователю {chat_id}")
    
    for part in parts:
        if not part.strip():
            continue
        async def send_part():
            await bot.send_message(chat_id=chat_id, text=part)
            await asyncio.sleep(1)
        
        try:
            await retry_operation(send_part)
        except Exception as e:
            logger.error(f"Ошибка отправки части сообщения: {e}")
            await bot.send_message(chat_id=chat_id, text="Ошибка при отправке. Свяжитесь с @zamira_esoteric.")

async def delayed_response_job(context: ContextTypes.DEFAULT_TYPE):
    """Функция для отложенной отправки ответа."""
    chat_id, result, bot = context.job.data
    logger.info(f"Выполняю отложенную задачу для {chat_id}")
    try:
        cleaned_result = clean_text(result)
        await send_long_message(chat_id, cleaned_result, bot)
        await bot.send_message(chat_id=chat_id, text=clean_text(REVIEW_TEXT))
    except Exception as e:
        logger.error(f"Ошибка в delayed_response_job: {e}")
        await bot.send_message(chat_id=chat_id, text="Ошибка при отправке ответа. Свяжитесь с @zamira_esoteric.")

# Обработчики
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in completed_users:
        await update.message.reply_text(clean_text(PRIVATE_MESSAGE))
        return
    user_data[user_id] = {"type": None, "text": ""}
    await update.message.reply_text(clean_text(WELCOME_TEXT), reply_markup=get_main_keyboard())
    logger.info(f"Пользователь {user_id} начал взаимодействие.")

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
        elif query.data == "cancel":
            if user_id in user_data:
                del user_data[user_id]
            await query.message.reply_text("Ваш запрос отменён. Вы можете начать заново.", reply_markup=get_main_keyboard())
        elif query.data == "confirm":
            data = user_data.get(user_id, {})
            if not data.get("type") or not data.get("text", "").strip():
                await query.message.reply_text(clean_text("Вы ещё ничего не написали."))
                return
            if len(data["text"]) < CONFIG["MIN_TEXT_LENGTH_TAROT"] and data["type"] == "tarot":
                await query.message.reply_text(clean_text("Текст для Таро слишком короткий. Напишите больше."))
                return
            if len(data["text"]) < CONFIG["MIN_TEXT_LENGTH_MATRIX"] and data["type"] == "matrix":
                await query.message.reply_text(clean_text("Текст для матрицы слишком короткий. Напишите больше."))
                return

            date_match = re.search(r"\b\d{2}\.\d{2}\.\d{4}\b", data["text"])
            if not date_match or not validate_date(date_match.group()):
                await query.message.reply_text(clean_text("Неверный формат даты или дата не существует. Используйте ДД.ММ.ГГГГ."))
                return

            await query.message.reply_text(clean_text(RESPONSE_WAIT))
            prompt = (
                PROMPT_TAROT.format(input_text=data["text"]) if data["type"] == "tarot"
                else PROMPT_MATRIX.format(input_text=data["text"])
            )
            max_tokens = (
                CONFIG["OPENAI_MAX_TOKENS_TAROT"] if data["type"] == "tarot"
                else CONFIG["OPENAI_MAX_TOKENS_MATRIX"]
            )
            result = await ask_gpt(prompt, max_tokens)
            if not context.job_queue:
                logger.error("JobQueue не инициализирован!")
                await query.message.reply_text("Ошибка бота. Свяжитесь с @zamira_esoteric.")
                return
            context.job_queue.run_once(delayed_response_job, CONFIG["DELAY_SECONDS"], data=(query.message.chat.id, result, context.bot))
            completed_users.add(user_id)
            del user_data[user_id]
            logger.info(f"Заявка пользователя {user_id} запланирована.")
    except Exception as e:
        logger.error(f"Ошибка в handle_callback: {e}")
        await query.message.reply_text("Ошибка обработки запроса. Свяжитесь с @zamira_esoteric.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.text:
        user_id = update.message.from_user.id
        if user_id in completed_users:
            await update.message.reply_text(clean_text(PRIVATE_MESSAGE))
            return
        if user_id in user_data:
            cleaned_text = clean_text(update.message.text)
            user_data[user_id]["text"] += "\n" + cleaned_text
            logger.debug(f"Сообщение от {user_id}: {cleaned_text}")

async def ignore_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(clean_text("Пожалуйста, отправляйте только текст."))

# Клавиатуры
def get_main_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Расклад Таро 🃏", callback_data="tarot")],
            [InlineKeyboardButton("Матрица судьбы 🌟", callback_data="matrix")],
            [InlineKeyboardButton("Связь со мной 📩", callback_data="contact")],
        ]
    )

def get_confirm_keyboard(tarot=False):
    button_text = "✅ Подтвердить предысторию" if tarot else "✅ Подтвердить"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(button_text, callback_data="confirm")],
            [InlineKeyboardButton("❌ Отменить запрос", callback_data="cancel")],
        ]
    )

# Запуск бота
if __name__ == "__main__":
    try:
        app = ApplicationBuilder().token(BOT_TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CallbackQueryHandler(handle_callback))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        app.add_handler(MessageHandler(~filters.TEXT & ~filters.COMMAND, ignore_media))
        logger.info("Бот запускается...")
        app.run_polling()
    except Exception as e:
        logger.critical(f"Ошибка запуска: {e}")
        raise