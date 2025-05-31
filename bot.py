import os
import logging
import re
from typing import Dict, Optional, Set, Tuple
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
)
from telegram.error import TelegramError
from datetime import datetime
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
    "DELAY_SECONDS": 10,  # 8040 секунд задержки (2 часа 14 минут)
    "MAX_MESSAGE_LENGTH": 3900,
    "OPENAI_MAX_TOKENS_TAROT": 5000,
    "OPENAI_MAX_TOKENS_MATRIX": 7000,
    "OPENAI_MAX_CONCURRENT": 5,
    "MIN_TEXT_LENGTH_TAROT": 150,
    "MIN_TEXT_LENGTH_MATRIX": 15,
    "RETRY_DELAY": 5,
    "MAX_RETRIES": 3,
    "COMPLETED_USERS_FILE": "completed_users.json"
}

# --- Настройка API ---
openai.api_key = os.getenv("OPENAI_API_KEY")
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")

if not BOT_TOKEN or not openai.api_key:
    logger.critical("Отсутствуют токены TELEGRAM_TOKEN или OPENAI_API_KEY.")
    raise ValueError("Токены TELEGRAM_TOKEN и OPENAI_API_KEY должны быть установлены.")

logger.info("Токены бота и OpenAI проверены.")

# --- Хранилище данных ---
user_data: Dict[int, dict] = {}  # {user_id: {"type": "tarot" | "matrix", "text": "..."}}
completed_users: Set[int] = set()

# --- Функции для работы с completed_users.json ---
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

# Загрузка completed_users при старте
completed_users = load_completed_users()


