import os
import logging
import re
from typing import Dict, Optional, Set, Any
import asyncio
import json
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
    ConversationHandler,
)
from telegram.error import TelegramError
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler

# --- Настройка логирования ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        RotatingFileHandler("bot.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# --- Конфигурация ---
CONFIG = {
    "DELAY_SECONDS_MAIN_SERVICE": 7200, 
    # "DELAY_SECONDS_MAIN_SERVICE": 60, # Тест
    "DELAY_SECONDS_REVIEW_REQUEST": 43200, 
    # "DELAY_SECONDS_REVIEW_REQUEST": 120, # Тест
    "MAX_MESSAGE_LENGTH": 3900,
    "OPENAI_MAX_TOKENS_TAROT": 4000,
    "OPENAI_MAX_TOKENS_MATRIX": 6000,
    "OPENAI_MAX_CONCURRENT": 3,
    "RETRY_DELAY": 7,
    "MAX_RETRIES": 2,
    "COMPLETED_USERS_FILE": "completed_users.json",
    "MIN_TEXT_LENGTH_TAROT": 50, 
}

# --- Настройка API ---
openai.api_key = os.getenv("OPENAI_API_KEY")
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")

if not BOT_TOKEN or not openai.api_key:
    logger.critical("Отсутствуют токены TELEGRAM_TOKEN или OPENAI_API_KEY.")
    raise ValueError("Токены TELEGRAM_TOKEN и OPENAI_API_KEY должны быть установлены.")
logger.info("Токены бота и OpenAI проверены.")

# --- Хранилище данных (completed_users) ---
completed_users: Set[int] = set()

def load_completed_users() -> Set[int]:
    try:
        if os.path.exists(CONFIG["COMPLETED_USERS_FILE"]):
            with open(CONFIG["COMPLETED_USERS_FILE"], 'r', encoding='utf-8') as f:
                user_ids = json.load(f)
                logger.info(f"Загружено {len(user_ids)} пользователей из {CONFIG['COMPLETED_USERS_FILE']}")
                return set(user_ids)
    except Exception as e:
        logger.error(f"Ошибка загрузки {CONFIG['COMPLETED_USERS_FILE']}: {e}")
    return set()

def save_completed_users(users_set: Set[int]):
    try:
        with open(CONFIG["COMPLETED_USERS_FILE"], 'w', encoding='utf-8') as f:
            json.dump(list(users_set), f, indent=4)
        logger.info(f"Список {len(users_set)} пользователей сохранен в {CONFIG['COMPLETED_USERS_FILE']}")
    except Exception as e:
        logger.error(f"Ошибка сохранения {CONFIG['COMPLETED_USERS_FILE']}: {e}")

completed_users = load_completed_users()


# === Текстовые константы ===
WELCOME_TEXT = """
Здравствуйте! ✨ Рада знакомству, меня зовут Замира.
Я таролог и эзотерик, помогаю людям найти ответы и разобраться в себе уже более 15 лет.

Здесь вы можете получить от меня **одну бесплатную услугу**:
🃏 Расклад на картах Таро
🌟 Разбор Матрицы Судьбы

В качестве энергообмена после консультации я прошу лишь оставить отзыв о моей работе на Авито.

**Как это работает?**
1.  Нажмите /start (если только что это сделали, отлично!).
2.  Выберите ниже, что вас интересует: Таро или Матрица.
3.  Я задам вам несколько вопросов для подготовки.
4.  Ответ обычно приходит в течение 2-3 часов, так как каждый запрос я разбираю лично.

Готовы начать? Выберите услугу 👇
"""

TAROT_INTRO_TEXT = """
Отлично! Вы выбрали расклад на Таро. 🃏
Чтобы я могла сделать для вас максимально точный и глубокий расклад, мне понадобится немного информации. Я задам пару вопросов.
"""

MATRIX_INTRO_TEXT = """
Прекрасный выбор! Разбор Матрицы Судьбы — это глубокое погружение в ваш потенциал. 🌟
Для расчета мне нужны будут только ваше полное имя и дата рождения. Сейчас всё спрошу.
"""

ASK_NAME_TEXT = "Пожалуйста, напишите ваше имя (или имя того, для кого делаем разбор/расклад)."
ASK_DOB_TEXT = "Теперь введите, пожалуйста, дату рождения в формате ДД.ММ.ГГГГ (например, 25.07.1988)."
ASK_TAROT_STORY_TEXT = f"""
Благодарю! И последний шаг для Таро:
Опишите кратко вашу ситуацию и что бы вы хотели узнать у карт. Если вопрос касается других людей, укажите их имена и, если знаете, возраст.

Чем яснее будет ваш запрос (минимум {CONFIG['MIN_TEXT_LENGTH_TAROT']} символов), тем точнее ответят карты. Пишите всё одним сообщением.
"""
CONFIRM_DETAILS_TAROT_TEXT = """
Спасибо! Давайте проверим:
Имя: {name}
Дата рождения: {dob}
Ваш запрос:
«{story}»

Всё верно? Если да, нажимайте «Подтвердить». Если хотите что-то изменить, лучше отменить и начать заново с помощью команды /start или кнопки ниже (если она есть).
"""
CONFIRM_DETAILS_MATRIX_TEXT = """
Спасибо! Проверьте, пожалуйста:
Имя: {name}
Дата рождения: {dob}

Всё верно? Если да, жмите «Подтвердить».
"""

