import os
import logging
import re
from typing import Dict, Optional, Set, Any, List, Tuple
import asyncio
import json
import openai
import random # Для вариаций ответов
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery, ReactionTypeEmoji
from telegram.constants import ParseMode, ChatAction
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
    "ADMIN_IDS": [7611426172],
    "DELAY_SECONDS_MAIN_SERVICE": 7200,
    # "DELAY_SECONDS_MAIN_SERVICE": 10,  # Тест
    "DELAY_SECONDS_REVIEW_REQUEST": 43200,
    # "DELAY_SECONDS_REVIEW_REQUEST": 20,  # Тест
    "MAX_MESSAGE_LENGTH": 3900,
    "OPENAI_MAX_TOKENS_TAROT": 4000,
    "OPENAI_MAX_TOKENS_MATRIX": 6000,
    "OPENAI_MAX_CONCURRENT": 3,
    "RETRY_DELAY": 7,
    "MAX_RETRIES": 2,
    "COMPLETED_USERS_FILE": "completed_users.json",
    "MIN_TEXT_LENGTH_TAROT_BACKSTORY": 30,
    "MIN_TEXT_LENGTH_TAROT_QUESTION": 10,
}

# --- Настройка API ---
openai.api_key = os.getenv("OPENAI_API_KEY")
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")

# Этот лог будет одним из первых при запуске скрипта глобально
if not BOT_TOKEN or not openai.api_key:
    # Критические ошибки логируем до того, как основной логгер из main блока может быть настроен
    # В данном случае, логгер уже настроен глобально выше.
    logger.critical("ОТСУТСТВУЮТ КЛЮЧЕВЫЕ ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ: TELEGRAM_TOKEN или OPENAI_API_KEY.")
    raise ValueError("Токены TELEGRAM_TOKEN и OPENAI_API_KEY должны быть установлены в переменных окружения.")
logger.info("ГЛОБАЛЬНО: Переменные окружения TELEGRAM_TOKEN и OPENAI_API_KEY найдены.")

# --- Хранилище данных (completed_users) ---
completed_users: Set[int] = set()

def load_completed_users() -> Set[int]:
    try:
        if os.path.exists(CONFIG["COMPLETED_USERS_FILE"]):
            with open(CONFIG["COMPLETED_USERS_FILE"], 'r', encoding='utf-8') as f:
                user_ids = json.load(f)
                logger.info(f"ДАННЫЕ: Загружено {len(user_ids)} пользователей из {CONFIG['COMPLETED_USERS_FILE']}")
                return set(user_ids)
    except Exception as e:
        logger.error(f"ДАННЫЕ: Ошибка загрузки {CONFIG['COMPLETED_USERS_FILE']}: {e}")
    return set()

def save_completed_users(users_set: Set[int]):
    try:
        with open(CONFIG["COMPLETED_USERS_FILE"], 'w', encoding='utf-8') as f:
            json.dump(list(users_set), f, indent=4)
        logger.info(f"ДАННЫЕ: Список {len(users_set)} пользователей сохранен в {CONFIG['COMPLETED_USERS_FILE']}")
    except Exception as e:
        logger.error(f"ДАННЫЕ: Ошибка сохранения {CONFIG['COMPLETED_USERS_FILE']}: {e}")

completed_users = load_completed_users()


# === Текстовые константы ===
WELCOME_TEXT = """
Здравствуйте! ✨ Рада знакомству, меня зовут Замира.
Я таролог и эзотерик, помогаю людям найти ответы и разобраться в себе уже более 15 лет.

Здесь вы можете получить от меня одну бесплатную услугу:
🃏 Расклад на картах Таро
🌟 Разбор Матрицы Судьбы

В качестве энергообмена после консультации я прошу лишь оставить отзыв о моей работе на Авито.

Как это работает?
1.  Нажмите /start (если только что это сделали, отлично!).
2.  Выберите ниже, что вас интересует: Таро или Матрица.
3.  Я задам вам несколько вопросов для подготовки.
4.  Ответ обычно приходит в течение 2-3 часов, так как каждый запрос я разбираю лично.

Готовы начать? Выберите услугу 👇
"""

TAROT_INTRO_TEXT = "Отлично! Вы выбрали расклад на Таро. 🃏\nЧтобы я могла сделать для вас максимально точный и глубокий расклад, мне понадобится некоторая информация. Я буду задавать вопросы по шагам."
MATRIX_INTRO_TEXT = "Прекрасный выбор! Разбор Матрицы Судьбы — это глубокое погружение в ваш потенциал. 🌟\nДля расчета мне нужны будут только ваше полное имя и дата рождения. Сейчас всё спрошу."

ASK_MATRIX_NAME_TEXT = "(Шаг 1 из 2) Пожалуйста, напишите ваше полное имя (или имя того, для кого делаем разбор)."
ASK_MATRIX_DOB_TEXT = "(Шаг 2 из 2) Теперь введите, пожалуйста, дату рождения в формате ДД.ММ.ГГГГ (например, 25.07.1988)."
CONFIRM_DETAILS_MATRIX_TEXT = """
Спасибо! Проверьте, пожалуйста, данные для Матрицы Судьбы:
Имя: {name}
Дата рождения: {dob}

Всё верно? Если да, жмите «Подтвердить».
"""

ASK_TAROT_MAIN_PERSON_NAME_TEXT = "(Шаг 1 из 5) Давайте начнем. На чье имя будем делать расклад Таро? Напишите, пожалуйста, основное имя."
ASK_TAROT_MAIN_PERSON_DOB_TEXT = "(Шаг 2 из 5) Поняла, {name}. Теперь укажите дату рождения этого человека в формате ДД.ММ.ГГГГ (например, 12.08.1985)."
ASK_TAROT_BACKSTORY_TEXT = f"""
(Шаг 3 из 5) Отлично. Теперь очень важный момент: опишите вашу ситуацию или предысторию вопроса. 
Что произошло, что вас беспокоит или интересует? Чем подробнее вы опишете контекст (хотя бы {CONFIG['MIN_TEXT_LENGTH_TAROT_BACKSTORY']} символов), тем глубже я смогу посмотреть.
Например: «Мы с партнером стали часто конфликтовать в последние месяцы, не понимаю, в чем причина и как это исправить» или «Стою перед выбором новой работы, есть два варианта, не могу определиться».
"""
ASK_TAROT_OTHER_PEOPLE_TEXT = """
(Шаг 4 из 5) Понятно. Есть ли другие важные люди, которые имеют прямое отношение к вашему вопросу? 
Если да, напишите их имена и, если возможно, возраст или дату рождения. Это поможет сделать расклад более точным.
Если других людей нет, просто напишите «нет» или «только я/он/она».
Например: «Да, мой партнер Сергей, 35 лет» или «Нет, вопрос только обо мне».
"""
ASK_TAROT_QUESTIONS_TEXT = f"""
(Шаг 5 из 5) И последний шаг: сформулируйте ваш основной вопрос (или 2-3 четких вопроса), на которые вы хотели бы получить ответ от карт Таро. 
Постарайтесь, чтобы вопросы были открытыми и касались сути вашей ситуации (хотя бы {CONFIG['MIN_TEXT_LENGTH_TAROT_QUESTION']} символов на основной вопрос).
Например: «Какие перспективы у моих отношений с Сергеем в ближайшие полгода?» или «Что мне нужно понять о текущей ситуации на работе, чтобы принять верное решение?».
"""
CONFIRM_DETAILS_TAROT_TEXT_DISPLAY = """
Благодарю за подробную информацию! Давайте все еще раз проверим для расклада Таро:

Основное имя: {main_person_name}
Дата рождения: {main_person_dob}

Описание ситуации:
«{backstory}»

Другие участники: {other_people}

Ваши вопросы к картам:
«{questions}»
""" 
EDIT_CHOICE_TEXT = "Если в данных выше есть ошибка, вы можете выбрать пункт для исправления. Если всё верно, нажимайте «Всё верно, подтверждаю»."


