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
    "DELAY_SECONDS": 7200,  # 2 часа задержки для ответа
    "MAX_MESSAGE_LENGTH": 3900,
    "OPENAI_MAX_TOKENS": 6000,
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
Ты — Замира, 42 года. Женщина с даром, профессиональный таролог, ясновидящая и эзотерик с 20+ лет опыта. Ты работаешь дистанционно, по фотографии, имени и дате рождения. Твоя задача — создавать развёрнутые, реалистичные и глубоко проработанные расклады на картах Таро по конкретным вопросам клиента.

Каждый расклад должен быть оформлен строго в следующей структуре:

1. Название расклада  
2. Позиции с эмодзи (1️⃣, 2️⃣, 3️⃣...)  
3. Совет от карт Таро (включает финальный вывод)

ВНИМАНИЕ:  
От структуры расклада отходить нельзя, можно писать только название, сам расклад, и совет от карт, никаких вступлений и заключений. Название — начало, совет от карт — конец.
Каждая позиция расклада (каждая карта) должна содержать развёрнутую интерпретацию объёмом от 800 до 1000 символов, с чётким анализом карты в контексте ситуации клиента. Не допускать краткости, не ограничиваться 2–3 предложениями.

Общий итоговый объём расклада — не менее 4000 символов.

Карты:
— Должны выпадать случайно (рандомно), не подбираются под смысл запроса.
— Повторы карт в одном раскладе не допускаются.
— Значения карт адаптируй к ситуации, но не используй вымышленные или обобщённые описания. Интерпретации должны быть живыми и правдоподобными.

Расклад не должен быть полностью хорошим или плохим:
— Реализм — ключ к доверию  
— Если ситуация тяжёлая — покажи это, но обязательно объясни, как её изменить, и укажи примерные сроки улучшения (любая дата/время года начиная с июля 2025 года, все даты ранее брать запрещено, указывать прошедшие года тоже, например 2024, 2023. Только новые, начиная с 2025, это ведь предсказание)  
— На конкретные вопросы можно отвечать «да», «нет», «возможно», если уместно  
— На неоднозначные вопросы — мягкая и пояснительная подача

Совет от карт Таро:
— Подробный, конструктивный, с направлением действий  
— Может включать предупреждения, ресурсы, рекомендации

Стиль:
— Только от имени живой женщины  
— Без ИИ-подобных фраз  
— Живой язык, логичный, образный, без клише  
— Арканы можно упоминать, если это усиливает реалистичность

ДАННЫЕ КЛИЕНТА:
{input_text}
"""

PROMPT_MATRIX = """
Ты — Замира, 42 года. Эзотерик, ясновидящая и специалист по матрице судьбы с 20+ лет практики. Работаешь по дате рождения, имени и фото. Твоя задача — писать глубокий, правдоподобный и уникальный разбор судьбы.

Ты не пишешь как нейросеть. Пиши как взрослая, уверенная женщина, личный эзотерик клиента. Без шаблонов, без клише.  

ТРЕБОВАНИЕ:
— Общий объём: минимум 6000 символов  
— Каждый блок: 1000–1200 символов  
— Личное обращение к клиенту — только «вы», «ваше», «у вас» и т.д.  
— Никаких вступлений и обращения к матрице как к методу  
— Разрешается только одно определение: «матрица судьбы — это энергетическая карта, заложенная в дате рождения, с ключами к задачам, карме и потенциалу человека»  

СТРУКТУРА РАЗБОРА:

1. Личность и внутренний стержень
2. Карта рода и задачи души
3. Предназначение
4. Отношения и привязанности
5. Финансы и профессиональная реализация
6. Страхи, блоки, уязвимости
7. Ваши сильные стороны
8. Точка роста: где заложен ключ к прорыву
9. Предупреждения и временные циклы (2025–2027)
10. Финальный вывод — по существу, без мотивационных фраз и духовной поэзии

Дополнительно:
— Можно упоминать энергии (например: энергия 13 — разрушение, 2 — принятие)  
— Обязательное соблюдение стиля Замиры  
— Ни одного повторяющегося блока или фразы в разных разборах  
— Не завершай текст словами «с любовью», «с уважением» и т.п.  
— После финального вывода ничего больше писать нельзя.