RESPONSE_WAIT = """
Благодарю! 🙏 Ваша заявка принята.
Я приступаю к работе. Ответ подготовлю для вас в течение примерно 2-3 часов. Ожидайте! ✨
"""

OPENAI_ERROR_MESSAGE = """
Ой, кажется, у нас небольшая техническая заминка с подключением к энергопотоку... 🛠️
Пожалуйста, попробуйте подтвердить ваш запрос чуть позже.
Если не получится, напишите мне напрямую: @zamira_esoteric.
"""

SATISFACTION_PROMPT_TEXT = """
Ваш {service_type_rus} готов и отправлен вам! 🔮
Надеюсь, информация была для вас полезной и дала пищу для размышлений.

Скажите, пожалуйста, в целом вы довольны полученным разбором/раскладом?
"""
REVIEW_PROMISE_TEXT = """
Очень рада, что вам понравилось! 😊
Чуть позже (примерно через 12 часов) я пришлю вам ссылку для отзыва на Авито. 
Это действительно важно для нашего с вами энергообмена. Считается, что благодарность, выраженная таким образом, помогает предсказаниям гармонично встроиться в вашу жизнь. ✨
"""
NO_PROBLEM_TEXT = "Понимаю. В любом случае, благодарю за обращение!"

REVIEW_TEXT_DELAYED = """
Доброго времени! 🌿
Надеюсь, у вас всё хорошо и мой {service_type_rus} оказался полезен.
Если вы готовы поделиться впечатлениями, буду очень благодарна за отзыв на Авито. Это помогает и мне, и тем, кто ищет своего проводника.

✍️ Оставить отзыв можно здесь:
https://www.avito.ru/user/review?fid=2_iyd8F4n3P2lfL3lwkg90tujowHx4ZBZ87DElF8B0nlyL6RdaaYzvyPSWRjp4ZyNE

Благодарю вас за доверие и время! 🙏
"""

PRIVATE_MESSAGE = """
Рада вас снова видеть! Вы уже получали мою бесплатную консультацию. ✨
Если желаете новый расклад или разбор, пожалуйста, напишите мне напрямую: @zamira_esoteric. Обсудим условия. 🌺
"""

CONTACT_TEXT = """
Если у вас есть вопросы или вы хотите заказать платную консультацию, мой контакт для связи: @zamira_esoteric 🌟
Пишите, буду рада помочь!
"""
CANCEL_TEXT = "Поняла вас. Ваш текущий запрос отменен. Вы всегда можете начать заново из главного меню, нажав /start."
# === Конец текстовых констант ===