RESPONSE_WAIT_VARIANTS = [
    "Благодарю! 🙏 Ваша заявка принята.\nЯ приступаю к работе. Ответ подготовлю для вас в течение примерно 2-3 часов. Ожидайте! ✨",
    "Спасибо! Заявка в обработке. 🌿\nЗамира уже получила ваш запрос и скоро начнет разбор. Ответ будет готов через 2-3 часа.",
    "Принято! Ваш запрос отправлен Замире. 🔮\nОна подготовит для вас ответ в течение 2-3 часов. Немного терпения!",
]

OPENAI_ERROR_MESSAGE = "Ой, кажется, у нас небольшая техническая заминка с подключением к энергопотоку... 🛠️\nПожалуйста, попробуйте подтвердить ваш запрос чуть позже.\nЕсли не получится, напишите мне напрямую: @zamira_esoteric."
SATISFACTION_PROMPT_TEXT = "Ваш {service_type_rus} готов и отправлен вам! 🔮\nНадеюсь, информация была для вас полезной и дала пищу для размышлений.\n\nСкажите, пожалуйста, в целом вы довольны полученным разбором/раскладом?"
DETAILED_FEEDBACK_PROMPT_TEXT = "Спасибо за вашу оценку! Чтобы я могла лучше понимать, что именно вам понравилось или что можно улучшить, выберите один из вариантов:"
REVIEW_PROMISE_TEXT = "Очень рада, что вам понравилось! 😊\nЧуть позже (примерно через 12 часов) я пришлю вам ссылку для отзыва на Авито. \nЭто действительно важно для нашего с вами энергообмена. Считается, что благодарность, выраженная таким образом, помогает предсказаниям гармонично встроиться в вашу жизнь. ✨"
NO_PROBLEM_TEXT = "Понимаю. В любом случае, благодарю за обращение!"
REVIEW_TEXT_DELAYED = "Доброго времени! 🌿\nНадеюсь, у вас всё хорошо и мой {service_type_rus} оказался полезен.\nЕсли вы готовы поделиться впечатлениями, буду очень благодарна за отзыв на Авито. Это помогает и мне, и тем, кто ищет своего проводника.\n\n✍️ Оставить отзыв можно здесь:\nhttps://www.avito.ru/user/review?fid=2_iyd8F4n3P2lfL3lwkg90tujowHx4ZBZ87DElF8B0nlyL6RdaaYzvyPSWRjp4ZyNE\n\nБлагодарю вас за доверие и время! 🙏"
PRIVATE_MESSAGE = "Рада вас снова видеть! Вы уже получали мою бесплатную консультацию. ✨\nЕсли желаете новый расклад или разбор, пожалуйста, напишите мне напрямую: @zamira_esoteric. Обсудим условия. 🌺"
CONTACT_TEXT = "Если у вас есть вопросы или вы хотите заказать платную консультацию, мой контакт для связи: @zamira_esoteric 🌟\nПишите, буду рада помочь!"
CANCEL_TEXT = "Поняла вас. Ваш текущий запрос отменен. Вы всегда можете начать заново из главного меню, нажав /start."

FAQ_ANSWERS = {
    "faq_tarot_question": "Чтобы карты Таро дали вам наиболее точный и полезный ответ, старайтесь задавать открытые вопросы, а не те, что предполагают простой 'да' или 'нет'. Например, вместо 'Выйду ли я замуж в этом году?' лучше спросить: 'Какие перспективы в личной жизни ожидают меня в этом году и на что стоит обратить внимание?'. Конкретика и честность с собой – ключ к глубокому раскладу. 🔮",
    "faq_matrix_data": "Для расчета вашей Матрицы Судьбы мне потребуются только ваше полное имя, данное при рождении, и полная дата рождения (день, месяц, год). Это основа, на которой строится вся карта ваших энергий и потенциала. 🌟",
    "faq_wait_time": "Каждый запрос я разбираю индивидуально, вкладывая время и внимание. Обычно ответ бывает готов в течение 2-3 часов после подтверждения всех данных. Если возможны задержки, я стараюсь предупреждать, но такое бывает редко. ⏳",
    "faq_free_service": "Да, одну услугу – расклад Таро или разбор Матрицы – я предоставляю бесплатно. Это моя возможность познакомиться с вами и показать свой подход. В качестве энергообмена я лишь прошу оставить честный отзыв о моей работе на Авито, если консультация была для вас ценной. 🙏"
}
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
def get_random_variant(variants_list: List[str]) -> str:
    """Возвращает случайный вариант из списка строк."""
    return random.choice(variants_list)

def clean_text(text: str) -> str:
    """Очищает текст от Markdown ** и непечатаемых символов."""
    try:
        text = text.replace("**", "") 
        return "".join(c for c in text if c.isprintable() or c in "\n\r\t ")
    except Exception as e:
        logger.error(f"Ошибка очистки текста: {e}")
        return text
    
def validate_date_format(date_text: str) -> bool:
    """Проверяет, соответствует ли строка формату ДД.ММ.ГГГГ."""
    return bool(re.match(r"^\d{2}\.\d{2}\.\d{4}$", date_text))

def validate_date_semantic(date_text: str) -> bool:
    """Проверяет, является ли дата в формате ДД.ММ.ГГГГ корректной (существующей)."""
    try:
        date = datetime.strptime(date_text, "%d.%m.%Y")
        # Проверка на разумные пределы года
        if date.year < 1900 or date.year > datetime.now().year + 5: # Допускаем небольшой запас в будущее (например, для детей)
            return False
        return True
    except ValueError:
        return False

def is_valid_name(name: str) -> bool:
    """Проверяет, является ли строка валидным именем (без цифр, не дата)."""
    name_stripped = name.strip()
    if len(name_stripped) < 2:
        return False
    # Запрещаем строки, которые полностью соответствуют формату даты
    if validate_date_format(name_stripped):
        return False
    # Разрешаем буквы (кириллица, латиница), пробелы, дефисы, апострофы.
    # И проверяем, что есть хотя бы одна буква, чтобы не прошли только пробелы/дефисы.
    if re.fullmatch(r"^[A-Za-zА-Яа-яЁё\s'-]+$", name_stripped) and any(char.isalpha() for char in name_stripped):
        return True
    return False