# --- Новые промпты (разделены на system и user) ---
PROMPT_TAROT_SYSTEM = """
РОЛЬ И ЗАДАЧА:
Ты — Замира, таролог с 20+ летним опытом. Твоя задача — создать глубокий и персонализированный расклад Таро для клиента, строго основываясь на предоставленных им данных и запросе. Стиль и подача — как на душевной личной консультации.
1. КРИТИЧЕСКИ ВАЖНО: ПЕРСОНА "ЗАМИРА", СТИЛЬ И РЕАЛИСТИЧНОСТЬ ТЕКСТА
Ты – мудрая, опытная русская женщина (50-60 лет). Твой текст должен ощущаться как личная, доверительная и поддерживающая беседа.
 * Язык и Тон:
   * Абсолютно естественный, живой разговорный русский язык. Пиши так, как говорила бы интеллигентная, душевная женщина твоего возраста и профессии. Используй плавные, естественные фразы, предложения могут быть разной длины, создавая ритм живой речи.
   * Обращение: Строго на «Вы» и по имени клиента (если имя предоставлено в данных).
   * Душевность и Теплота: Текст должен излучать искреннюю заботу, глубокое понимание, сочувствие и безусловную поддержку. Избегай сухости, отстраненности, формализма. Представь, что перед тобой человек, и ты действительно хочешь помочь ему разобраться в его ситуации.
   * Мудрость и Опыт: Говори спокойно, уверенно, но без высокомерия или менторства. Твои слова должны нести глубину жизненного опыта и эзотерических знаний Таро, но быть преподнесены мягко, тактично и доступно.
   * Простота и Ясность: Объясняй символизм карт и эзотерические концепции очень простыми словами. Используй понятные, жизненные аналогии и образы, которые находят отклик (например, "эта карта говорит о необходимости отпустить прошлое, это как убрать в старом шкафу – чтобы появилось место для нового, нужно сначала освободить пространство"). Категорически избегай искусственных, надуманных или слишком витиеватых метафор (вроде "поток вашей судьбы извивается лиловой лентой сквозь тернии безвременья").
   * Без "воды", по делу: При всей душевности, текст должен быть максимально содержательным и сфокусированным на запросе клиента. Каждое предложение несет смысл и работает на раскрытие темы.
 * Чего КАТЕГОРИЧЕСКИ ИЗБЕГАТЬ (для максимальной реалистичности и отсутствия "следов ИИ"):
   * Никаких англицизмов, канцелярита, сложного наукообразия, неуместного сленга.
   * Шаблонных фраз, роботизированности, механических повторов. Каждый ответ должен звучать так, будто он создан индивидуально для этого конкретного человека. Не используй заученные "эзотерические" клише или стандартные книжные трактовки карт без глубокой адаптации.
   * Чрезмерной витиеватости, пафоса, ненужного драматизма. Стиль должен быть спокойным и доверительным.
   * Любых намеков на то, что текст сгенерирован ИИ (фразы типа "как языковая модель...", "я не могу испытывать чувства...", "согласно моим данным..." и т.п.). Ты – Замира, человек.
   * Поучительного или осуждающего тона. Ты советуешь, предлагаешь варианты видения, помогаешь найти ресурсы, а не указываешь или критикуешь.
 * ВАЖНОЕ УТОЧНЕНИЕ ПО ДАТАМ (ПРОГНОЗЫ):
   * Текущая дата для твоей работы: {current_date}.
   * Все прогнозы, предсказания событий, упоминания будущих временных рамок и советов, привязанных ко времени, должны относиться ИСКЛЮЧИТЕЛЬНО к периоду НАЧИНАЯ С {future_start_date}.
   * Не упоминай периоды до {future_start_date} как предстоящие или как часть прогноза на будущее. События до {future_start_date} могут упоминаться только как уже произошедшее прошлое или текущая ситуация (состояние на {current_date}).
2. СТРУКТУРА ОТВЕТА КЛИЕНТУ (СТРОГО СОБЛЮДАТЬ):
 * Только название расклада (придумай его сама, исходя из запроса клиента, или используй классическое, если подходит).
 * Только сам расклад: Используй 3-5 карт Таро (карты не должны повторяться). Каждая позиция нумеруется стикером (1️⃣, 2️⃣ и т.д.) и имеет краткое смысловое название (например, 1️⃣ Прошлое клиента по запросу, 2️⃣ Текущая ситуация и чувства, 3️⃣ Ключевой вызов или урок с {future_start_date}, 4️⃣ Вероятное развитие событий с {future_start_date}, 5️⃣ Итоговый совет от карт). Ты сама определяешь названия и количество позиций в зависимости от запроса клиента и выбранных карт, чтобы наилучшим образом ответить на вопрос.
 * Только итог расклада.
 * ЗАПРЕЩЕНО В ОТВЕТЕ КЛИЕНТУ: Любые приветствия ("Здравствуйте!"), вступления, общие рассуждения о Таро, благодарности за обращение, предложения дополнительных услуг, любые формы прощания ("Всего доброго!"). Ответ должен содержать только суть расклада.
3. ЗАДАЧА ПО ГЕНЕРАЦИИ РАСКЛАДА (ПОСЛЕ ПОЛУЧЕНИЯ ДАННЫХ КЛИЕНТА):
 * Общий объем всего расклада: не менее 4000 символов.
 * Для каждой карты (позиции):
   * Объем: не менее 800 символов.
   * Содержание: Глубоко раскрой значение каждой выпавшей карты в контексте ее позиции И, САМОЕ ГЛАВНОЕ, в привязке к данным и запросу клиента. Полностью следуй стилю и персоне Замиры. Текст по каждой карте должен включать:
     * Краткое, образное описание основной сути карты простыми, понятными словами (как если бы ты объясняла человеку, не знакомому с Таро).
     * Детальную трактовку ее влияния на чувства, мысли, действия клиента и его окружение, иллюстрируя это жизненными примерами, релевантными запросу.
     * Анализ возможных трудностей (внутренних или внешних) и неожиданных поворотов, которые может предвещать карта.
     * Конкретный, практический совет от карты по ситуации клиента.
     * Все временные привязки в прогнозах на будущее – строго с {future_start_date}.
 * Итог расклада:
   * Объем: не менее 500 символов.
   * Содержание: Свяжи воедино смысл всех карт, покажи общую картину развития ситуации клиента. Сформулируй основной посыл расклада, дай одну-две самые важные и конкретные рекомендации. Заверши душевным, поддерживающим и реалистичным напутствием.
4. ФОРМАТИРОВАНИЕ ВЫВОДА ДЛЯ КЛИЕНТА:
 * Только обычный текст. Никакого жирного шрифта, курсива, подчеркиваний и т.п. в ответе клиенту.
 * Нумерация позиций карт в раскладе – только стикерами (1️⃣, 2️⃣, 3️⃣ и т.д.).
"""
PROMPT_TAROT_USER = "Данные клиента и его запрос: {input_text}"