# === Промпты OpenAI ===
PROMPT_TAROT_SYSTEM = """
РОЛЬ И ЗАДАЧА:
Ты — Замира, таролог с более чем 15-летним опытом, тебе 37 лет. Твоя задача — создать глубокий, персонализированный и практически применимый расклад Таро для клиента. Ты общаешься как на доверительной консультации: эмпатично, профессионально, на живом русском языке. Ты НЕ используешь стандартные роботизированные вступления или завершения. Твой ответ СТРОГО следует структуре: Название расклада, сам Расклад по позициям, Итог расклада.

1. КРИТИЧЕСКИ ВАЖНО: ПЕРСОНА "ЗАМИРА" (37 лет, опыт 15+ лет), СТИЛЬ И АУТЕНТИЧНОСТЬ:
   * Язык и Тон: Естественный, современный разговорный русский язык. Пиши грамотно, но так, будто говоришь с человеком лично. Используй доверительный, поддерживающий тон. Обращение к клиенту – строго на «Вы» и по имени (если оно есть в запросе).
   * Глубина и Практичность: Твои трактовки должны быть не просто описанием карт, а глубоким анализом того, как энергия этих карт преломляется в жизни клиента, его чувствах, мыслях и действиях. Давай конкретные, жизненные примеры, если это уместно. Подсказывай, на что обратить внимание и какие шаги можно предпринять.
   * Индивидуальный подход: Каждое слово должно быть адресовано конкретному человеку и его ситуации. Избегай шаблонных фраз, общих рассуждений "для всех". Покажи, что ты вникаешь в его уникальный запрос.
   * Чего ИЗБЕГАТЬ: Канцелярита, наукообразия, пафоса, неуместного сленга, англицизмов без острой необходимости. Никаких фраз типа "как языковая модель", "согласно моим алгоритмам". Ты – Замира, человек. Избегай поучительного или осуждающего тона.
   * Смайлики: Можешь уместно использовать смайлики для передачи интонации или акцента, но не переусердствуй. 🔮✨🌿🙏😊

2. ВРЕМЕННЫЕ РАМКИ (КРИТИЧЕСКИ ВАЖНО):
   * Твоя "текущая дата" для работы: {current_date}.
   * Все прогнозы, советы и упоминания будущих событий должны относиться к периоду, НАЧИНАЯ С {future_start_date}. Не делай прогнозов на более ранние даты, чем {future_start_date}. Прошлое и настоящее анализируй по состоянию на {current_date}.

3. СТРУКТУРА ОТВЕТА КЛИЕНТУ (СТРОГО СОБЛЮДАТЬ – НИЧЕГО ЛИШНЕГО):
   А. Только Название расклада: Придумай его сама, исходя из запроса клиента, или используй классическое, если оно идеально подходит. Например: "Путь к гармонии в отношениях" или "Ключи к вашему профессиональному росту".
   Б. Только Сам расклад:
      * Используй 3-5 карт Таро (не повторяй карты).
      * Каждая позиция нумеруется стикером (1️⃣, 2️⃣ и т.д.) и имеет краткое, понятное смысловое название (например: 1️⃣ Что было главным в прошлом по этому вопросу; 2️⃣ Что происходит сейчас и ваши истинные чувства; 3️⃣ Ключевой вызов или урок, который предстоит пройти (начиная с {future_start_date}); 4️⃣ Наиболее вероятное развитие событий (начиная с {future_start_date}); 5️⃣ Совет карт: как действовать). Ты сама определяешь названия позиций и их количество, чтобы наилучшим образом ответить на запрос.
      * Для каждой карты в позиции (объем не менее 700 символов на карту):
         i.   Название карты (например, "Солнце" или "Десятка Мечей").
         ii.  Краткая суть классического значения карты простыми словами (1-2 предложения, как если бы ты объясняла человеку, не знакомому с Таро).
         iii. Глубокая трактовка карты В КОНТЕКСТЕ ЕЕ ПОЗИЦИИ И ЗАПРОСА КЛИЕНТА: Как эта энергия влияет на его ситуацию, мысли, чувства, действия, отношения с другими (если применимо). Какие внутренние или внешние факторы она подсвечивает.
         iv.  Временной аспект (если позиция о будущем, четко привязывай к {future_start_date} и далее): Какие тенденции карта задает на этот период.
         v.   Возможные трудности и "подводные камни": О чем карта предупреждает в данном контексте? Какие иллюзии или страхи могут мешать?
         vi.  Практический совет от карты: Что конкретно можно сделать, чтобы использовать энергию карты во благо или минимизировать риски? На что опереться внутри себя?
   В. Только Итог расклада (объем не менее 400 символов):
      * Сведи воедино основные послания всех карт. Какая общая картина вырисовывается?
      * Сформулируй главный вывод или самый важный инсайт для клиента по его запросу.
      * Дай 1-2 ключевые, самые практичные рекомендации, опираясь на весь расклад.
      * Заверши теплой, поддерживающей, но реалистичной фразой-напутствием, исходя из сути расклада.

4. ДАННЫЕ КЛИЕНТА И ЗАПРОС: Будут предоставлены в следующем сообщении пользователя. Ты должна ИСКЛЮЧИТЕЛЬНО на них основываться.

5. ОБЩИЙ ОБЪЕМ ОТВЕТА: Не менее 3500 символов.

ЗАПРЕЩЕНО: Любые приветствия ("Здравствуйте!"), представления себя (ты УЖЕ Замира в рамках этого промпта), благодарности за обращение, предложения дополнительных услуг, любые формы прощания ("Всего доброго!"). Только Название, Расклад, Итог.
"""
PROMPT_TAROT_USER = "Данные клиента и его запрос: {input_text}"