async def retry_operation(coro, max_retries=CONFIG["MAX_RETRIES"], delay=CONFIG["RETRY_DELAY"]):
    """Повторяет выполнение асинхронной операции при ошибке."""
    for attempt in range(max_retries):
        try:
            return await coro()
        except Exception as e:
            logger.warning(f"Попытка {attempt + 1} не удалась: {e}")
            if attempt == max_retries - 1:
                raise
            await asyncio.sleep(delay * (2 ** attempt))
    return None # Если все попытки не удались

semaphore = asyncio.Semaphore(CONFIG["OPENAI_MAX_CONCURRENT"])

async def ask_gpt(system_prompt_template: str, user_prompt_content: str, max_tokens: int, context: ContextTypes.DEFAULT_TYPE, user_id_for_error: int) -> Optional[str]:
    """Запрос к OpenAI с обработкой ошибок и уведомлением администратора."""
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
            
            logger.info(f"OpenAI запрос для {user_id_for_error}: system_prompt (начало): {system_prompt[:100]}...")
            logger.info(f"OpenAI запрос для {user_id_for_error}: user_prompt: {user_prompt_content[:100]}...")

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
            # Показываем индикатор "печатает..." во время запроса к OpenAI
            await context.bot.send_chat_action(chat_id=user_id_for_error, action=ChatAction.TYPING)
            return await retry_operation(gpt_call)
        except Exception as e:
            error_msg = f"Критическая ошибка OpenAI после нескольких попыток для пользователя {user_id_for_error}: {e}"
            logger.error(error_msg, exc_info=True)
            await send_admin_notification(context, error_msg, critical=True) 
            return None

async def send_long_message(chat_id: int, message: str, bot_instance):
    """Отправляет длинное сообщение по частям."""
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
            if i == 0: # Если даже первая часть не ушла, сообщаем об общей проблеме
                await bot_instance.send_message(chat_id=chat_id, text=clean_text("Произошла ошибка при отправке ответа. Часть информации может быть утеряна. Свяжитесь с @zamira_esoteric."))
            raise # Передаем ошибку выше

async def send_admin_notification(context: ContextTypes.DEFAULT_TYPE, message: str, critical: bool = False):
    """Отправляет уведомление всем администраторам."""
    full_message = f"🔔 Уведомление Бота Замиры ({'КРИТИЧЕСКАЯ ОШИБКА 🆘' if critical else 'Инфо'}) 🔔\n{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n{message}"
    for admin_id in CONFIG["ADMIN_IDS"]:
        try:
            await context.bot.send_message(chat_id=admin_id, text=full_message)
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление администратору {admin_id}: {e}")
            
# --- Callbacks для JobQueue ---
async def main_service_job(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data 
    user_id: int = job_data["user_id"] # type: ignore
    result: str = job_data["result"] # type: ignore
    service_type: str = job_data["service_type"] # type: ignore
    user_name_for_log = job_data.get("user_name_for_log", str(user_id)) # type: ignore

    service_type_rus_map = {"tarot": "расклад Таро", "matrix": "разбор Матрицы Судьбы"}
    service_type_rus = service_type_rus_map.get(service_type, "услугу")

    logger.info(f"Выполняю отложенную задачу ({service_type_rus}) для {user_name_for_log} ({user_id})")
    try:
        cleaned_result = clean_text(result) 
        await send_long_message(user_id, cleaned_result, context.bot)
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("👍 Да, доволен(льна)", callback_data=f"satisfaction_yes_{service_type}")],
            [InlineKeyboardButton("👎 Нет, не совсем", callback_data=f"satisfaction_no_{service_type}")],
        ])
        await context.bot.send_message(user_id, clean_text(SATISFACTION_PROMPT_TEXT.format(service_type_rus=service_type_rus)), reply_markup=keyboard)
        
        completed_users.add(user_id)
        save_completed_users(completed_users)
        logger.info(f"Пользователь {user_name_for_log} ({user_id}) успешно получил {service_type_rus} и добавлен в completed_users.")
        await send_admin_notification(context, f"✅ Пользователь {user_name_for_log} (ID: {user_id}) успешно получил {service_type_rus}.")

    except Exception as e:
        error_message = f"Критическая ошибка в main_service_job для пользователя {user_name_for_log} ({user_id}): {e}"
        logger.error(error_message, exc_info=True)
        await send_admin_notification(context, error_message, critical=True)
        try:
            await context.bot.send_message(user_id, clean_text("К сожалению, при подготовке вашего ответа произошла серьезная ошибка. Администратор уже уведомлен. Пожалуйста, свяжитесь с @zamira_esoteric для уточнения деталей."))
        except Exception as e_nested:
            logger.error(f"Не удалось отправить сообщение об ошибке в main_service_job пользователю {user_id}: {e_nested}")