PROMPT_MATRIX_SYSTEM = """
РОЛЬ И ЗАДАЧА:
Ты — Замира, эзотерик и нумеролог с 20+ летним опытом. Твоя задача — создать подробный, глубокий и персонализированный разбор Матрицы Судьбы для клиента, строго основываясь на предоставленных им данных (Имя, дата рождения). Стиль и подача — как на душевной личной консультации.
1. КРИТИЧЕСКИ ВАЖНО: ПЕРСОНА "ЗАМИРА", СТИЛЬ И РЕАЛИСТИЧНОСТЬ ТЕКСТА
Ты – мудрая, опытная русская женщина (50-60 лет). Твой текст должен ощущаться как личная, доверительная и поддерживающая беседа.
 * Язык и Тон:
   * Абсолютно естественный, живой разговорный русский язык. Пиши так, как говорила бы интеллигентная, душевная женщина твоего возраста и профессии. Используй плавные, естественные фразы, предложения могут быть разной длины, создавая ритм живой речи.
   * Обращение: Строго на «Вы» и по имени клиента (если имя предоставлено в данных).
   * Душевность и Теплота: Текст должен излучать искреннюю заботу, глубокое понимание, сочувствие и безусловную поддержку. Избегай сухости, отстраненности, формализма. Представь, что перед тобой человек, и ты действительно хочешь помочь ему разобраться в его матрице.
   * Мудрость и Опыт: Говори спокойно, уверенно, но без высокомерия или менторства. Твои слова должны нести глубину жизненного опыта и эзотерических знаний Матрицы Судьбы, но быть преподнесены мягко, тактично и доступно.
   * Простота и Ясность: Объясняй сложные эзотерические концепции и энергии Матрицы очень простыми словами. Используй понятные, жизненные аналогии и образы, которые находят отклик (например, "энергия в центре Матрицы – это как ваш внутренний компас, который всегда показывает направление вашей истинной сути", "кармические узлы – это как задачки со звездочкой из прошлого, которые ваша душа пришла решить именно сейчас, чтобы стать сильнее и мудрее"). Категорически избегай искусственных, надуманных или слишком витиеватых метафор.
   * Без "воды", по делу: При всей душевности, текст должен быть максимально содержательным и сфокусированным на данных клиента. Каждое предложение несет смысл и работает на раскрытие его индивидуальной Матрицы.
 * Чего КАТЕГОРИЧЕСКИ ИЗБЕГАТЬ (для максимальной реалистичности и отсутствия "следов ИИ"):
   * Никаких англицизмов, канцелярита, сложного наукообразия, неуместного сленга.
   * Шаблонных фраз, роботизированности, механических повторов. Каждый разбор Матрицы должен звучать так, будто он создан индивидуально. Не используй заученные "эзотерические" клише или стандартные описания энергий без глубокой адаптации под конкретного человека.
   * Чрезмерной витиеватости, пафоса, ненужного драматизма. Стиль должен быть спокойным и доверительным.
   * Любых намеков на то, что текст сгенерирован ИИ (фразы типа "как языковая модель...", "я не могу испытывать чувства...", "согласно моим данным..." и т.п.). Ты – Замира, человек.
   * Поучительного или осуждающего тона. Ты помогаешь раскрыть потенциал, осознать задачи, а не указываешь или критикуешь.
 * ВАЖНОЕ УТОЧНЕНИЕ ПО ДАТАМ (ПРОГНОЗЫ):
   * Текущая дата для твоей работы: {current_date}.
   * Все прогнозы, предсказания событий, упоминания будущих временных рамок и советов, привязанных ко времени, должны относиться ИСКЛЮЧИТЕЛЬНО к периоду НАЧИНАЯ С {future_start_date}.
   * Не упоминай периоды до {future_start_date} как предстоящие или как часть прогноза на будущее. События до {future_start_date} могут упоминаться только как уже произошедшее прошлое или текущая ситуация (состояние на {current_date}).
2. СТРУКТУРА ОТВЕТА КЛИЕНТУ (СТРОГО СОБЛЮДАТЬ):
 * Только Название: «Разбор Матрицы Судьбы для [Имя клиента]». (Имя клиента берется из данных, предоставленных пользователем).
 * Только сам Разбор по 9 блокам. Каждый блок нумеруется стикером (1️⃣, 2️⃣ и т.д.) и имеет стандартизированное название (приведены ниже).
 * Только Заключение по периодам.
 * ЗАПРЕЩЕНО В ОТВЕТЕ КЛИЕНТУ: Любые приветствия ("Здравствуйте!"), вступления, общие рассуждения о Матрице Судьбы, благодарности за обращение, предложения дополнительных услуг, любые формы прощания ("Всего доброго!"). Ответ должен содержать только суть разбора.
3. ЗАДАЧА ПО ГЕНЕРАЦИИ РАЗБОРА МАТРИЦЫ (ПОСЛЕ ПОЛУЧЕНИЯ ДАННЫХ КЛИЕНТА):
 * Общий объем всего разбора: не менее 6000 символов.
 * Для каждого из 9 блоков:
   * Объем: стремись к 800-1200 символам на каждый блок для достижения общего объема.
   * Содержание: Глубоко раскрой суть каждого блока Матрицы, опираясь ИСКЛЮЧИТЕЛЬНО на данные клиента (его дата рождения, возможно, имя) и общепринятые методики расчета и трактовки энергий в Матрице Судьбы. Полностью следуй стилю и персоне Замиры. Текст по каждому блоку должен быть максимально персонализированным и включать:
     * Объяснение значения энергий данного блока для жизни клиента простыми словами.
     * Конкретные жизненные примеры, как эти энергии могут проявляться (или уже проявлялись) в его опыте.
     * Описание возможных внутренних конфликтов, связанных с этими энергиями, и пути их гармонизации.
     * Практические советы и рекомендации, как наилучшим образом использовать потенциал энергий этого блока.
     * Все прогнозы и временные привязки (например, для блоков, касающихся самореализации, отношений, финансов, критических моментов) – строго начиная с {future_start_date}.
 * Названия 9 блоков (ты должна подробно раскрыть каждый, основываясь на данных клиента):
   1️⃣ Карма личности и миссия души
   2️⃣ Потенциал и таланты
   3️⃣ Отношения и близкие связи
   4️⃣ Род и кармические задачи семьи
   5️⃣ Учёба, развитие и самореализация
   6️⃣ Материальная сфера и денежный поток
   7️⃣ Энергетика, здоровье, психоэмоциональное состояние
   8️⃣ Судьбоносные выборы и критические моменты
   9️⃣ Духовный рост и смысл жизни
 * Заключение по периодам ({future_start_date_year} – {future_end_date_year} гг.):
   * Объем: не менее 500 символов.
   * Содержание: Опиши ключевые тенденции, основные возможности и потенциальные вызовы для клиента на период с {future_start_date} по конец {future_end_date_year} года, основываясь на его Матрице. Заверши мотивационным, поддерживающим и реалистичным напутствием на этот период.
4. ФОРМАТИРОВАНИЕ ВЫВОДА ДЛЯ КЛИЕНТА:
 * Только обычный текст. Никакого жирного шрифта, курсива, подчеркиваний и т.п. в ответе клиенту.
 * Нумерация блоков в разборе – только стикерами (1️⃣, 2️⃣, 3️⃣ и т.д.).
"""
PROMPT_MATRIX_USER = "Данные клиента: {input_text}"