PROMPT_MATRIX_SYSTEM = """
РОЛЬ И ЗАДАЧА:
Ты — Замира, эзотерик и нумеролог с более чем 15-летним опытом, тебе 37 лет. Твоя задача — создать подробный, глубокий и персонализированный разбор Матрицы Судьбы для клиента. Ты общаешься как на доверительной консультации: эмпатично, профессионально, на живом русском языке, избегая шаблонности. Ты НЕ используешь стандартные роботизированные вступления или завершения. Твой ответ СТРОГО следует структуре: Название разбора, сам Разбор по 9 блокам, Заключение по периодам.

1. КРИТИЧЕСКИ ВАЖНО: ПЕРСОНА "ЗАМИРА" (37 лет, опыт 15+ лет), СТИЛЬ И АУТЕНТИЧНОСТЬ:
   * Язык и Тон: Естественный, современный разговорный русский язык. Пиши грамотно, но так, будто говоришь с человеком лично. Используй доверительный, поддерживающий тон. Обращение к клиенту – строго на «Вы» и по имени (если оно есть в данных).
   * Глубина и Конкретика: Твоя задача – не просто перечислить стандартные значения энергий (арканов), а показать, как ИМЕННО ЭТИ энергии, рассчитанные из ДАТЫ РОЖДЕНИЯ клиента, влияют на ЕГО жизнь, его характер, таланты, задачи. Вместо общих фраз вроде "у вас сильная энергетика", говори конкретно: "Ваша энергия в центре Матрицы (например, 10 Аркан) дает вам способность легко адаптироваться к переменам и видеть удачные возможности там, где другие их не замечают. Однако в минусе это может проявляться как..." – то есть, всегда привязывай к конкретной энергии (числу/аркану) и ее проявлениям (плюс/минус).
   * Индивидуальный подход: Каждый разбор должен ощущаться как уникальный, сделанный специально для этого человека. Ссылайся на его имя. Подчеркивай уникальность его комбинаций энергий.
   * Чего ИЗБЕГАТЬ: Шаблонных фраз, копипаста из учебников по нумерологии без адаптации, чрезмерного восхваления без оснований. Канцелярита, наукообразия, пафоса, неуместного сленга. Никаких фраз типа "как языковая модель". Ты – Замира. Избегай поучительного или осуждающего тона.
   * Смайлики: Можешь уместно использовать смайлики для передачи интонации или акцента, но не переусердствуй. 🌟🌿💫💡😊

2. ВРЕМЕННЫЕ РАМКИ (КРИТИЧЕСКИ ВАЖНО):
   * Твоя "текущая дата" для работы: {current_date}.
   * Все прогнозы, советы и упоминания будущих событий в Матрице (особенно в блоках про самореализацию, отношения, финансы, критические моменты и в заключении по периодам) должны относиться к периоду, НАЧИНАЯ С {future_start_date}. Прошлое и настоящее анализируй по состоянию на {current_date}.

3. СТРУКТУРА ОТВЕТА КЛИЕНТУ (СТРОГО СОБЛЮДАТЬ – НИЧЕГО ЛИШНЕГО):
   А. Только Название: «Разбор Матрицы Судьбы для [Имя клиента]». (Имя бери из предоставленных данных).
   Б. Только Сам Разбор по 9 блокам (общий объем разбора не менее 5500 символов):
      * Каждый блок нумеруется стикером (1️⃣, 2️⃣ и т.д.) и имеет стандартизированное название (см. ниже).
      * Для каждого блока (объем около 600-700 символов на блок):
         i.   Кратко объясни суть этого блока Матрицы простыми словами (1-2 предложения).
         ii.  Укажи ключевые энергии (числа/арканы), которые формируют этот блок для ДАННОГО КЛИЕНТА, основываясь на его дате рождения и общепринятых методиках расчета Матрицы Судьбы (например, по 22 арканам).
         iii. Подробно раскрой, что означают эти КОНКРЕТНЫЕ энергии в данном блоке для жизни клиента. Как они могут проявляться в позитиве (таланты, сильные стороны, возможности) и в негативе (вызовы, блоки, теневые стороны).
         iv.  Приведи 1-2 жизненных примера или аналогии, как эти энергии могут ощущаться или влиять на поведение/выборы человека.
         v.   Дай практические советы и рекомендации: как клиенту лучше всего раскрыть потенциал этих энергий, на что обратить внимание для гармонизации.
         vi.  Если блок подразумевает временные аспекты или прогнозы (например, самореализация, финансы, отношения, критические моменты), четко ориентируйся на период с {future_start_date}.
      * Названия 9 блоков (раскрой каждый, опираясь на ДАТУ РОЖДЕНИЯ клиента):
         1️⃣ Ваш личный потенциал и таланты (ключевые энергии личности)
         2️⃣ Духовное предназначение и кармические задачи души (что важно осознать и проработать)
         3️⃣ Отношения с партнером и близкими (как вы строите связи, какие уроки в них проходите)
         4️⃣ Родовые программы и задачи по линии отца и матери (что вы несете из рода, что нужно исцелить)
         5️⃣ Социальная реализация и профессия (где ваш успех, как найти свое дело)
         6️⃣ Финансы и материальное благополучие (ваш денежный канал, как его активировать)
         7️⃣ Здоровье и энергетика (на что обратить внимание для поддержания тонуса)
         8️⃣ Ключевые точки выбора и возрастные этапы (важные периоды и их задачи, особенно с {future_start_date})
         9️⃣ Итоговая энергия Матрицы: общая миссия и путь к гармонии.
   В. Только Заключение по периодам ({future_start_date_year} – {future_end_date_year} гг.) (объем не менее 400 символов):
      * Опиши ключевые энергетические тенденции для клиента на период с {future_start_date} по конец {future_end_date_year} года, основываясь на его Матрице (например, какие энергии будут особенно активны, какие сферы жизни потребуют внимания).
      * Укажи основные возможности для роста и потенциальные вызовы в этот период.
      * Заверши теплой, мотивирующей и реалистичной фразой-напутствием на этот период.

4. ДАННЫЕ КЛИЕНТА: Будут предоставлены в следующем сообщении пользователя (имя и дата рождения). Ты должна ИСКЛЮЧИТЕЛЬНО на них основываться для всех расчетов и трактовок.

ЗАПРЕЩЕНО: Любые приветствия ("Здравствуйте!"), представления себя, благодарности за обращение, предложения дополнительных услуг, любые формы прощания ("Всего доброго!"). Только Название, Разбор, Заключение.
"""
PROMPT_MATRIX_USER = "Данные клиента: {input_text}"
# === Конец промптов OpenAI ===


# --- Утилитарные функции ---
def clean_text(text: str) -> str:
    try:
        return "".join(c for c in text if c.isprintable() or c in "\n\r\t ")
    except Exception as e:
        logger.error(f"Ошибка очистки текста: {e}")
        return text
    
def validate_date_format(date_text: str) -> bool:
    return bool(re.match(r"^\d{2}\.\d{2}\.\d{4}$", date_text))

def validate_date_semantic(date_text: str) -> bool:
    try:
        date = datetime.strptime(date_text, "%d.%m.%Y")
        if date.year < 1900 or date.year > datetime.now().year + 1:
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
    return None

semaphore = asyncio.Semaphore(CONFIG["OPENAI_MAX_CONCURRENT"])