async def review_request_job(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data 
    user_id: int = job_data["user_id"] # type: ignore
    service_type: str = job_data["service_type"] # type: ignore
    service_type_rus_map = {"tarot": "расклад Таро", "matrix": "разбор Матрицы Судьбы"}
    service_type_rus = service_type_rus_map.get(service_type, "услугу")
    logger.info(f"Отправка отложенного запроса на отзыв пользователю {user_id} для {service_type_rus}")
    try:
        await context.bot.send_message(user_id, clean_text(REVIEW_TEXT_DELAYED.format(service_type_rus=service_type_rus)))
    except Exception as e:
        logger.error(f"Ошибка при отправке запроса на отзыв пользователю {user_id}: {e}", exc_info=True)

# --- ConversationHandler состояния ---
(CHOOSE_SERVICE, 
 ASK_MATRIX_NAME, ASK_MATRIX_DOB, CONFIRM_MATRIX_DATA,                 # 0, 1, 2, 3
 ASK_TAROT_MAIN_PERSON_NAME, ASK_TAROT_MAIN_PERSON_DOB,              # 4, 5
 ASK_TAROT_BACKSTORY, ASK_TAROT_OTHER_PEOPLE, ASK_TAROT_QUESTIONS,    # 6, 7, 8
 SHOW_TAROT_CONFIRM_OPTIONS # 9 - Показ всех данных Таро и кнопок "Редактировать/Подтвердить"
 ) = range(10) # Обновили количество состояний до 10

CANCEL_CALLBACK_DATA = "cancel_conv_inline" 
EDIT_PREFIX_TAROT = "edit_field_tarot_" 

# --- Клавиатуры ---
def get_cancel_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отменить", callback_data=CANCEL_CALLBACK_DATA)]])

def get_tarot_edit_keyboard() -> InlineKeyboardMarkup: 
    buttons = [
        [InlineKeyboardButton("✏️ Имя основное", callback_data=f"{EDIT_PREFIX_TAROT}main_person_name")],
        [InlineKeyboardButton("✏️ Дату рожд. основную", callback_data=f"{EDIT_PREFIX_TAROT}main_person_dob")],
        [InlineKeyboardButton("✏️ Предысторию", callback_data=f"{EDIT_PREFIX_TAROT}backstory")],
        [InlineKeyboardButton("✏️ Других участников", callback_data=f"{EDIT_PREFIX_TAROT}other_people")],
        [InlineKeyboardButton("✏️ Вопросы к картам", callback_data=f"{EDIT_PREFIX_TAROT}questions")],
        [InlineKeyboardButton("✅ Всё верно, подтверждаю", callback_data="confirm_final_tarot")],
        [InlineKeyboardButton("❌ Отменить всё и начать заново", callback_data=CANCEL_CALLBACK_DATA)]
    ]
    return InlineKeyboardMarkup(buttons)

# --- Функции ConversationHandler ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if not user: 
        logger.warning("Не удалось получить пользователя в start_command")
        return ConversationHandler.END
    
    if user.id in completed_users:
        await update.message.reply_text(clean_text(PRIVATE_MESSAGE))
        return ConversationHandler.END

    if context.user_data: 
        context.user_data.clear() 
        
    keyboard = [
        [InlineKeyboardButton("🃏 Расклад Таро", callback_data="tarot")],
        [InlineKeyboardButton("🌟 Матрица Судьбы", callback_data="matrix")],
        [InlineKeyboardButton("📩 Связь со мной", callback_data="contact_direct")],
        [InlineKeyboardButton("💡 Помощь / FAQ", callback_data="help_section")] 
    ]
    await update.message.reply_text(clean_text(WELCOME_TEXT), reply_markup=InlineKeyboardMarkup(keyboard))
    return CHOOSE_SERVICE

async def choose_service_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_data = context.user_data 
    if user_data is None: 
        user_data = context.user_data = {}

    service_type_or_action = query.data # Может быть 'tarot', 'matrix', 'contact_direct', 'back_to_start', 'help_section'
    
    if service_type_or_action == "contact_direct":
        await query.edit_message_text(clean_text(CONTACT_TEXT), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад в меню", callback_data="back_to_start")]]))
        return CHOOSE_SERVICE 
    elif service_type_or_action == "back_to_start":
        keyboard_main = [
            [InlineKeyboardButton("🃏 Расклад Таро", callback_data="tarot")],
            [InlineKeyboardButton("🌟 Матрица Судьбы", callback_data="matrix")],
            [InlineKeyboardButton("📩 Связь со мной", callback_data="contact_direct")],
            [InlineKeyboardButton("💡 Помощь / FAQ", callback_data="help_section")]
        ]
        await query.edit_message_text(clean_text(WELCOME_TEXT), reply_markup=InlineKeyboardMarkup(keyboard_main))
        return CHOOSE_SERVICE
    elif service_type_or_action == "help_section": 
        # Удаляем предыдущее сообщение с кнопками выбора услуги, чтобы не было путаницы
        try:
            await query.delete_message()
        except Exception as e:
            logger.warning(f"Не удалось удалить сообщение в choose_service_callback при переходе в help: {e}")
        # Вызываем help_command, который отправит новое сообщение с FAQ
        await help_command(update, context) 
        # Диалог ConversationHandler не должен завершаться, пользователь может вернуться к выбору услуги.
        # Но так как help_command - это CommandHandler, он не вернет состояние для ConvHandler.
        # Поэтому мы просто остаемся в CHOOSE_SERVICE, но пользователь увидит новое сообщение от /help.
        # Либо можно сделать help частью ConvHandler, но это усложнит.
        # Простой вариант: пользователь после /help должен будет снова нажать /start или кнопку из старого сообщения, если оно осталось.
        # Чтобы этого избежать, можно help_command сделать так, чтобы он возвращал клавиатуру главного меню.
        # Пока оставим так: help_command сам по себе, а ConvHandler ждет новый ввод или /start.
        # Лучше, чтобы help_command был вне ConvHandler. Этот callback 'help_section' нужен для кнопок.
        # Отправляем сообщение и возвращаем пользователя в начало диалога (или просто ничего не возвращаем, если help сам все делает)
        # await query.message.reply_text("Для помощи используйте команду /help.") # Если help_command не вызывается отсюда
        return ConversationHandler.END # Завершаем текущий диалог, чтобы /help сработал как независимая команда
                                       # Это не лучший UX, если пользователь нажал кнопку помощи из меню выбора услуги.
                                       # Правильнее было бы, если help_command мог быть вызван и отсюда, и показал бы инфо,
                                       # а потом пользователь вернулся бы к CHOOSE_SERVICE.
                                       # Упрощенный вариант: завершаем диалог, пользователь видит сообщение от /help и может начать заново.


    # Если это выбор услуги (tarot или matrix)
    user_data["service_type"] = service_type_or_action # type: ignore
    user_data["current_step"] = 1 # type: ignore 
    
    if service_type_or_action == "tarot":
        user_data["total_steps"] = 5 # type: ignore 
        await query.edit_message_text(text=clean_text(TAROT_INTRO_TEXT), reply_markup=None) # Убираем кнопки выбора услуги
        prompt_text = ASK_TAROT_MAIN_PERSON_NAME_TEXT 
        await query.message.reply_text(clean_text(prompt_text), reply_markup=get_cancel_keyboard())
        return ASK_TAROT_MAIN_PERSON_NAME
    elif service_type_or_action == "matrix":
        user_data["total_steps"] = 2 # type: ignore 
        await query.edit_message_text(text=clean_text(MATRIX_INTRO_TEXT), reply_markup=None) # Убираем кнопки выбора услуги
        prompt_text = ASK_MATRIX_NAME_TEXT 
        await query.message.reply_text(clean_text(prompt_text), reply_markup=get_cancel_keyboard())
        return ASK_MATRIX_NAME
    else:
        logger.warning(f"Неизвестный service_type_or_action в choose_service_callback: {service_type_or_action}")
        keyboard_main_fallback = [
            [InlineKeyboardButton("🃏 Расклад Таро", callback_data="tarot")],
            [InlineKeyboardButton("🌟 Матрица Судьбы", callback_data="matrix")],
            [InlineKeyboardButton("📩 Связь со мной", callback_data="contact_direct")],
            [InlineKeyboardButton("💡 Помощь / FAQ", callback_data="help_section")]
        ]
        await query.edit_message_text(clean_text(WELCOME_TEXT), reply_markup=InlineKeyboardMarkup(keyboard_main_fallback))
        return CHOOSE_SERVICE
        # --- Функции для Матрицы (продолжение ConversationHandler) ---
async def ask_matrix_name_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_data = context.user_data # type: ignore
    name_input = update.message.text
    if not name_input or not is_valid_name(name_input):
        error_msg = f"Хм, «{name_input or ''}» не очень похоже на имя. Имя должно содержать только буквы, пробелы, дефисы или апострофы, и быть не короче двух символов. Попробуйте еще раз, пожалуйста."
        await update.message.reply_text(clean_text(error_msg), reply_markup=get_cancel_keyboard())
        return ASK_MATRIX_NAME
        
    user_data["matrix_name"] = clean_text(name_input.strip())
    user_data["current_step"] = 2 # type: ignore
    
    reply_variants = [
        ASK_MATRIX_DOB_TEXT, # Уже содержит (Шаг 2 из 2)
        f"(Шаг 2 из 2) Отлично, {user_data['matrix_name']}! Теперь нужна ваша дата рождения (ДД.ММ.ГГГГ).",
        f"(Шаг 2 из 2) Записала, {user_data['matrix_name']}. Далее, пожалуйста, дату вашего рождения в формате ДД.ММ.ГГГГ."
    ]
    await update.message.reply_text(clean_text(get_random_variant(reply_variants)), reply_markup=get_cancel_keyboard())
    return ASK_MATRIX_DOB

async def ask_matrix_dob_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_data = context.user_data # type: ignore
    dob_text_input = update.message.text
    if not dob_text_input:
        await update.message.reply_text("Вы не ввели дату. Пожалуйста, введите дату в формате ДД.ММ.ГГГГ.", reply_markup=get_cancel_keyboard())
        return ASK_MATRIX_DOB

    dob_text = dob_text_input.strip()
    if not validate_date_format(dob_text):
        await update.message.reply_text(f"Формат даты «{dob_text}» неверный. Пожалуйста, введите дату в формате ДД.ММ.ГГГГ (например, 15.03.1990).", reply_markup=get_cancel_keyboard())
        return ASK_MATRIX_DOB
    if not validate_date_semantic(dob_text):
        await update.message.reply_text(f"Дата «{dob_text}» кажется некорректной (например, неверный год или несуществующий день). Пожалуйста, проверьте и введите снова.", reply_markup=get_cancel_keyboard())
        return ASK_MATRIX_DOB
        
    user_data["matrix_dob"] = clean_text(dob_text)
    confirm_text = CONFIRM_DETAILS_MATRIX_TEXT.format(name=user_data["matrix_name"], dob=user_data["matrix_dob"])
    keyboard = [[InlineKeyboardButton("✅ Всё верно, подтверждаю", callback_data="confirm_final_matrix")],
                [InlineKeyboardButton("❌ Отменить", callback_data=CANCEL_CALLBACK_DATA)]]
    await update.message.reply_text(confirm_text, reply_markup=InlineKeyboardMarkup(keyboard))
    return CONFIRM_MATRIX_DATA

# --- Функции для Таро (продолжение ConversationHandler) ---
async def ask_tarot_main_person_name_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_data = context.user_data # type: ignore
    name_input = update.message.text
    if not name_input or not is_valid_name(name_input):
        error_msg = f"Хм, «{name_input or ''}» не очень похоже на имя. Имя должно содержать только буквы, пробелы, дефисы или апострофы. Попробуйте еще раз."
        await update.message.reply_text(clean_text(error_msg), reply_markup=get_cancel_keyboard())
        return ASK_TAROT_MAIN_PERSON_NAME
        
    user_data["tarot_main_person_name"] = clean_text(name_input.strip())
    
    if user_data.pop("editing_this_specific_field", None) == f"{EDIT_PREFIX_TAROT}main_person_name": # type: ignore 
        return await show_tarot_confirm_options_message(update, context)

    user_data["current_step"] = 2 # type: ignore
    prompt_text = ASK_TAROT_MAIN_PERSON_DOB_TEXT.format(name=user_data["tarot_main_person_name"])
    await update.message.reply_text(clean_text(prompt_text), reply_markup=get_cancel_keyboard())
    return ASK_TAROT_MAIN_PERSON_DOB

async def ask_tarot_main_person_dob_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_data = context.user_data # type: ignore
    dob_text_input = update.message.text
    if not dob_text_input:
        await update.message.reply_text("Вы не ввели дату. Пожалуйста, введите дату в формате ДД.ММ.ГГГГ.", reply_markup=get_cancel_keyboard())
        return ASK_TAROT_MAIN_PERSON_DOB
    
    dob_text = dob_text_input.strip()
    if not validate_date_format(dob_text):
        await update.message.reply_text(f"Формат даты «{dob_text}» неверный. Пожалуйста, введите дату в формате ДД.ММ.ГГГГ.", reply_markup=get_cancel_keyboard())
        return ASK_TAROT_MAIN_PERSON_DOB
    if not validate_date_semantic(dob_text):
        await update.message.reply_text(f"Дата «{dob_text}» кажется некорректной. Проверьте год и формат.", reply_markup=get_cancel_keyboard())
        return ASK_TAROT_MAIN_PERSON_DOB
        
    user_data["tarot_main_person_dob"] = clean_text(dob_text)

    if user_data.pop("editing_this_specific_field", None) == f"{EDIT_PREFIX_TAROT}main_person_dob": # type: ignore
        return await show_tarot_confirm_options_message(update, context)

    user_data["current_step"] = 3 # type: ignore
    await update.message.reply_text(clean_text(ASK_TAROT_BACKSTORY_TEXT), reply_markup=get_cancel_keyboard())
    return ASK_TAROT_BACKSTORY

async def ask_tarot_backstory_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_data = context.user_data # type: ignore
    backstory_input = update.message.text
    min_len = CONFIG.get("MIN_TEXT_LENGTH_TAROT_BACKSTORY", 30)
    if not backstory_input or len(backstory_input.strip()) < min_len:
        await update.message.reply_text(f"Пожалуйста, опишите ситуацию подробнее (не менее {min_len} символов). Это важно для точности расклада.", reply_markup=get_cancel_keyboard())
        return ASK_TAROT_BACKSTORY
        
    user_data["tarot_backstory"] = clean_text(backstory_input.strip())

    if user_data.pop("editing_this_specific_field", None) == f"{EDIT_PREFIX_TAROT}backstory": # type: ignore
        return await show_tarot_confirm_options_message(update, context)

    user_data["current_step"] = 4 # type: ignore
    await update.message.reply_text(clean_text(ASK_TAROT_OTHER_PEOPLE_TEXT), reply_markup=get_cancel_keyboard())
    return ASK_TAROT_OTHER_PEOPLE

async def ask_tarot_other_people_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_data = context.user_data # type: ignore
    other_people_input = update.message.text
    if not other_people_input or len(other_people_input.strip()) < 2: 
        await update.message.reply_text("Пожалуйста, укажите других участников или напишите 'нет', если их нет.", reply_markup=get_cancel_keyboard())
        return ASK_TAROT_OTHER_PEOPLE
        
    user_data["tarot_other_people"] = clean_text(other_people_input.strip())

    if user_data.pop("editing_this_specific_field", None) == f"{EDIT_PREFIX_TAROT}other_people": # type: ignore
        return await show_tarot_confirm_options_message(update, context)

    user_data["current_step"] = 5 # type: ignore
    await update.message.reply_text(clean_text(ASK_TAROT_QUESTIONS_TEXT), reply_markup=get_cancel_keyboard())
    return ASK_TAROT_QUESTIONS

async def ask_tarot_questions_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_data = context.user_data # type: ignore
    questions_input = update.message.text
    min_len = CONFIG.get("MIN_TEXT_LENGTH_TAROT_QUESTION", 10)
    if not questions_input or len(questions_input.strip()) < min_len:
        await update.message.reply_text(f"Пожалуйста, сформулируйте ваш вопрос(ы) к картам (не менее {min_len} символов). Если вопросов несколько, напишите их все в одном сообщении.", reply_markup=get_cancel_keyboard())
        return ASK_TAROT_QUESTIONS
        
    user_data["tarot_questions"] = clean_text(questions_input.strip())

    # После сбора всех данных (или редактирования последнего поля), переходим к экрану подтверждения/редактирования
    return await show_tarot_confirm_options_message(update, context)


async def show_tarot_confirm_options_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_data = context.user_data # type: ignore
    
    effective_message = update.effective_message 
    if not effective_message:
        logger.warning("show_tarot_confirm_options_message: effective_message is None, trying to send new.")
        if update.effective_chat:
            effective_message = await context.bot.send_message(update.effective_chat.id, "Проверяем ваши данные...")
        else: 
            logger.error("show_tarot_confirm_options_message: effective_chat is None, cannot proceed.")
            return ConversationHandler.END

    if not user_data or user_data.get("service_type") != "tarot": 
        await effective_message.reply_text(clean_text("Произошла ошибка при сборе данных для Таро. Давайте начнем сначала."), reply_markup=get_cancel_keyboard())
        # Вместо start_command, который может быть не update.message, просто завершаем диалог
        if user_data: user_data.clear()
        return ConversationHandler.END 

    confirm_text_display = CONFIRM_DETAILS_TAROT_TEXT_DISPLAY.format(
        main_person_name=user_data.get("tarot_main_person_name", "-"),
        main_person_dob=user_data.get("tarot_main_person_dob", "-"),
        backstory=user_data.get("tarot_backstory", "-"),
        other_people=user_data.get("tarot_other_people", "-"),
        questions=user_data.get("tarot_questions", "-")
    )
    
    keyboard = get_tarot_edit_keyboard()
    
    message_to_edit_id = user_data.pop("tarot_confirm_options_message_id", None) # type: ignore
    new_message_with_buttons = None

    # Отправляем всегда новые сообщения для подтверждения, чтобы избежать путаницы с редактированием старых
    await effective_message.reply_text(clean_text(confirm_text_display)) 
    new_message_with_buttons = await effective_message.reply_text(clean_text(EDIT_CHOICE_TEXT), reply_markup=keyboard)
    
    if user_data and new_message_with_buttons: # type: ignore
        user_data["tarot_confirm_options_message_id"] = new_message_with_buttons.message_id # type: ignore
        
    return SHOW_TAROT_CONFIRM_OPTIONS

async def edit_field_tarot_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_data = context.user_data
    if not user_data: return ConversationHandler.END # type: ignore

    if query.message:
        try:
            await query.delete_message() # Удаляем сообщение с кнопками "Редактировать/Подтвердить"
            user_data.pop("tarot_confirm_options_message_id", None) # type: ignore
        except Exception as e:
            logger.warning(f"Не удалось удалить сообщение ({query.message.message_id}) с выбором редактирования: {e}")

    field_to_edit_key_from_callback = query.data # e.g., "edit_field_tarot_main_person_name"
    
    # Этот флаг поможет соответствующей функции ask_..._message понять, что мы в режиме редактирования
    user_data["editing_this_specific_field"] = field_to_edit_key_from_callback # type: ignore 

    # Очищаем старое значение поля, чтобы запросить его заново
    field_name_in_user_data = field_to_edit_key_from_callback.replace(EDIT_PREFIX_TAROT, "tarot_")
    user_data.pop(field_name_in_user_data, None) # type: ignore

    next_state_map = {
        f"{EDIT_PREFIX_TAROT}main_person_name": (ASK_TAROT_MAIN_PERSON_NAME, ASK_TAROT_MAIN_PERSON_NAME_TEXT),
        f"{EDIT_PREFIX_TAROT}main_person_dob": (ASK_TAROT_MAIN_PERSON_DOB, ASK_TAROT_MAIN_PERSON_DOB_TEXT.format(name=user_data.get("tarot_main_person_name", "для него/нее"))), # type: ignore
        f"{EDIT_PREFIX_TAROT}backstory": (ASK_TAROT_BACKSTORY, ASK_TAROT_BACKSTORY_TEXT),
        f"{EDIT_PREFIX_TAROT}other_people": (ASK_TAROT_OTHER_PEOPLE, ASK_TAROT_OTHER_PEOPLE_TEXT),
        f"{EDIT_PREFIX_TAROT}questions": (ASK_TAROT_QUESTIONS, ASK_TAROT_QUESTIONS_TEXT),
    }

    if field_to_edit_key_from_callback in next_state_map:
        next_state, prompt_text = next_state_map[field_to_edit_key_from_callback]
        await query.message.reply_text(clean_text(prompt_text), reply_markup=get_cancel_keyboard())
        return next_state
    
    logger.warning(f"Неизвестное поле для редактирования Таро: {field_to_edit_key_from_callback}")
    return await show_tarot_confirm_options_message(update, context) # type: ignore


# --- Общая функция подтверждения и вызова OpenAI ---
async def process_final_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE, service_type: str) -> int:
    query = update.callback_query
    await query.answer()
    user_data = context.user_data # type: ignore
    user_id = query.from_user.id
    user_name_for_log = query.from_user.full_name or str(user_id)
    # Сохраняем имя пользователя из Telegram для логов в main_service_job
    user_data["user_name_for_log"] = user_name_for_log # type: ignore

    message_id_to_remove = user_data.pop("tarot_confirm_options_message_id", None) if service_type == "tarot" else (query.message.message_id if query.message else None) # type: ignore
    
    response_wait_text = get_random_variant(RESPONSE_WAIT_VARIANTS)
    sent_confirmation_msg = None

    if message_id_to_remove and query.message and query.message.chat:
        try:
            sent_confirmation_msg = await context.bot.edit_message_text(
                chat_id=query.message.chat.id, message_id=message_id_to_remove,
                text=clean_text(response_wait_text), reply_markup=None )
        except TelegramError as e:
            if "Message is not modified" not in str(e) and "message to edit not found" not in str(e).lower(): 
                logger.error(f"Ошибка edit_message_text в process_final_confirmation: {e}. Отправляю новое.")
                sent_confirmation_msg = await query.message.reply_text(text=clean_text(response_wait_text))
            elif "message to edit not found" in str(e).lower(): 
                 sent_confirmation_msg = await query.message.reply_text(text=clean_text(response_wait_text))
            else: 
                sent_confirmation_msg = query.message 
    else: 
        sent_confirmation_msg = await query.message.reply_text(text=clean_text(response_wait_text))
             
    if sent_confirmation_msg: 
        try:
            await context.bot.set_message_reaction(
                chat_id=sent_confirmation_msg.chat_id, message_id=sent_confirmation_msg.message_id, 
                reaction=[ReactionTypeEmoji("⚡")] )
        except Exception as e_react:
            logger.warning(f"Не удалось поставить реакцию на сообщение {sent_confirmation_msg.message_id}: {e_react}")
             
    input_for_gpt = ""
    system_prompt_template = ""
    user_prompt_template_str = ""
    max_tokens_val = 0
    confirm_text_on_error = "" 
    next_confirm_state_on_error = ConversationHandler.END 

    if service_type == "tarot":
        input_for_gpt = (
            f"Основное имя: {user_data.get('tarot_main_person_name', 'Не указано')}\n"
            f"Дата рождения: {user_data.get('tarot_main_person_dob', 'Не указано')}\n"
            f"Описание ситуации: {user_data.get('tarot_backstory', 'Не указано')}\n"
            f"Другие участники: {user_data.get('tarot_other_people', 'Не указано')}\n"
            f"Вопросы к картам: {user_data.get('tarot_questions', 'Не указано')}" )
        system_prompt_template = PROMPT_TAROT_SYSTEM
        user_prompt_template_str = PROMPT_TAROT_USER
        max_tokens_val = CONFIG["OPENAI_MAX_TOKENS_TAROT"]
        confirm_text_on_error = CONFIRM_DETAILS_TAROT_TEXT_DISPLAY.format( 
            main_person_name=user_data.get('tarot_main_person_name', '?'),
            main_person_dob=user_data.get('tarot_main_person_dob', '?'),
            backstory=user_data.get('tarot_backstory', '?'),
            other_people=user_data.get('tarot_other_people', '?'),
            questions=user_data.get('tarot_questions', '?') )
        next_confirm_state_on_error = SHOW_TAROT_CONFIRM_OPTIONS 
    elif service_type == "matrix":
        input_for_gpt = (
            f"Имя: {user_data.get('matrix_name', 'Не указано')}\n"
            f"Дата рождения: {user_data.get('matrix_dob', 'Не указано')}" )
        system_prompt_template = PROMPT_MATRIX_SYSTEM
        user_prompt_template_str = PROMPT_MATRIX_USER
        max_tokens_val = CONFIG["OPENAI_MAX_TOKENS_MATRIX"]
        confirm_text_on_error = CONFIRM_DETAILS_MATRIX_TEXT.format(
            name=user_data.get('matrix_name', '?'), dob=user_data.get('matrix_dob', '?') )
        next_confirm_state_on_error = CONFIRM_MATRIX_DATA

    final_user_prompt = user_prompt_template_str.format(input_text=input_for_gpt)
    result = await ask_gpt(system_prompt_template, final_user_prompt, max_tokens_val, context, user_id)

    if result is None: 
        await query.message.reply_text(clean_text(OPENAI_ERROR_MESSAGE)) 
        
        keyboard_retry_callback_data = f"confirm_final_{service_type}"
        keyboard_retry = [[InlineKeyboardButton("Попробовать подтвердить снова", callback_data=keyboard_retry_callback_data)],
                          [InlineKeyboardButton("❌ Отменить", callback_data=CANCEL_CALLBACK_DATA)]] 
        try: 
            await query.message.reply_text(text=clean_text(confirm_text_on_error), reply_markup=InlineKeyboardMarkup(keyboard_retry))
        except Exception as e_reply:
            logger.error(f"Не удалось отправить кнопки повтора после ошибки OpenAI: {e_reply}")
        return next_confirm_state_on_error

    if not context.job_queue:
        logger.error("JobQueue не инициализирован!")
        await query.message.reply_text("Критическая ошибка бота. Свяжитесь с @zamira_esoteric.")
        await send_admin_notification(context, "JobQueue не инициализирован при попытке запланировать задачу!", critical=True)
        if user_data: user_data.clear() 
        return ConversationHandler.END
    
    job_payload = {"user_id": user_id, "result": result, "service_type": service_type, "user_name_for_log": user_name_for_log}
    context.job_queue.run_once(main_service_job, CONFIG["DELAY_SECONDS_MAIN_SERVICE"], data=job_payload, name=f"main_job_{user_id}") # type: ignore
    
    logger.info(f"Заявка пользователя {user_name_for_log} ({user_id}) ({service_type}) принята и запланирована.")
    await send_admin_notification(context, f"📨 Новая заявка от {user_name_for_log} (ID: {user_id}) на {service_type}. Запланирована.")
    if user_data: user_data.clear() 
    return ConversationHandler.END

async def confirm_matrix_data_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await process_final_confirmation(update, context, "matrix")

async def confirm_tarot_data_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await process_final_confirmation(update, context, "tarot")

# --- Общая логика отмены ---
async def common_cancel_logic(update: Update, context: ContextTypes.DEFAULT_TYPE, query: Optional[CallbackQuery] = None) -> int:
    user_data = context.user_data
    if user_data:
        user_data.clear() # type: ignore
    
    cancel_message_text = clean_text(CANCEL_TEXT)
    
    chat_to_reply = None
    effective_message_to_handle = None

    if query: 
        effective_message_to_handle = query.message
        if effective_message_to_handle:
            chat_to_reply = effective_message_to_handle.chat
            try:
                await query.edit_message_text(text=cancel_message_text, reply_markup=None)
            except TelegramError as e:
                if "Message is not modified" not in str(e) and "message to edit not found" not in str(e).lower(): 
                    logger.warning(f"Не удалось отредактировать сообщение ({effective_message_to_handle.message_id}) при отмене через кнопку: {e}")
                    await effective_message_to_handle.reply_text(text=cancel_message_text) 
                elif "message to edit not found" in str(e).lower(): 
                     await effective_message_to_handle.reply_text(text=cancel_message_text)
        else: # Если query.message None, но есть query.from_user (для chat_id)
            if query.from_user : chat_to_reply = await context.bot.get_chat(query.from_user.id) # type: ignore
            await context.bot.send_message(chat_id=query.from_user.id, text=cancel_message_text)


    elif update.message: 
        await update.message.reply_text(text=cancel_message_text)
        chat_to_reply = update.message.chat

    if chat_to_reply: 
        keyboard_main = [
            [InlineKeyboardButton("🃏 Расклад Таро", callback_data="tarot")],
            [InlineKeyboardButton("🌟 Матрица Судьбы", callback_data="matrix")],
            [InlineKeyboardButton("📩 Связь со мной", callback_data="contact_direct")],
            [InlineKeyboardButton("💡 Помощь / FAQ", callback_data="help_section")]
        ]
        try:
            # Отправляем новое сообщение WELCOME_TEXT, так как предыдущее было заменено на CANCEL_TEXT
            await context.bot.send_message(chat_id=chat_to_reply.id, text=clean_text(WELCOME_TEXT), reply_markup=InlineKeyboardMarkup(keyboard_main))
        except Exception as e:
            logger.error(f"Не удалось отправить WELCOME_TEXT после отмены: {e}")
    
    return ConversationHandler.END


async def cancel_conv_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id if update.effective_user else "Unknown"
    logger.info(f"Пользователь {user_id} отменил диалог командой /cancel.")
    return await common_cancel_logic(update, context)

async def cancel_conv_inline_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer() 
    logger.info(f"Пользователь {query.from_user.id} отменил диалог через инлайн кнопку.")
    return await common_cancel_logic(update, context, query=query)

# --- Обработчики вне ConversationHandler ---
async def handle_satisfaction_and_other_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data: return
    await query.answer()
    user_id = query.from_user.id

    if query.data.startswith("satisfaction_"):
        parts = query.data.split("_")
        answer = parts[1] 
        service_type = parts[2] if len(parts) > 2 else "услугу" # fallback
        
        original_message_text = query.message.text if query.message else clean_text(SATISFACTION_PROMPT_TEXT.format(service_type_rus="консультацию"))
        
        if answer == "yes":
            detailed_feedback_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("👍 Очень точно!", callback_data=f"detailed_fb_accurate_{service_type}")],
                [InlineKeyboardButton("👌 Полезно, но есть вопросы", callback_data=f"detailed_fb_useful_qs_{service_type}")],
                [InlineKeyboardButton("🙂 Общие моменты совпали", callback_data=f"detailed_fb_general_{service_type}")],
                [InlineKeyboardButton("➡️ Просто спасибо (пропустить)", callback_data=f"detailed_fb_skip_{service_type}")],
            ])
            try:
                await query.edit_message_text(
                    text=f"{original_message_text}\n\n{clean_text(DETAILED_FEEDBACK_PROMPT_TEXT)}", 
                    reply_markup=detailed_feedback_keyboard
                )
            except TelegramError as e: # Если не удалось отредактировать (например, сообщение слишком старое)
                 logger.warning(f"Не удалось отредактировать сообщение для детального фидбека: {e}")
                 await query.message.reply_text(text=clean_text(DETAILED_FEEDBACK_PROMPT_TEXT), reply_markup=detailed_feedback_keyboard)

        elif answer == "no":
            await query.edit_message_text(text=f"{original_message_text}\n\n{clean_text(NO_PROBLEM_TEXT)}", reply_markup=None)
    
    elif query.data.startswith("detailed_fb_"): 
        feedback_parts = query.data.split("_") 
        feedback_type = feedback_parts[2]
        service_type = feedback_parts[3] if len(feedback_parts) > 3 else "услугу"

        logger.info(f"Пользователь {user_id} дал детальный фидбек: {feedback_type} для {service_type}")

        thank_you_for_feedback_text = "Спасибо за ваш отклик! Это очень помогает мне становиться лучше."
        if feedback_type == "skip":
            thank_you_for_feedback_text = "Понимаю. Спасибо за использование сервиса!"
        
        # Пытаемся извлечь основной текст из сообщения с кнопками детального фидбека
        original_satisfaction_text = query.message.text.split(clean_text(DETAILED_FEEDBACK_PROMPT_TEXT))[0].strip() if query.message and query.message.text else ""
        
        try:
            await query.edit_message_text(
                text=f"{original_satisfaction_text}\n\n{thank_you_for_feedback_text}",
                reply_markup=None
            )
        except TelegramError as e:
            logger.warning(f"Не удалось отредактировать сообщение после детального фидбека: {e}")
            # Отправляем новое сообщение, если редактирование не удалось
            await query.message.reply_text(thank_you_for_feedback_text)

        if feedback_type != "skip":
            # Отправляем новое сообщение с REVIEW_PROMISE_TEXT, т.к. предыдущее было отредактировано
            await query.message.reply_text(clean_text(REVIEW_PROMISE_TEXT)) 
            if not context.job_queue:
                logger.error(f"JobQueue не найден при планировании запроса отзыва после детального фидбека для {user_id}")
                return
            job_payload = {"user_id": user_id, "service_type": service_type}
            context.job_queue.run_once(review_request_job, CONFIG["DELAY_SECONDS_REVIEW_REQUEST"], data=job_payload, name=f"review_req_job_{user_id}") # type: ignore
            logger.info(f"Запланирован запрос отзыва для {user_id} через {CONFIG['DELAY_SECONDS_REVIEW_REQUEST']} секунд после детального фидбека '{feedback_type}'.")