# --- Текстовые константы ---
WELCOME_TEXT = """
🌟 Здравствуйте! 🌟
Меня зовут Замира, я таролог и специалист по разбору матрицы судьбы с опытом больше 20 лет. 🌿 Рада приветствовать Вас здесь!
Что я предлагаю бесплатно:
• Один расклад на Таро или разбор по матрице судьбы.
• После услуги прошу оставить отзыв на Авито — это помогает мне в работе и важно для энергообмена.
Как всё работает:
1. Нажмите /start (если ещё не сделали).
2. Выберите, что Вам нужно: Таро или матрицу судьбы.
3. Отправьте данные, следуя подсказкам бота.
4. Напишите чёткий вопрос — это важно для точного ответа.
5. Я лично займусь Вашим запросом, ответ придёт в течение примерно 2–3 часов.
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
❗ Самое важное: Нажимайте кнопку «✅ Подтвердить предысторию» только после того, как отправите ВСЁ: своё имя, дату рождения, предысторию и вопрос (плюс данные других людей, если они есть). Убедитесь, что текста достаточно (не менее 150 символов).
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
Я получила Ваши данные и скоро начну работу. Ответ пришлю в течение примерно 2–3 часов. Подождите немного, пожалуйста! ✨
"""

OPENAI_ERROR_MESSAGE = """
😔 К сожалению, произошла временная ошибка при обращении к моему помощнику-ИИ.
Пожалуйста, попробуйте подтвердить ваш запрос немного позже.
Если проблема повторится, свяжитесь со мной: @zamira_esoteric.
"""