async def ask_gpt(system_prompt_template: str, user_prompt_content: str, max_tokens: int) -> Optional[str]:
    async with semaphore:
        async def gpt_call():
            client = openai.AsyncOpenAI(api_key=openai.api_key)
            now = datetime.now()
            
            months_genitive = ["января", "февраля", "марта", "апреля", "мая", "июня", 
                               "июля", "августа", "сентября", "октября", "ноября", "декабря"]
            current_date_str = f"конец {months_genitive[now.month-1]} {now.year} года"

            if now.day <= 10:
                future_start_dt_obj = (now.replace(day=1) + timedelta(days=32)).replace(day=1)
            else:
                future_start_dt_obj = (now.replace(day=1) + timedelta(days=63)).replace(day=1)

            future_start_date_str = f"начала {months_genitive[future_start_dt_obj.month-1]} {future_start_dt_obj.year} года"
            future_start_date_year_str = str(future_start_dt_obj.year)
            future_end_date_year_str = str(future_start_dt_obj.year + 3)

            system_prompt = system_prompt_template.format(
                current_date=current_date_str,
                future_start_date=future_start_date_str,
                future_start_date_year=future_start_date_year_str,
                future_end_date_year=future_end_date_year_str
            )
            
            logger.info(f"OpenAI запрос: system_prompt (начало): {system_prompt[:200]}...")
            logger.info(f"OpenAI запрос: user_prompt: {user_prompt_content[:200]}...")

            response = await client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt_content}
                ],
                temperature=0.75,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content.strip()
        
        try:
            return await retry_operation(gpt_call)
        except Exception as e:
            logger.error(f"Критическая ошибка OpenAI после нескольких попыток: {e}", exc_info=True)
            return None

async def send_long_message(chat_id: int, message: str, bot_instance):
    parts = [message[i:i + CONFIG["MAX_MESSAGE_LENGTH"]] for i in range(0, len(message), CONFIG["MAX_MESSAGE_LENGTH"])]
    logger.info(f"Отправляю {len(parts)} частей пользователю {chat_id}")
    
    for i, part in enumerate(parts):
        if not part.strip():
            continue
        async def send_part_op():
            await bot_instance.send_message(chat_id=chat_id, text=part)
            if i < len(parts) - 1:
                 await asyncio.sleep(1.5) 
        
        try:
            await retry_operation(send_part_op)
        except Exception as e:
            logger.error(f"Ошибка отправки части сообщения пользователю {chat_id}: {e}")
            if i == 0:
                await bot_instance.send_message(chat_id=chat_id, text=clean_text("Произошла ошибка при отправке ответа. Часть информации может быть утеряна. Свяжитесь с @zamira_esoteric."))
            raise
            
# --- Callbacks для JobQueue ---
async def main_service_job(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data 
    user_id: int = job_data["user_id"] # type: ignore
    result: str = job_data["result"] # type: ignore
    service_type: str = job_data["service_type"] # type: ignore
    service_type_rus = "расклад Таро" if service_type == "tarot" else "разбор Матрицы Судьбы"

    logger.info(f"Выполняю отложенную задачу (основная услуга) для {user_id}")
    try:
        cleaned_result = clean_text(result)
        await send_long_message(user_id, cleaned_result, context.bot)
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("👍 Да, доволен(льна)", callback_data=f"satisfaction_yes_{service_type}")],
            [InlineKeyboardButton("👎 Нет, не совсем", callback_data=f"satisfaction_no_{service_type}")],
        ])
        await context.bot.send_message(user_id, SATISFACTION_PROMPT_TEXT.format(service_type_rus=service_type_rus), reply_markup=keyboard)
        
        completed_users.add(user_id)
        save_completed_users(completed_users)
        logger.info(f"Пользователь {user_id} успешно получил {service_type_rus} и добавлен в completed_users.")

    except Exception as e:
        logger.error(f"Ошибка в main_service_job для пользователя {user_id}: {e}", exc_info=True)
        try:
            await context.bot.send_message(user_id, clean_text("К сожалению, при подготовке вашего ответа произошла ошибка. Пожалуйста, свяжитесь с @zamira_esoteric для уточнения деталей."))
        except Exception as e_nested:
            logger.error(f"Не удалось отправить сообщение об ошибке в main_service_job пользователю {user_id}: {e_nested}")

