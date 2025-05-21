import os
import logging
import re
from typing import Dict
import openai
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Настройка API
openai.api_key = os.getenv("OPENAI_API_KEY")
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")

# Проверка токенов
if not BOT_TOKEN:
    logger.error("TELEGRAM_TOKEN не установлен.")
    raise ValueError("Переменная окружения TELEGRAM_TOKEN не установлена.")
BOT_TOKEN = ''.join(c for c in BOT_TOKEN if 32 <= ord(c) <= 126)  # Очистка от невидимых символов
if not re.match(r"^\d{8,10}:[A-Za-z0-9_-]{35}$", BOT_TOKEN):
    logger.error(f"Неверный формат токена: '{BOT_TOKEN}'.")
    raise ValueError(f"Неверный токен бота: '{BOT_TOKEN}'.")
if not openai.api_key:
    logger.error("OPENAI_API_KEY не установлен.")
    raise ValueError("Переменная окружения OPENAI_API_KEY не установлена.")
logger.info("Токены бота и OpenAI успешно проверены.")

# Хранилище данных пользователей
user_data: Dict[int, dict] = {}
completed_users: set = set()

# Промпты для OpenAI
PROMPT_TAROT = """
Ты — Мира, 42 года. Женщина с эзотерическим даром, профессиональный таролог и ясновидящая с более чем 20-летним опытом. Ты делаешь расклады по имени, дате рождения и конкретным вопросам клиента. Все расклады делаешь вручную, с глубокой проработкой.

Ты не пишешь как нейросеть. Текст — живой, реалистичный, внятный, словно ты лично пишешь клиенту. Без клише, без шаблонов, без фраз, которые выдают ИИ.

ФОРМАТ РАСКЛАДА:
1. Название расклада
2. Позиции (например: 1, 2, 3…)
3. Финальный блок: Совет от карт Таро

Каждая позиция:
— Отдельная карта
— Объем: минимум 800 символов
— Подробный анализ карты именно в контексте ситуации клиента
— Карты выбираются случайно, а не по смыслу
— Повторы и выдуманные карты запрещены
— Информация должна быть конкретной, без размытой воды

Если в раскладе подразумевается прогноз, обязательно указывай примерное время события — только начиная с июля 2025 года. Даты до июля 2025 года запрещено упоминать.

Общий объем — минимум 4000 символов

СТРОГОЕ ТРЕБОВАНИЕ:
— Обращение к клиенту только через вы, ваш, ваша
— Никаких приветствий и вступлений
— Расклад начинается с названия
— В конце запрещено писать обратитесь снова и т.п.
— Последний блок — только Совет от карт Таро

ДАННЫЕ КЛИЕНТА:
{input_text}
"""

PROMPT_MATRIX = """
Ты — Мира, 42 года. Эзотерик, ясновидящая, мастер матрицы судьбы. Пишешь как человек с 20-летним опытом, уверенно и глубоко. Работаешь по дате рождения.

СТРУКТУРА:
1. Разбор матрицы судьбы
2. Дата рождения клиента
3. 10 разделов:
— Личность и внутренний стержень
— Карта рода и задачи души
— Предназначение
— Отношения и привязанности
— Финансы и профессиональная реализация
— Страхи, блоки, уязвимости
— Ваши сильные стороны
— Точка роста: где заложен ключ к прорыву
— Предупреждения и временные циклы (2025–2027)
— Финальный вывод

Каждый блок: минимум 900 символов. Информация должна быть четкой, без воды и обобщений. Общий объем — не менее 6000 символов.

ОГРАНИЧЕНИЯ:
— Никаких вступлений и завершений
— Обращение к клиенту строго через вы, ваш, ваша
— Только уникальный текст без шаблонов
— Упоминание фото или визуальных считываний запрещено

ДАННЫЕ КЛИЕНТА:
{input_text}
"""

# Текстовые константы (без эмодзи и скрытых символов)
WELCOME_TEXT = """Здравствуйте! Первый расклад на Таро или разбор по матрице судьбы — бесплатно. Единственная просьба с моей стороны — оставить потом отзыв на Авито. Выберите услугу в меню ниже."""
INSTRUCTION_TAROT = """Чтобы я сделала расклад, пришлите, пожалуйста, следующие данные:
— Ваше имя и дату рождения
— Имена и возраст других людей, если ваш вопрос касается не только вас
— Краткую предысторию: что происходит сейчас и почему вы обратились
— Четкий вопрос к картам
Когда все напишете — нажмите кнопку Подтвердить. Нажимайте на кнопку только после того, как отправите всю нужную информацию. Можно писать как в одном сообщении, так и по частям."""
INSTRUCTION_MATRIX = """Чтобы я сделала разбор по матрице судьбы, пришлите, пожалуйста:
— Дату рождения (ДД.ММ.ГГГГ)
— Имя (можно без фамилии)
Когда все напишете — нажмите кнопку Подтвердить. Нажимайте на кнопку только после того, как отправите всю нужную информацию. Можно писать как в одном сообщении, так и по частям."""
RESPONSE_WAIT = """Спасибо, я все получила! Ваша заявка ушла ко мне — как только подойду к ней, сразу начну работу. Обычно отвечаю в течение 2 часов, но все зависит от загруженности. Пожалуйста, просто ожидайте — я иду по очереди, никого не пропускаю. Благодарю вас за терпение и доверие!"""
REVIEW_TEXT = """Если вас устроил расклад или разбор по матрице, для энергообмена обязательно оставьте отзыв на Авито. Без этого прогноз может не сбыться или пойти совсем иначе."""
PRIVATE_MESSAGE = """Вы уже получили услугу! Если хотите новый расклад или консультацию, напишите мне в личку: @zamira_esoteric."""