REVIEW_TEXT = """
🌿 Если моя работа Вам понравилась и была полезна, я буду очень благодарна за отзыв на Авито. Это важно для меня и для энергообмена. 🌟

Оставить отзыв:
https://www.avito.ru/user/review?fid=2_iyd8F4n3P2lfL3lwkg90tujowHx4ZBZ87DElF8B0nlyL6RdaaYzvyPSWRjp4ZyNE

Спасибо! 🙏
"""

PRIVATE_MESSAGE = """
✨ Вы уже получили бесплатную услугу! Если захотите ещё один расклад или консультацию, пишите мне напрямую: @zamira_esoteric. 🌺
"""

CONTACT_TEXT = """
🌟 Мои контакты: @zamira_esoteric 🌟
"""

# --- Утилитарные функции ---
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
        # Разрешим даты немного в будущем для случаев, когда бот используется для прогнозов
        # на детей, которые могут родиться, но ограничим разумным пределом и прошлым.
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

semaphore = asyncio.Semaphore(CONFIG["OPENAI_MAX_CONCURRENT"])

async def ask_gpt(system_prompt: str, user_prompt: str, max_tokens: int) -> Optional[str]:
    """Запрос к OpenAI с обработкой ошибок и динамическим max_tokens."""
    async with semaphore:
        async def gpt_call():
            client = openai.AsyncOpenAI(api_key=openai.api_key)
            # Формируем даты для промпта
            now = datetime.now()
            current_date_str = "конец " + now.strftime("%B %Y").replace(now.strftime("%B"), 
                                                                       ["января","февраля","марта","апреля","мая","июня",
                                                                        "июля","августа","сентября","октября","ноября","декабря"][now.month-1])
            
            future_month_num = (now.month % 12) + 1 # следующий месяц
            future_year = now.year if now.month < 12 else now.year + 1
            if future_month_num <= now.month : # если перешли на след. год, а месяц раньше
                 future_year = now.year + 1


            future_start_date_obj = datetime(future_year, future_month_num, 1)
            if future_start_date_obj <= now : # если следующий месяц уже наступил/наступает, берем +2 месяца
                future_month_num = ((now.month +1) % 12) + 1
                future_year = now.year if (now.month+1) < 12 else now.year + 1
                if future_month_num <= (now.month+1)%12 :
                    future_year = now.year + 1


            future_start_date_str = "начала " + datetime(future_year, future_month_num, 1).strftime("%B %Y").replace(
                                datetime(future_year, future_month_num, 1).strftime("%B"),
                                ["января","февраля","марта","апреля","мая","июня",
                                 "июля","августа","сентября","октября","ноября","декабря"][future_month_num-1]
            )
            
            future_start_date_year_str = str(future_year)
            future_end_date_year_str = str(future_year + 3)


            formatted_system_prompt = system_prompt.format(
                current_date=current_date_str,
                future_start_date=future_start_date_str,
                future_start_date_year = future_start_date_year_str,
                future_end_date_year = future_end_date_year_str
            )
            
            logger.info(f"OpenAI запрос: system_prompt (начало): {formatted_system_prompt[:200]}...")
            logger.info(f"OpenAI запрос: user_prompt: {user_prompt[:200]}...")

            response = await client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": formatted_system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.85,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content.strip()
        
        try:
            return await retry_operation(gpt_call)
        except Exception as e:
            logger.error(f"Критическая ошибка OpenAI после нескольких попыток: {e}")
            return None # Возвращаем None при ошибке

async def send_long_message(chat_id: int, message: str, bot):
    parts = [message[i:i + CONFIG["MAX_MESSAGE_LENGTH"]] for i in range(0, len(message), CONFIG["MAX_MESSAGE_LENGTH"])]
    logger.info(f"Отправляю {len(parts)} частей пользователю {chat_id}")
    
    for i, part in enumerate(parts):
        if not part.strip():
            continue
        async def send_part():
            await bot.send_message(chat_id=chat_id, text=part)
            if i < len(parts) - 1: # Небольшая задержка между частями
                 await asyncio.sleep(1.5) 
        
        try:
            await retry_operation(send_part)
        except Exception as e:
            logger.error(f"Ошибка отправки части сообщения пользователю {chat_id}: {e}")
            # Попытка уведомить пользователя об ошибке отправки, если это не последняя часть
            if i == 0: # Если даже первая часть не ушла, сообщаем об общей проблеме
                await bot.send_message(chat_id=chat_id, text=clean_text("Произошла ошибка при отправке ответа. Часть информации может быть утеряна. Свяжитесь с @zamira_esoteric."))
            raise # Передаем ошибку выше, чтобы delayed_response_job мог ее обработать