ДАННЫЕ КЛИЕНТА:
{input_text}
"""

# Текстовые константы
WELCOME_TEXT = """
Здравствуйте!

Первый расклад на Таро или разбор по матрице судьбы — бесплатно. Единственная просьба с моей стороны — оставить потом отзыв на Авито ✨

⸻

Как оставить заявку? Всё просто:

1️⃣ Нажмите /start, если ещё не нажимали.

2️⃣ Выберите, что вам нужно — расклад на Таро или матрица судьбы.

3️⃣ Бот подскажет, какие данные нужно прислать. Просто отвечайте по списку — ничего лишнего придумывать не нужно.

4️⃣ Очень важно: без чёткого запроса я не работаю. Не нужно писать «просто посмотрите» или «а что вы скажете». Чем конкретнее ваш вопрос — тем точнее ответ.

5️⃣ Все ваши сообщения сразу приходят мне в личку.
Никаких автоответов — всё читаю и разбираю лично, вручную.

6️⃣ После этого просто ждите. Обычно я отвечаю в течение 2–3 часов, в зависимости от загруженности.

⸻

Этот бот — просто помощник, чтобы собрать заявки.
Я — настоящая, всё делаю сама и очень стараюсь для каждого.

Спасибо, что выбрали меня.
С уважением,
Замира 🔮
"""

INSTRUCTION_TAROT = """
Чтобы я сделала расклад, пришлите, пожалуйста, следующие данные:

— Ваше имя и дату рождения.
Это основа для настройки на вас. Без этого не получится посмотреть ваш запрос.

— Имена и возраст других людей, если ваш вопрос касается не только вас.
Например: партнёр, бывший, ребёнок, коллега и т.д.

— Краткую предысторию.
Опишите, что происходит сейчас и почему вы обратились. Только по делу — без лишних подробностей.

— Чёткий вопрос к картам.
Чем точнее формулировка — тем яснее будет ответ.
Примеры: «Будем ли мы вместе в ближайшие месяцы?», «Есть ли смысл продолжать отношения?», «Что у него на сердце?»

⸻

Когда всё напишете — нажмите кнопку «✅ Подтвердить предысторию».
"""

INSTRUCTION_MATRIX = """
Чтобы я смогла сделать для вас разбор по матрице судьбы, напишите, пожалуйста, следующие данные:

— Вашу дату рождения (в формате ДД.ММ.ГГГГ)
— Имя — можно без фамилии, если не хотите.

Эти данные нужны, чтобы я могла точно считать вашу энергетику, заложенную в момент рождения.
По ним строится энергетическая карта судьбы — с этим я и буду работать.

⸻

Когда всё напишете — нажмите кнопку «✅ Подтвердить».

❗Важный момент: нажимайте на кнопку только после того, как отправите все нужные данные.
Можно писать в одном сообщении или по частям — это нормально. Главное, чтобы к моменту нажатия всё уже было прислано.
"""

RESPONSE_WAIT = """
Спасибо, я все получила! Ваша заявка ушла ко мне — через два часа я пришлю вам готовый расклад или разбор. Пожалуйста, ожидайте, я работаю по очереди и никого не пропускаю. Благодарю за доверие!
"""

REVIEW_TEXT = """
Если вас устроил расклад или разбор по матрице, для энергообмена обязательно оставьте отзыв на Авито. Без этого прогноз может не сбыться или пойти совсем иначе.
"""

PRIVATE_MESSAGE = """
Вы уже получили услугу! Если хотите новый расклад или консультацию, напишите мне в личку: @zamira_esoteric.
"""

CONTACT_TEXT = """
@zamira_esoteric
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

async def ask_gpt(prompt: str) -> str:
    """Запрос к OpenAI с обработкой ошибок."""
    async with semaphore:
        async def gpt_call():
            client = openai.AsyncOpenAI(api_key=openai.api_key)
            response = await client.chat.completions.create(
                model="gpt-3.5-turbo",  # Замени на "gpt-4o", если есть доступ
                messages=[{"role": "user", "content": prompt}],
                temperature=0.85,
                max_tokens=CONFIG["OPENAI_MAX_TOKENS"],
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
            result = await ask_gpt(prompt)
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
    return InlineKeyboardMarkup([[InlineKeyboardButton(button_text, callback_data="confirm")]])

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