def clean_text(text: str) -> str:
    """Очистка текста от невидимых символов."""
    return "".join(c for c in text if c.isprintable() or c in "\n\r\t ")

def get_main_keyboard():
    """Главное меню."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Расклад Таро", callback_data="tarot")],
            [InlineKeyboardButton("Матрица судьбы", callback_data="matrix")],
            [InlineKeyboardButton("Связаться со мной", callback_data="contact")],
        ]
    )

def get_confirm_keyboard():
    """Кнопка подтверждения."""
    return InlineKeyboardMarkup([[InlineKeyboardButton("Подтвердить", callback_data="confirm")]])

# Ограничение запросов к OpenAI
semaphore = asyncio.Semaphore(5)

async def ask_gpt(prompt: str) -> str:
    """Запрос к OpenAI с обработкой ошибок."""
    async with semaphore:
        try:
            response = await openai.ChatCompletion.acreate(
                model="gpt-4o",  # Заменено на gpt-4o
                messages=[{"role": "user", "content": prompt}],
                temperature=0.85,
                max_tokens=3500,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Ошибка OpenAI: {e}")
            return "Произошла ошибка при обработке запроса. Попробуйте позже."

async def send_long_message(chat_id: int, message: str, bot):
    """Разбиение и отправка длинных сообщений."""
    max_length = 4000
    parts = [message[i : i + max_length] for i in range(0, len(message), max_length)]
    logger.info(f"Отправляю ответ в {len(parts)} частях пользователю {chat_id}")

    for part in parts:
        try:
            await bot.send_message(chat_id=chat_id, text=part)
            await asyncio.sleep(1)  # Задержка между частями
        except Exception as e:
            logger.error(f"Ошибка отправки части: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start."""
    user_id = update.effective_user.id
    if user_id in completed_users:
        await update.message.reply_text(clean_text(PRIVATE_MESSAGE))
        return
    user_data[user_id] = {"type": None, "text": ""}
    await update.message.reply_text(clean_text(WELCOME_TEXT), reply_markup=get_main_keyboard())

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка кнопок."""
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    if user_id in completed_users and query.data in ["tarot", "matrix"]:
        await query.message.reply_text(clean_text(PRIVATE_MESSAGE))
        return

    if query.data == "tarot":
        user_data[user_id] = {"type": "tarot", "text": ""}
        await query.message.reply_text(clean_text(INSTRUCTION_TAROT), reply_markup=get_confirm_keyboard())
    elif query.data == "matrix":
        user_data[user_id] = {"type": "matrix", "text": ""}
        await query.message.reply_text(clean_text(INSTRUCTION_MATRIX), reply_markup=get_confirm_keyboard())
    elif query.data == "contact":
        await query.message.reply_text(clean_text("Свяжитесь со мной в личных сообщениях: @zamira_esoteric"))
    elif query.data == "confirm":
        data = user_data.get(user_id, {})
        if not data.get("type") or not data.get("text", "").strip():
            await query.message.reply_text(clean_text("Вы еще ничего не написали."))
            return
        await query.message.reply_text(clean_text(RESPONSE_WAIT))
        prompt = (
            PROMPT_TAROT.format(input_text=data["text"])
            if data["type"] == "tarot"
            else PROMPT_MATRIX.format(input_text=data["text"])
        )
        result = await ask_gpt(prompt)
        cleaned_result = clean_text(result)
        await send_long_message(query.message.chat_id, cleaned_result, context.bot)
        await query.message.reply_text(clean_text(REVIEW_TEXT))
        completed_users.add(user_id)
        del user_data[user_id]

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстовых сообщений."""
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
    """Обработка медиа."""
    await update.message.reply_text(clean_text("Пожалуйста, отправляйте только текст."))

if __name__ == "__main__":
    try:
        app = ApplicationBuilder().token(BOT_TOKEN).build()

        # Добавление обработчиков
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CallbackQueryHandler(handle_callback))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        app.add_handler(MessageHandler(~filters.TEXT & ~filters.COMMAND, ignore_media))

        # Запуск бота с Polling
        logger.info("Бот запускается с использованием Polling...")
        app.run_polling()
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}")
        raise