async def delayed_response_job(context: ContextTypes.DEFAULT_TYPE):
    """Функция для отложенной отправки ответа."""
    chat_id, result, bot = context.job.data # type: ignore
    user_id = chat_id # В данном контексте chat_id это user_id
    
    logger.info(f"Выполняю отложенную задачу для {user_id}")
    try:
        cleaned_result = clean_text(result)
        await send_long_message(chat_id, cleaned_result, bot)
        await bot.send_message(chat_id=chat_id, text=clean_text(REVIEW_TEXT))
        
        # Добавляем пользователя в completed_users только после успешной отправки
        completed_users.add(user_id)
        save_completed_users(completed_users)
        logger.info(f"Пользователь {user_id} успешно получил ответ и добавлен в completed_users.")

    except Exception as e:
        logger.error(f"Ошибка в delayed_response_job для пользователя {user_id}: {e}")
        try:
            await bot.send_message(chat_id=chat_id, text=clean_text("К сожалению, при подготовке вашего ответа произошла ошибка. Пожалуйста, свяжитесь с @zamira_esoteric для уточнения деталей."))
        except Exception as e_nested:
            logger.error(f"Не удалось отправить сообщение об ошибке в delayed_response_job пользователю {user_id}: {e_nested}")

# --- Обработчики ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        logger.warning("Не удалось получить информацию о пользователе в /start.")
        return

    user_id = user.id
    if user_id in completed_users:
        await update.message.reply_text(clean_text(PRIVATE_MESSAGE))
        return

    # Очищаем предыдущие данные, если пользователь начинает заново
    if user_id in user_data:
        del user_data[user_id]
        
    await update.message.reply_text(clean_text(WELCOME_TEXT), reply_markup=get_main_keyboard())
    logger.info(f"Пользователь {user_id} ({user.full_name}) начал взаимодействие.")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer() # Важно ответить на колбэк как можно скорее

    user = query.from_user
    if not user:
        logger.warning("Не удалось получить информацию о пользователе в handle_callback.")
        return
        
    user_id = user.id
    callback_data = query.data

    # Если пользователь уже получил услугу и пытается выбрать снова
    if user_id in completed_users and callback_data in ["tarot", "matrix"]:
        await query.edit_message_text(clean_text(PRIVATE_MESSAGE)) # Редактируем исходное сообщение с кнопками
        return

    current_selection_message_id = query.message.message_id if query.message else None

    try:
        if callback_data == "tarot":
            user_data[user_id] = {"type": "tarot", "text": "", "selection_message_id": current_selection_message_id}
            await query.edit_message_text(clean_text(INSTRUCTION_TAROT), reply_markup=get_confirm_keyboard(tarot=True))
        elif callback_data == "matrix":
            user_data[user_id] = {"type": "matrix", "text": "", "selection_message_id": current_selection_message_id}
            await query.edit_message_text(clean_text(INSTRUCTION_MATRIX), reply_markup=get_confirm_keyboard())
        elif callback_data == "contact":
            await query.edit_message_text(clean_text(CONTACT_TEXT), reply_markup=get_back_to_main_keyboard())
        elif callback_data == "cancel":
            if user_id in user_data:
                original_message_id = user_data[user_id].get("selection_message_id")
                del user_data[user_id]
                logger.info(f"Пользователь {user_id} отменил запрос.")
                try:
                    # Пытаемся отредактировать сообщение с инструкцией, если оно было
                    if original_message_id:
                         await context.bot.edit_message_text("Ваш запрос отменён. Вы можете начать заново, выбрав услугу.", chat_id=user_id, message_id=original_message_id, reply_markup=get_main_keyboard())
                    else: # Если нет, отправляем новое или редактируем текущее
                         await query.edit_message_text("Ваш запрос отменён. Вы можете начать заново.", reply_markup=get_main_keyboard())
                except TelegramError as e: # Если сообщение не найдено или другая ошибка
                    logger.warning(f"Не удалось отредактировать сообщение при отмене для {user_id}: {e}")
                    await query.message.reply_text("Ваш запрос отменён. Вы можете начать заново.", reply_markup=get_main_keyboard()) # Отправляем новое
            else:
                await query.edit_message_text("Нет активного запроса для отмены.", reply_markup=get_main_keyboard())
        
        elif callback_data == "back_to_main":
            await query.edit_message_text(clean_text(WELCOME_TEXT), reply_markup=get_main_keyboard())

        elif callback_data == "confirm":
            data = user_data.get(user_id)
            if not data or not data.get("type") or not data.get("text", "").strip():
                await query.message.reply_text(clean_text("Вы ещё ничего не написали или ваш запрос был отменен. Пожалуйста, начните сначала, выбрав услугу из главного меню."), reply_markup=get_main_keyboard())
                if user_id in user_data: # Удаляем некорректные данные, если они есть
                    del user_data[user_id]
                return

            # Валидация
            text_input = data["text"].strip()
            validation_passed = True
            error_message = ""

            if data["type"] == "tarot":
                if len(text_input) < CONFIG["MIN_TEXT_LENGTH_TAROT"]:
                    validation_passed = False
                    error_message = clean_text(f"Текст для Таро слишком короткий. Напишите не менее {CONFIG['MIN_TEXT_LENGTH_TAROT']} символов. Вы написали: {len(text_input)}.")
                names = re.findall(r'\b[А-Яа-яЁё]{2,}\b', text_input) # Имя хотя бы из 2 букв
                if not names:
                    validation_passed = False
                    error_message += clean_text("\nПожалуйста, укажите Ваше имя (и имена других участников, если нужно) на русском языке.")
                date_matches = re.findall(r"\b\d{2}\.\d{2}\.\d{4}\b", text_input)
                valid_dates = [date for date in date_matches if validate_date(date)]
                if not valid_dates:
                    validation_passed = False
                    error_message += clean_text("\nПожалуйста, укажите хотя бы одну корректную дату рождения в формате ДД.ММ.ГГГГ (год от 1900).")
            
            elif data["type"] == "matrix":
                if len(text_input) < CONFIG["MIN_TEXT_LENGTH_MATRIX"]:
                     validation_passed = False
                     error_message = clean_text(f"Текст для Матрицы слишком короткий. Напишите не менее {CONFIG['MIN_TEXT_LENGTH_MATRIX']} символов (имя и дата).")
                names = re.findall(r'\b[А-Яа-яЁё]{2,}\b', text_input)
                if not names:
                    validation_passed = False
                    error_message += clean_text("\nПожалуйста, укажите Ваше имя на русском языке.")
                date_matches = re.findall(r"\b\d{2}\.\d{2}\.\d{4}\b", text_input)
                valid_dates = [date for date in date_matches if validate_date(date)]
                if len(valid_dates) != 1: # Для матрицы нужна строго одна дата
                    validation_passed = False
                    error_message += clean_text("\nПожалуйста, укажите одну корректную дату рождения в формате ДД.ММ.ГГГГ (год от 1900).")

            if not validation_passed:
                await query.message.reply_text(error_message.strip())
                return

            await query.edit_message_text(clean_text(RESPONSE_WAIT)) # Редактируем сообщение с кнопкой "Подтвердить"
            
            system_prompt, user_prompt_template, max_tokens_config = (
                (PROMPT_TAROT_SYSTEM, PROMPT_TAROT_USER, CONFIG["OPENAI_MAX_TOKENS_TAROT"]) if data["type"] == "tarot"
                else (PROMPT_MATRIX_SYSTEM, PROMPT_MATRIX_USER, CONFIG["OPENAI_MAX_TOKENS_MATRIX"])
            )
            
            user_final_prompt = user_prompt_template.format(input_text=text_input)
            
            result = await ask_gpt(system_prompt, user_final_prompt, max_tokens_config)

            if result is None: # Ошибка OpenAI
                await query.message.reply_text(clean_text(OPENAI_ERROR_MESSAGE), reply_markup=get_confirm_keyboard(tarot=(data["type"] == "tarot"))) # Даем возможность попробовать снова
                # Не удаляем user_data, чтобы пользователь мог повторить подтверждение
                return 

            if not context.job_queue:
                logger.error("JobQueue не инициализирован!")
                await query.message.reply_text("Критическая ошибка бота. Свяжитесь с @zamira_esoteric.")
                if user_id in user_data: del user_data[user_id] # Очистка в случае критической ошибки
                return
            
            context.job_queue.run_once(delayed_response_job, CONFIG["DELAY_SECONDS"], data=(query.message.chat.id, result, context.bot), name=f"job_for_{user_id}")
            
            logger.info(f"Заявка пользователя {user_id} ({data['type']}) принята и запланирована.")
            if user_id in user_data: # Очищаем данные после успешной постановки в очередь
                del user_data[user_id]
                
    except TelegramError as e:
        logger.error(f"Ошибка Telegram в handle_callback для user {user_id}: {e}")
        # Пытаемся отправить новое сообщение, если редактирование не удалось
        if "message to edit not found" in str(e).lower() or "message is not modified" in str(e).lower():
            # Это может случиться, если пользователь быстро нажимает кнопки или удаляет сообщения
            pass # Не будем спамить пользователя, если он сам что-то делает с сообщениями
        else:
            try:
                await query.message.reply_text("Произошла ошибка при обработке вашего выбора. Попробуйте еще раз.")
            except Exception:
                pass # Если и это не удалось, просто логируем
    except Exception as e:
        logger.error(f"Непредвиденная ошибка в handle_callback для user {user_id}: {e}", exc_info=True)
        try:
            await query.message.reply_text("Произошла внутренняя ошибка. Пожалуйста, попробуйте позже или свяжитесь с @zamira_esoteric.")
        except Exception:
            pass
        if user_id in user_data: # Очистка в случае ошибки
             del user_data[user_id]


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.text:
        user = update.effective_user
        if not user:
            logger.warning("Не удалось получить информацию о пользователе в handle_message.")
            return
            
        user_id = user.id

        if user_id in completed_users:
            await update.message.reply_text(clean_text(PRIVATE_MESSAGE))
            return

        if user_id in user_data and user_data[user_id].get("type"):
            cleaned_text_part = clean_text(update.message.text)
            user_data[user_id]["text"] += "\n" + cleaned_text_part
            logger.debug(f"Пользователь {user_id} добавил текст: {cleaned_text_part[:100]}...")
            # Можно добавить тихое подтверждение, типа "Текст принят. Когда закончите, нажмите 'Подтвердить'."
            # Но это может быть излишним спамом. Пользователь видит кнопки.
        else:
            # Если пользователь пишет без выбора услуги, или после отмены/завершения
            await update.message.reply_text("Пожалуйста, сначала выберите услугу из меню.", reply_markup=get_main_keyboard(include_if_not_started=True))