async def post_fallback_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.effective_user: 
        user_id = update.effective_user.id
        if user_id in completed_users:
            await update.message.reply_text(clean_text(PRIVATE_MESSAGE))
            return
        
        is_in_conversation = context.user_data and context.user_data.get(ConversationHandler.STATE) is not None # type: ignore
        if not is_in_conversation:
            await update.message.reply_text(
            "Кажется, мы не находимся в процессе оформления запроса. Нажмите /start, чтобы начать или выбрать услугу 🔮.",
        )

# --- Админские команды ---
async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or user.id not in CONFIG["ADMIN_IDS"]:
        await update.message.reply_text("Эта команда доступна только администратору.")
        return

    completed_count = len(completed_users)
    active_jobs = context.job_queue.jobs() if context.job_queue else [] # type: ignore
    pending_main_jobs = 0
    pending_review_jobs = 0
    for job in active_jobs:
        if job.name and job.name.startswith("main_job_"):
            pending_main_jobs += 1
        elif job.name and job.name.startswith("review_req_job_"):
            pending_review_jobs += 1
    
    stats_message = (
        f"Статистика Бота Замиры 📊:\n"
        f"----------------------------\n"
        f"Всего выполненных бесплатных услуг: {completed_count}\n"
        f"Активных задач на выполнение услуги: {pending_main_jobs}\n"
        f"Активных задач на отправку запроса отзыва: {pending_review_jobs}\n"
        f"----------------------------\n"
        f"Время сервера: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    await update.message.reply_text(stats_message)

async def admin_clear_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or user.id not in CONFIG["ADMIN_IDS"]:
        await update.message.reply_text("Эта команда доступна только администратору.")
        return

    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Пожалуйста, укажите ID пользователя: /clear_user <ID>")
        return
    
    user_to_clear_id = int(args[0])
    if user_to_clear_id in completed_users:
        completed_users.remove(user_to_clear_id)
        save_completed_users(completed_users)
        await update.message.reply_text(f"Пользователь {user_to_clear_id} удален из списка 'completed'. Он сможет получить бесплатную услугу снова.")
        logger.info(f"Администратор {user.id} удалил {user_to_clear_id} из completed_users.")
        await send_admin_notification(context, f"Администратор {user.id} удалил пользователя {user_to_clear_id} из списка completed.")
    else:
        await update.message.reply_text(f"Пользователь {user_to_clear_id} не найден в списке 'completed'.")

async def admin_get_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or user.id not in CONFIG["ADMIN_IDS"]:
        await update.message.reply_text("Эта команда доступна только администратору.")
        return
    try:
        await update.message.reply_document(document=open("bot.log", "rb"), filename="bot_activity.log")
    except FileNotFoundError:
        await update.message.reply_text("Файл логов 'bot.log' не найден.")
    except Exception as e:
        await update.message.reply_text(f"Ошибка при отправке логов: {e}")
        logger.error(f"Ошибка отправки логов администратору: {e}")

async def admin_get_completed_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or user.id not in CONFIG["ADMIN_IDS"]:
        await update.message.reply_text("Эта команда доступна только администратору.")
        return
    try:
        await update.message.reply_document(document=open(CONFIG["COMPLETED_USERS_FILE"], "rb"), filename=CONFIG["COMPLETED_USERS_FILE"])
    except FileNotFoundError:
        await update.message.reply_text(f"Файл '{CONFIG['COMPLETED_USERS_FILE']}' не найден.")
    except Exception as e:
        await update.message.reply_text(f"Ошибка при отправке списка: {e}")
        logger.error(f"Ошибка отправки списка completed_users администратору: {e}")

# --- Команда /help и обработчик FAQ ---
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("❓ Как задать вопрос для Таро?", callback_data="faq_tarot_question")],
        [InlineKeyboardButton("❓ Что нужно для Матрицы Судьбы?", callback_data="faq_matrix_data")],
        [InlineKeyboardButton("❓ Сколько ждать ответ?", callback_data="faq_wait_time")],
        [InlineKeyboardButton("❓ Это бесплатно?", callback_data="faq_free_service")],
        [InlineKeyboardButton("⬅️ Закрыть помощь", callback_data="faq_close")],
    ]
    
    # Если команда вызвана через CallbackQuery (например, из главного меню), редактируем сообщение
    if update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.edit_message_text(
                clean