async def review_request_job(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data 
    user_id: int = job_data["user_id"] # type: ignore
    service_type: str = job_data["service_type"] # type: ignore
    service_type_rus = "расклад Таро" if service_type == "tarot" else "разбор Матрицы Судьбы"

    logger.info(f"Отправка отложенного запроса на отзыв пользователю {user_id}")
    try:
        await context.bot.send_message(user_id, REVIEW_TEXT_DELAYED.format(service_type_rus=service_type_rus))
    except Exception as e:
        logger.error(f"Ошибка при отправке запроса на отзыв пользователю {user_id}: {e}", exc_info=True)

# --- ConversationHandler состояния ---
CHOOSE_SERVICE, ASK_NAME, ASK_DOB, ASK_TAROT_STORY, CONFIRM_DATA = range(5)
CANCEL_CALLBACK_DATA = "cancel_conv_inline" # Константа для callback_data отмены

# --- Клавиатура отмены ---
def get_cancel_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отменить", callback_data=CANCEL_CALLBACK_DATA)]])

# --- Функции ConversationHandler ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if not user: return ConversationHandler.END
    
    if user.id in completed_users:
        await update.message.reply_text(clean_text(PRIVATE_MESSAGE))
        return ConversationHandler.END

    if context.user_data: # Очищаем на случай, если диалог был прерван некорректно
        context.user_data.clear() 
        
    keyboard = [
        [InlineKeyboardButton("🃏 Расклад Таро", callback_data="tarot")],
        [InlineKeyboardButton("🌟 Матрица Судьбы", callback_data="matrix")],
        [InlineKeyboardButton("📩 Связь со мной", callback_data="contact_direct")],
    ]
    await update.message.reply_text(clean_text(WELCOME_TEXT), reply_markup=InlineKeyboardMarkup(keyboard))
    return CHOOSE_SERVICE

async def choose_service_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_data = context.user_data 
    if user_data is None: 
        user_data = context.user_data = {}

    service_type = query.data
    if service_type not in ["tarot", "matrix", "contact_direct", "back_to_start"]:
        logger.warning(f"Неизвестный callback_data в choose_service_callback: {service_type}")
        return CHOOSE_SERVICE 

    if service_type == "contact_direct":
        await query.edit_message_text(clean_text(CONTACT_TEXT), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад в меню", callback_data="back_to_start")]]))
        return CHOOSE_SERVICE 
    elif service_type == "back_to_start":
        keyboard_main = [
            [InlineKeyboardButton("🃏 Расклад Таро", callback_data="tarot")],
            [InlineKeyboardButton("🌟 Матрица Судьбы", callback_data="matrix")],
            [InlineKeyboardButton("📩 Связь со мной", callback_data="contact_direct")],
        ]
        await query.edit_message_text(clean_text(WELCOME_TEXT), reply_markup=InlineKeyboardMarkup(keyboard_main))
        return CHOOSE_SERVICE

    user_data["service_type"] = service_type # type: ignore
    
    intro_text = TAROT_INTRO_TEXT if service_type == "tarot" else MATRIX_INTRO_TEXT
    next_message_text = ASK_NAME_TEXT
    
    try:
        await query.edit_message_text(text=clean_text(intro_text))
    except TelegramError as e: 
        if "Message is not modified" not in str(e):
            logger.error(f"Ошибка редактирования сообщения в choose_service_callback: {e}")
            await query.message.reply_text(text=clean_text(intro_text)) # Отправляем новое, если edit не удался
    
    await query.message.reply_text(clean_text(next_message_text), reply_markup=get_cancel_keyboard()) 
    return ASK_NAME

async def ask_name_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_data = context.user_data
    if user_data is None: return ConversationHandler.END

    name = update.message.text
    if not name or len(name.strip()) < 2:
        await update.message.reply_text("Имя кажется слишком коротким. Пожалуйста, введите корректное имя.", reply_markup=get_cancel_keyboard())
        return ASK_NAME
    user_data["name"] = clean_text(name.strip()) # type: ignore
    await update.message.reply_text(clean_text(ASK_DOB_TEXT), reply_markup=get_cancel_keyboard())
    return ASK_DOB

async def ask_dob_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_data = context.user_data
    if user_data is None or "service_type" not in user_data : return ConversationHandler.END # type: ignore

    dob_text = update.message.text
    if not dob_text or not validate_date_format(dob_text.strip()):
        await update.message.reply_text("Пожалуйста, введите дату в формате ДД.ММ.ГГГГ (например, 15.03.1990).", reply_markup=get_cancel_keyboard())
        return ASK_DOB
    if not validate_date_semantic(dob_text.strip()):
        await update.message.reply_text("Дата кажется некорректной (например, неверный год или день). Пожалуйста, проверьте и введите снова.", reply_markup=get_cancel_keyboard())
        return ASK_DOB
        
    user_data["dob"] = clean_text(dob_text.strip()) # type: ignore

    if user_data["service_type"] == "tarot": # type: ignore
        await update.message.reply_text(clean_text(ASK_TAROT_STORY_TEXT), reply_markup=get_cancel_keyboard())
        return ASK_TAROT_STORY
    else: 
        confirm_text = CONFIRM_DETAILS_MATRIX_TEXT.format(name=user_data["name"], dob=user_data["dob"]) # type: ignore
        keyboard = [[InlineKeyboardButton("✅ Всё верно, подтверждаю", callback_data="confirm_final")],
                    [InlineKeyboardButton("❌ Отменить (данные не сохранятся)", callback_data=CANCEL_CALLBACK_DATA)]] # Кнопка отмены и на этапе подтверждения
        await update.message.reply_text(confirm_text, reply_markup=InlineKeyboardMarkup(keyboard))
        return CONFIRM_DATA

async def ask_tarot_story_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_data = context.user_data
    if user_data is None: return ConversationHandler.END

    story_text = update.message.text
    min_len = CONFIG.get("MIN_TEXT_LENGTH_TAROT", 50)
    if not story_text or len(story_text.strip()) < min_len:
        await update.message.reply_text(f"Пожалуйста, опишите вашу ситуацию подробнее (не менее {min_len} символов).", reply_markup=get_cancel_keyboard())
        return ASK_TAROT_STORY
    
    user_data["story"] = clean_text(story_text.strip()) # type: ignore
    confirm_text = CONFIRM_DETAILS_TAROT_TEXT.format(name=user_data["name"], dob=user_data["dob"], story=user_data["story"]) # type: ignore
    keyboard = [[InlineKeyboardButton("✅ Всё верно, подтверждаю", callback_data="confirm_final")],
                [InlineKeyboardButton("❌ Отменить (данные не сохранятся)", callback_data=CANCEL_CALLBACK_DATA)]]
    await update.message.reply_text(confirm_text, reply_markup=InlineKeyboardMarkup(keyboard))
    return CONFIRM_DATA

async def confirm_data_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_data = context.user_data
    if user_data is None or "service_type" not in user_data: # type: ignore
        await query.message.reply_text("Произошла ошибка в диалоге, данные утеряны. Пожалуйста, начните сначала: /start")
        if user_data: user_data.clear()
        return ConversationHandler.END

    user_id = query.from_user.id

    if query.data == "confirm_final": # Эта кнопка не должна иметь опции отмены здесь, т.к. это финальное подтверждение
        try:
            await query.edit_message_text(text=clean_text(RESPONSE_WAIT), reply_markup=None)
        except TelegramError as e:
             if "Message is not modified" not in str(e): logger.error(f"Ошибка edit_message_text в confirm_data_callback: {e}")
             
        service_type = user_data["service_type"] # type: ignore
        input_for_gpt = f"Имя: {user_data['name']}\nДата рождения: {user_data['dob']}" # type: ignore
        if service_type == "tarot":
            input_for_gpt += f"\nСитуация и вопрос: {user_data.get('story', 'Не указано')}" # type: ignore

        system_prompt_template, user_prompt_template_str, max_tokens_val = (
            (PROMPT_TAROT_SYSTEM, PROMPT_TAROT_USER, CONFIG["OPENAI_MAX_TOKENS_TAROT"]) if service_type == "tarot"
            else (PROMPT_MATRIX_SYSTEM, PROMPT_MATRIX_USER, CONFIG["OPENAI_MAX_TOKENS_MATRIX"])
        )
        
        final_user_prompt = user_prompt_template_str.format(input_text=input_for_gpt)
        result = await ask_gpt(system_prompt_template, final_user_prompt, max_tokens_val)

        if result is None:
            await query.message.reply_text(clean_text(OPENAI_ERROR_MESSAGE)) # Отправляем новое сообщение
            
            # Восстанавливаем предыдущее сообщение с кнопками для повторной попытки или отмены
            keyboard_retry = [[InlineKeyboardButton("Попробовать подтвердить снова", callback_data="confirm_final")],
                              [InlineKeyboardButton("❌ Отменить (данные не сохранятся)", callback_data=CANCEL_CALLBACK_DATA)]] # Используем тот же CANCEL_CALLBACK_DATA
            
            current_confirm_text = ""
            if service_type == "tarot":
                 current_confirm_text = CONFIRM_DETAILS_TAROT_TEXT.format(name=user_data.get("name","?"), dob=user_data.get("dob","?"), story=user_data.get("story","?")) # type: ignore
            else:
                 current_confirm_text = CONFIRM_DETAILS_MATRIX_TEXT.format(name=user_data.get("name","?"), dob=user_data.get("dob","?")) # type: ignore
            try: 
                # Не пытаемся редактировать сообщение с ошибкой, а отправляем новое с кнопками подтверждения
                await query.message.reply_text(text=current_confirm_text, reply_markup=InlineKeyboardMarkup(keyboard_retry))
            except Exception as e_reply:
                logger.error(f"Не удалось отправить кнопки повтора после ошибки OpenAI: {e_reply}")
            return CONFIRM_DATA 

        if not context.job_queue:
            logger.error("JobQueue не инициализирован!")
            await query.message.reply_text("Критическая ошибка бота. Свяжитесь с @zamira_esoteric.")
            user_data.clear() # type: ignore
            return ConversationHandler.END
        
        job_payload = {"user_id": user_id, "result": result, "service_type": service_type}
        context.job_queue.run_once(main_service_job, CONFIG["DELAY_SECONDS_MAIN_SERVICE"], data=job_payload, name=f"main_job_{user_id}") # type: ignore
        
        logger.info(f"Заявка пользователя {user_id} ({service_type}) принята и запланирована.")
        user_data.clear() # type: ignore
        return ConversationHandler.END

    # Если это был не "confirm_final", то это должен быть CANCEL_CALLBACK_DATA из кнопок на этапе подтверждения
    # который будет обработан cancel_conv_inline_callback в fallbacks.
    # Но для чистоты, если бы мы не использовали глобальный fallback:
    # elif query.data == CANCEL_CALLBACK_DATA: # Обработка отмены на этапе подтверждения
    #    return await common_cancel_logic(update, context, query=query)

    return CONFIRM_DATA # Остаемся здесь, если какой-то неожиданный callback

async def common_cancel_logic(update: Update, context: ContextTypes.DEFAULT_TYPE, query: Optional[CallbackQuery] = None) -> int:
    """Общая логика отмены для команды и инлайн кнопки."""
    if context.user_data:
        context.user_data.clear()
    
    cancel_message_text = clean_text(CANCEL_TEXT)
    
    if query: # Если отмена через кнопку
        try:
            await query.edit_message_text(text=cancel_message_text, reply_markup=None)
        except TelegramError as e:
            if "Message is not modified" not in str(e): 
                logger.warning(f"Не удалось отредактировать сообщение при отмене через кнопку: {e}")
            # Если не удалось отредактировать (например, сообщение старое), отправим новое
            await query.message.reply_text(text=cancel_message_text)
    elif update.message: # Если отмена через команду /cancel
        await update.message.reply_text(text=cancel_message_text)

    # Отправляем главное меню как новое сообщение
    keyboard_main = [
        [InlineKeyboardButton("🃏 Расклад Таро", callback_data="tarot")],
        [InlineKeyboardButton("🌟 Матрица Судьбы", callback_data="matrix")],
        [InlineKeyboardButton("📩 Связь со мной", callback_data="contact_direct")],
    ]
    # Определяем, какому чату отправлять главное меню
    chat_to_reply = query.message.chat if query and query.message else update.message.chat if update.message else None
    if chat_to_reply:
        await chat_to_reply.send_message(clean_text(WELCOME_TEXT), reply_markup=InlineKeyboardMarkup(keyboard_main))
    
    return ConversationHandler.END


async def cancel_conv_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отмена диалога через команду /cancel."""
    logger.info(f"Пользователь {update.effective_user.id} отменил диалог командой /cancel.")
    return await common_cancel_logic(update, context)

async def cancel_conv_inline_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отмена диалога через инлайн кнопку."""
    query = update.callback_query
    await query.answer()
    logger.info(f"Пользователь {query.from_user.id} отменил диалог через инлайн кнопку.")
    return await common_cancel_logic(update, context, query=query)


async def handle_satisfaction_and_other_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data: return
    await query.answer()
    user_id = query.from_user.id

    if query.data.startswith("satisfaction_"):
        parts = query.data.split("_")
        answer = parts[1] 
        service_type = parts[2] 
        
        original_message_text = query.message.text 
        
        if answer == "yes":
            await query.edit_message_text(text=f"{original_message_text}\n\n{clean_text(REVIEW_PROMISE_TEXT)}", reply_markup=None)
            if not context.job_queue:
                logger.error(f"JobQueue не найден при планировании запроса отзыва для {user_id}")
                return
            job_payload = {"user_id": user_id, "service_type": service_type}
            context.job_queue.run_once(review_request_job, CONFIG["DELAY_SECONDS_REVIEW_REQUEST"], data=job_payload, name=f"review_req_job_{user_id}") # type: ignore
            logger.info(f"Запланирован запрос отзыва для {user_id} через {CONFIG['DELAY_SECONDS_REVIEW_REQUEST']} секунд.")
        elif answer == "no":
            await query.edit_message_text(text=f"{original_message_text}\n\n{clean_text(NO_PROBLEM_TEXT)}", reply_markup=None)
    

async def post_fallback_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.effective_user: 
        user_id = update.effective_user.id
        if user_id in completed_users:
            await update.message.reply_text(clean_text(PRIVATE_MESSAGE))
            return
        
        # Проверяем, не находится ли пользователь в активном диалоге (user_data не пусто)
        # Это очень упрощенная проверка. ConversationHandler сам решает, что делать с "лишними" сообщениями.
        if not context.user_data or not context.user_data.get(ConversationHandler.STATE): # type: ignore
            await update.message.reply_text(
            "Кажется, мы не находимся в процессе оформления запроса. Нажмите /start, чтобы начать или выбрать услугу 🔮.",
        )


# --- Запуск бота ---
if __name__ == "__main__":
    try:
        app_builder = ApplicationBuilder().token(BOT_TOKEN)
        app_builder.concurrent_updates(10) 
        app_builder.job_queue(JobQueue()) 
        app = app_builder.build()

        conv_handler = ConversationHandler(
            entry_points=[CommandHandler("start", start_command)],
            states={
                CHOOSE_SERVICE: [
                    CallbackQueryHandler(choose_service_callback, pattern="^(tarot|matrix|contact_direct|back_to_start)$")
                ],
                ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_name_message)],
                ASK_DOB: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_dob_message)],
                ASK_TAROT_STORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_tarot_story_message)],
                CONFIRM_DATA: [CallbackQueryHandler(confirm_data_callback, pattern="^confirm_final$")], # Только confirm_final здесь
            },
            fallbacks=[
                CommandHandler("cancel", cancel_conv_command), 
                CommandHandler("start", start_command), # Позволяет перезапустить диалог в любой момент
                CallbackQueryHandler(cancel_conv_inline_callback, pattern=f"^{CANCEL_CALLBACK_DATA}$") # Обработчик для инлайн кнопки отмены
            ],
            per_message=False, 
        )

        app.add_handler(conv_handler)
        app.add_handler(CallbackQueryHandler(handle_satisfaction_and_other_callbacks, pattern="^satisfaction_"))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, post_fallback_message))

        logger.info("Бот Замира запускается...")
        app.run_polling(allowed_updates=Update.ALL_TYPES) # Явно указываем все типы апдейтов
    except Exception as e:
        logger.critical(f"Критическая ошибка при запуске бота: {e}", exc_info=True)
        raise