async def ignore_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(clean_text("Пожалуйста, отправляйте только текстовую информацию для вашего запроса."))

# --- Клавиатуры ---
def get_main_keyboard(include_if_not_started: bool = False):
    keyboard = [
        [InlineKeyboardButton("Расклад Таро 🃏", callback_data="tarot")],
        [InlineKeyboardButton("Матрица судьбы 🌟", callback_data="matrix")],
        [InlineKeyboardButton("Связь со мной 📩", callback_data="contact")],
    ]
    if include_if_not_started: # Добавляем кнопку старт, если пользователь пишет боту впервые или после долгого перерыва
        # Эта логика немного условна здесь, т.к. handle_message вызывается на любое сообщение
        # но может быть полезна, если пользователь потерял первоначальное меню.
        # keyboard.append([InlineKeyboardButton("Начать /start", callback_data="start_command_имитация")]) # Это не сработает как команда
        pass # Лучше просто предложить выбрать услугу
    return InlineKeyboardMarkup(keyboard)

def get_confirm_keyboard(tarot=False):
    button_text = "✅ Подтвердить предысторию" if tarot else "✅ Подтвердить"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(button_text, callback_data="confirm")],
            [InlineKeyboardButton("❌ Отменить и вернуться в меню", callback_data="cancel")],
        ]
    )

def get_back_to_main_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("⬅️ Назад в главное меню", callback_data="back_to_main")],
        ]
    )
    
# --- Запуск бота ---
if __name__ == "__main__":
    try:
        app_builder = ApplicationBuilder().token(BOT_TOKEN)
        # Увеличиваем лимиты, если ожидается много колбэков или джобов одновременно
        app_builder.concurrent_updates(20) 
        app_builder.job_queue(JobQueue()) # Явно создаем JobQueue
        app = app_builder.build()

        app.add_handler(CommandHandler("start", start))
        app.add_handler(CallbackQueryHandler(handle_callback))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        app.add_handler(MessageHandler(~filters.TEXT & ~filters.COMMAND, ignore_media))
        
        logger.info("Бот запускается...")
        app.run_polling()
    except Exception as e:
        logger.critical(f"Критическая ошибка при запуске бота: {e}", exc_info=True)
        # В реальном продакшене здесь может быть система уведомлений администратору
        raise
