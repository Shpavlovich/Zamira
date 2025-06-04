import os
import logging
import re
from typing import Dict, Optional, Set, Any, List, Tuple
import asyncio
import json
from openai import AsyncOpenAI
import random
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
    "ADMIN_IDS": [7611426172],  # Замени на свой Telegram ID
    "DELAY_SECONDS_MAIN_SERVICE": 9420,  # 2 часа 37 минут (7200 + 2220 секунд)
    "DELAY_SECONDS_REVIEW_REQUEST": 43200,  # 12 часов
    "MAX_MESSAGE_LENGTH": 3900,
    "OPENAI_MAX_TOKENS_TAROT": 4000,
    "OPENAI_MAX_TOKENS_MATRIX": 6000,
    "OPENAI_MAX_CONCURRENT": 3,
    "RETRY_DELAY": 7,
    "MAX_RETRIES": 2,
    "COMPLETED_USERS_FILE": "completed_users.json",
    "MIN_TEXT_LENGTH_TAROT_BACKSTORY": 100,  # Минимальная длина предыстории: 100 символов
    "MIN_TEXT_LENGTH_TAROT_QUESTION": 100,  # Минимальная длина вопросов: 100 символов
}

# --- Настройка API ---
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not BOT_TOKEN or not OPENAI_API_KEY:
    logger.critical("Отсутствуют переменные окружения: TELEGRAM_TOKEN или OPENAI_API_KEY")
    raise ValueError("Установите TELEGRAM_TOKEN и OPENAI_API_KEY в настройках окружения")

openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
logger.info("Переменные окружения успешно загружены")

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
            logger.info(f"Сохранено {len(users_set)} пользователей в {CONFIG['COMPLETED_USERS_FILE']}")
    except Exception as e:
        logger.error(f"Ошибка сохранения {CONFIG['COMPLETED_USERS_FILE']}: {e}")

completed_users = load_completed_users()

# --- Текстовые константы ---
WELCOME_TEXT = """Здравствуйте. Меня зовут Замира.
Я практикующий таролог и специалист по Матрице Судьбы с опытом более 15 лет. Рада, если смогу помочь вам прояснить вашу ситуацию или лучше понять себя.

В этом боте вы можете получить одну бесплатную ознакомительную консультацию. Это хорошая возможность вам познакомиться со мной и моим подходом к работе.

На ваш выбор:
🃏 Расклад на картах Таро – посмотрим вашу ситуацию, поищем ответы на волнующие вопросы.
🌟 Разбор Матрицы Судьбы – поможет раскрыть ваши личные энергии, таланты и задачи.

В качестве энергообмена за мою работу и время, если консультация окажется для вас действительно полезной, я буду благодарна за ваш честный отзыв на Авито.

Как всё происходит:
1.  Вы уже здесь, если видите это сообщение после /start. Отлично!
2.  Теперь, пожалуйста, выберите ниже, какая услуга вас интересует: Таро или Матрица.
3.  После выбора я задам несколько уточняющих вопросов – это нужно для качественной подготовки.
4.  Сам ответ я готовлю обычно в течение 2-3 часов, так как с каждым запросом работаю индивидуально и внимательно.

Готовы? Тогда выбирайте 👇"""

TAROT_INTRO_TEXT = """Отлично, вы выбрали расклад на Таро. 🃏
Для того чтобы я смогла сделать для вас как можно более точный и глубокий анализ ситуации, мне понадобится некоторая информация. Буду задавать вопросы постепенно."""

MATRIX_INTRO_TEXT = """Хороший выбор. Разбор Матрицы Судьбы – это действительно глубокое исследование вашего личного потенциала. 🌟
Для расчета мне потребуются ваши полные имя и дата рождения. Я задам эти вопросы вам сейчас."""

ASK_MATRIX_NAME_TEXT = """(Шаг 1 из 2) Итак, приступаем к расчету вашей Матрицы.
Напишите, пожалуйста, ваше полное имя. Если делаете разбор для другого человека – тогда его имя."""

ASK_MATRIX_DOB_TEXT = """(Шаг 2 из 2) Благодарю. Теперь нужна дата рождения.
Пожалуйста, укажите ее в формате ДД.ММ.ГГГГ (например: 25.07.1988)."""

CONFIRM_DETAILS_MATRIX_TEXT = """Благодарю. Теперь важный момент: давайте внимательно проверим данные для Матрицы Судьбы.

Имя: {name}
Дата рождения: {dob}

Всё верно указано? Если да, пожалуйста, нажмите «Подтвердить»."""

ASK_TAROT_MAIN_PERSON_NAME_TEXT = """(Шаг 1 из 5) Итак, приступаем к подготовке расклада Таро.
Для начала напишите, пожалуйста, Ваше имя."""

ASK_TAROT_MAIN_PERSON_DOB_TEXT = """(Шаг 2 из 5) Записала, {name}. Теперь, пожалуйста, укажите Вашу дату рождения.
Формат: ДД.ММ.ГГГГ (например: 12.08.1985)."""

ASK_TAROT_BACKSTORY_TEXT = f"""\
(Шаг 3 из 5) Очень хорошо. Теперь очень важный момент – ваша ситуация или предыстория вопроса.
Расскажите, пожалуйста, что произошло, что вас беспокоит или особенно интересует сейчас? Чем подробнее вы опишете фон событий (хотя бы {CONFIG['MIN_TEXT_LENGTH_TAROT_BACKSTORY']} знаков), тем глубже я смогу проанализировать ситуацию для вас.
К примеру: «Мы с партнером в последние месяцы стали часто конфликтовать, не понимаю причину и как это исправить» или «Стою перед выбором новой работы, есть два варианта, не могу определиться».
"""

ASK_TAROT_OTHER_PEOPLE_TEXT = """\
(Шаг 4 из 5) Приняла вашу историю. Теперь уточним насчет других участников.
Скажите, пожалуйста, есть ли еще значимые люди, напрямую вовлеченные в ваш вопрос?
Если да, то напишите их имена и, по возможности, возраст или дату рождения. Эта информация поможет сделать расклад более полным и точным.
Если таких людей нет, достаточно написать «нет» или, например, «вопрос только обо мне».
Примеры: «Да, это мой муж Андрей, 40 лет» или «Нет, других нет».
"""

ASK_TAROT_QUESTIONS_TEXT = f"""\
(Шаг 5 из 5) Мы почти у цели. Остался заключительный шаг – ваши вопросы к картам.
Пожалуйста, сформулируйте основной вопрос (или два-три четких вопроса), на которые вы хотите получить ответ от Таро.
Постарайтесь, чтобы вопросы были открытыми, то есть не предполагали простого ответа «да» или «нет», и отражали суть вашей ситуации. Для основного вопроса желательно не менее {CONFIG['MIN_TEXT_LENGTH_TAROT_QUESTION']} знаков.
Например: «Каковы перспективы развития моих отношений с Михаилом в ближайшие полгода?» или «Что мне важно понять о текущей ситуации на работе, чтобы принять правильное решение?».
"""

CONFIRM_DETAILS_TAROT_TEXT_DISPLAY = """\
Благодарю вас за все уточнения. Это очень поможет для точного расклада.
Теперь, пожалуйста, еще раз внимательно всё проверьте:

Основное имя (ваше): {main_person_name}
Дата рождения (ваша): {main_person_dob}

Описание ситуации:
«{backstory}»

Другие упомянутые лица (если есть): {other_people}

Ваши вопросы к картам Таро:
«{questions}»
"""

EDIT_CHOICE_TEXT = """Пожалуйста, еще раз сверьтесь с данными выше. Если обнаружится ошибка, выберите пункт для ее исправления.
Если всё указано правильно, нажимайте «Всё верно, подтверждаю»."""

RESPONSE_WAIT_VARIANTS = [
    "Благодарю вас! 🙏 Заявку приняла и приступаю к работе над вашим вопросом. Ответ подготовлю в течение 2-3 часов. Пожалуйста, ожидайте. ✨",
    "Спасибо, ваш запрос получен. 🌿 Начинаю его внимательно изучать. Ответ будет готов для вас ориентировочно через 2-3 часа.",
    "Всё принято! 🔮 Я получила ваш запрос и уже скоро приступлю к его разбору. Постараюсь подготовить ответ в ближайшие 2-3 часа. Немного терпения, пожалуйста."
]

OPENAI_ERROR_MESSAGE = """К сожалению, в данный момент есть небольшая техническая неполадка. 🛠️
Пожалуйста, попробуйте подтвердить ваш запрос через несколько минут.
Если это не поможет, свяжитесь со мной напрямую: @zamira_esoteric."""

SATISFACTION_PROMPT_TEXT = """Ваш {service_type_rus} готов, я его вам отправила. 🔮
Очень надеюсь, что информация из него была для вас полезной и дала пищу для размышлений.

Скажите, пожалуйста, в целом вы довольны полученным разбором/раскладом?"""

DETAILED_FEEDBACK_PROMPT_TEXT = """Благодарю за вашу оценку! Мне будет очень ценно, если вы уточните: это поможет мне лучше понимать, что именно вам понравилось или что, возможно, стоило бы улучшить в моей работе.
Пожалуйста, выберите один из предложенных вариантов:"""

REVIEW_PROMISE_TEXT = """Я очень рада, что вам понравилось! 😊
Для нашего с вами энергообмена это действительно имеет значение. Поэтому чуть позже (ориентировочно через 12 часов) я пришлю вам ссылку для отзыва на Авито.
Считается, что благодарность, проявленная таким образом, помогает полученным предсказаниям и советам гармонично встроиться в вашу жизнь и принести больше пользы. ✨"""

NO_PROBLEM_TEXT = "Понимаю вас. В любом случае, я благодарю вас за то, что обратились."

REVIEW_TEXT_DELAYED = """Доброго времени! 🌿
Это Замира. Пишу, чтобы узнать, всё ли у вас в порядке, и надеюсь, что {service_type_rus}, который я для вас делала, был полезен и принес ясность.

Если у вас найдется несколько минут и желание поделиться впечатлениями, я буду очень признательна за отзыв о моей работе на Авито. Такие отклики помогают не только мне, но и другим людям, которые ищут своего проводника в мир Таро или Матрицы.

✍️ Оставить отзыв можно здесь:
https://www.avito.ru/user/review?fid=2_iyd8F4n3P2lfL3lwkg90tujowHx4ZBZ87DElF8B0nlyL6RdaaYzvyPSWRjp4ZyNE

Еще раз благодарю вас за оказанное доверие и ваше время! 🙏"""

PRIVATE_MESSAGE = """Рада вас снова видеть! ✨
Вы уже обращались ко мне за бесплатной ознакомительной консультацией.
Если вы хотели бы получить новый расклад или разбор Матрицы, пожалуйста, напишите мне напрямую (@zamira_esoteric). Мы обсудим условия дальнейшей работы. 🌺"""

CONTACT_TEXT = """Если у вас остались вопросы или вы хотели бы заказать индивидуальную консультацию (платную), вы можете написать мне напрямую.
Мой контакт в Телеграм: @zamira_esoteric 🌟
Обращайтесь, буду рада помочь."""

CANCEL_TEXT = """Хорошо, я вас поняла. Ваш текущий запрос отменен.
Если захотите вернуться и начать снова, вы всегда можете это сделать через команду /start из главного меню."""

FAQ_ANSWERS = {
    "faq_tarot_question": "Чтобы карты Таро смогли дать вам самый точный и полезный ответ, важно правильно сформулировать вопрос. Старайтесь задавать так называемые открытые вопросы – те, которые не подразумевают простого ответа «да» или «нет».\nК примеру, вместо вопроса «Выйду ли я замуж в этом году?» лучше спросить: «Какие перспективы в моей личной жизни ожидаются в этом году и на что мне стоит обратить внимание?».\nКонкретика и честность с собой при постановке вопроса – это ключ к действительно глубокому и информативному раскладу. 🔮",
    "faq_matrix_data": "Чтобы я могла рассчитать для вас Матрицу Судьбы, мне потребуются всего две вещи: ваше полное имя (то, которое было дано при рождении) и ваша полная дата рождения (день, месяц, год).\nИменно эти данные служат основой для построения карты ваших личных энергий и понимания вашего потенциала. 🌟",
    "faq_wait_time": "Я подхожу к каждому запросу индивидуально, уделяя ему необходимое время и внимание.\nКак правило, ответ вы получите в течение 2-3 часов после того, как подтвердите все предоставленные данные.\nВ редких случаях возможны небольшие задержки, но о них я всегда стараюсь предупредить заранее. ⏳",
    "faq_free_service": "Да, это так. Я действительно предоставляю одну услугу на ваш выбор (это может быть расклад Таро или разбор Матрицы Судьбы) в качестве ознакомления, совершенно без оплаты.\nДля меня это хорошая возможность показать вам мой подход к работе и немного познакомиться с вашей ситуацией.\nВ качестве энергообмена, если моя работа принесет вам пользу и вы почувствуете отклик, я буду очень благодарна за честный отзыв на Авито. Это важно и для меня, и для других людей, которые ищут помощи. 🙏"
}

# --- Промпты OpenAI ---
PROMPT_TAROT_SYSTEM = """
Ты – Замира, 40-летний таролог с более чем 15-летним опытом. Твой голос – спокойный, уверенный, мудрый и теплый. Ты общаешься с клиентом на «Вы», как чуткий и опытный наставник, помогая разобраться в ситуации через карты Таро. Твоя задача – дать глубокий, персонализированный и практически применимый расклад. Пиши живым, естественным русским языком, избегай любых признаков ИИ, роботизированности, канцеляризмов или шаблонных эзотерических клише. Предложения строй плавно, речь должна литься, как в личной беседе.

КЛЮЧЕВЫЕ АСПЕКТЫ ТВОЕГО СТИЛЯ («ГОЛОС ЗАМИРЫ»):
1.  **Обращение и тон:** Строго на «Вы». Если имя клиента известно, можешь деликатно его использовать (например, «Анна, эта карта говорит о Вашем...»). Тон – доверительный, эмпатичный, но без фамильярности. Ты не просто «читаешь карты», а ведешь диалог с человеком через них.
2.  **Глубина и практичность:** Не ограничивайся стандартными значениями карт. Объясняй, как энергия конкретной карты преломляется в жизни человека, его чувствах, мыслях, действиях, отношениях. Подсказывай, на что обратить внимание, какие уроки можно извлечь, какие шаги могут быть полезны. Советы должны быть мягкими, направляющими, а не директивными.
3.  **Человечность:** Используй фразы, характерные для опытного практика: «Давайте посмотрим внимательнее…», «Здесь важно понимать…», «Как показывает практика…», «Я бы обратила Ваше внимание на…». Твоя речь должна быть наполнена смыслом, без «воды».
4.  **Эмодзи:** Крайне умеренно, только для смыслового акцента (🔮, ✨, 🙏, 🌱).

ВРЕМЕННЫЕ РАМКИ:
* Текущая дата: {current_date}.
* Прогнозы и советы по будущему: Начиная С {future_start_date}.

СТРУКТУРА ОТВЕТА (СТРОГО – ТОЛЬКО ЭТО, БЕЗ ВСЯКИХ ВСТУПЛЕНИЙ И ПРОЩАНИЙ):
А. **Название расклада:** Краткое, емкое, по сути запроса (придумай сама).
Б. **Сам расклад (3-5 карт):**
    * Для каждой карты (нумерация 1️⃣, 2️⃣... с твоим смысловым названием позиции):
        * **Название карты.**
        * **Основная суть карты:** (1-2 предложения, просто и понятно).
        * **Трактовка в контексте запроса/позиции:** Подробно, связно, глубоко. Как эта энергия влияет на ситуацию клиента? Что подсвечивает?
        * **Практический совет/на что обратить внимание:** Конкретно и по делу.
В. **Итог расклада:** Краткий синтез (2-3 абзаца). Основные выводы, ключевая рекомендация. Заверши одной теплой, поддерживающей фразой-напутствием по сути расклада.

ОБЪЕМ: Качество и глубина важнее знаков. Расклад должен быть полным, но без растягивания. Ориентир ~3000-3500 знаков.
ИСХОДНЫЕ ДАННЫЕ КЛИЕНТА: Будут в следующем сообщении. Анализ – ИСКЛЮЧИТЕЛЬНО по ним.
ЗАПРЕЩЕНО: Любые приветствия, представления, благодарности, реклама, предложения других услуг, прощания, упоминания себя как ИИ.
"""

PROMPT_MATRIX_SYSTEM = """
Ты – Замира, 40-летний нумеролог, специалист по Матрице Судьбы с 15-летним опытом. Твой голос – спокойный, мудрый, объясняющий сложные вещи просто и доступно. Ты помогаешь человеку глубже понять себя, свои таланты, задачи и потенциал. Твоя задача – дать подробный, персонализированный и вдохновляющий разбор Матрицы. Пиши живым, естественным русским языком, строго избегая любых признаков ИИ, роботизированности и сухих перечислений. Текст должен быть таким, будто ты лично консультируешь человека.

КЛЮЧЕВЫЕ АСПЕКТЫ ТВОЕГО СТИЛЯ («ГОЛОС ЗАМИРЫ»):
1.  **ОБРАЩЕНИЕ К КЛИЕНТУ (КРИТИЧЕСКИ ВАЖНО!):** ВСЕГДА обращайся напрямую к человеку, для которого делаешь разбор, используя «Вы» и его имя (если дано, например: «Дмитрий, в Вашей зоне финансов стоит энергия...»). НИКОГДА не пиши о нем в 3-м лице (НЕПРАВИЛЬНО: «У Дмитрия сильный характер»). ПРАВИЛЬНО: «Дмитрий, у Вас сильный характер» или «Ваш характер отличается силой». Ты говоришь С ЧЕЛОВЕКОМ.
2.  **Глубина и персонализация:** Не давай общих описаний арканов. Объясняй, как КОНКРЕТНАЯ энергия в КОНКРЕТНОМ месте Матрицы влияет ИМЕННО НА ЖИЗНЬ ЭТОГО ЧЕЛОВЕКА (его характер, таланты, вызовы, отношения, финансы и т.д.). Показывай проявления «в плюсе» (как ресурс) и «в минусе» (как задача для проработки).
3.  **Практичность и поддержка:** Давай понятные рекомендации, как вывести энергии в плюс, на что обратить внимание. Твой тон – поддерживающий, мотивирующий, но реалистичный. Используй фразы вроде: «Для Вас важно научиться…», «Обратите внимание, как в Вашей жизни проявляется…», «Чтобы эта энергия работала на Вас в плюсе…».
4.  **Доступность изложения:** Сложные концепции (карма, предназначение, родовые задачи) объясняй простыми словами, можно через понятные жизненные аналогии или метафоры (но без излишеств).
5.  **Эмодзи:** Очень умеренно (🌟, 🌱, 💡, ✨).

ВРЕМЕННЫЕ РАМКИ:
* Текущая дата: {current_date}.
* Прогнозы и советы по будущему: Начиная С {future_start_date}.

СТРУКТУРА ОТВЕТА (СТРОГО – ТОЛЬКО ЭТО, БЕЗ ВСЯКИХ ВСТУПЛЕНИЙ И ПРОЩАНИЙ):
А. **Название разбора:** «Разбор Матрицы Судьбы для [Имя клиента]» (или «Разбор Вашей Матрицы Судьбы», если имя не дано).
Б. **Сам Разбор по 9 блокам** (стандартные названия ниже; качество и глубина важнее формального объема на блок):
    * Для каждого блока (нумерация 1️⃣, 2️⃣...):
        * Краткая суть блока (1-2 предложения, обращаясь к клиенту: «Этот блок показывает Ваши...»).
        * Ключевые энергии (арканы) клиента в этом блоке.
        * **Подробное раскрытие (обращаясь к клиенту):** Как эти энергии проявляются в ЕГО жизни (в плюсе и минусе), какие задачи ставят, какие возможности дают. Практические советы по гармонизации.
    * Названия 9 блоков: 1️⃣ Ваш личный потенциал и таланты; 2️⃣ Ваше духовное предназначение и кармические задачи; 3️⃣ Ваши отношения; 4️⃣ Ваши родовые программы; 5️⃣ Ваша социальная реализация; 6️⃣ Ваши финансы; 7️⃣ Ваше здоровье; 8️⃣ Ваши ключевые точки выбора и возрастные этапы; 9️⃣ Ваша итоговая энергия Матрицы.
В. **Заключение по периодам ({future_start_date_year} – {future_end_date_year} гг.):**
    * Ключевые тенденции для КЛИЕНТА (обращаясь к нему) на этот период. Основные возможности и вызовы.
    * Заверши одной теплой, мотивирующей фразой-напутствием для клиента на этот период.

ОБЪЕМ: Качество и глубина важнее знаков. Разбор должен быть полным и содержательным, но без «воды». Ориентир ~5000-5500 знаков.
ИСХОДНЫЕ ДАННЫЕ КЛИЕНТА: Имя и дата рождения. Анализ – ИСКЛЮЧИТЕЛЬНО по ним.
ЗАПРЕЩЕНО: Любые приветствия, представления, благодарности, реклама, прощания, упоминания себя как ИИ.
"""

# --- Утилитарные функции ---
def get_random_variant(variants_list: List[str]) -> str:
    return random.choice(variants_list)

def clean_text(text: str) -> str:
    try:
        text = text.replace("**", "")
        # Разрешаем переводы строк и табы, но убираем другие непечатаемые символы, кроме пробела
        return "".join(c for c in text if c.isprintable() or c in "\n\r\t ")
    except Exception as e:
        logger.error(f"Ошибка очистки текста: {e}")
        return text # Возвращаем исходный текст в случае ошибки

def validate_date_format(date_text: str) -> bool:
    return bool(re.match(r"^\d{2}\.\d{2}\.\d{4}$", date_text))

def validate_date_semantic(date_text: str) -> bool:
    try:
        date = datetime.strptime(date_text, "%d.%m.%Y")
        # Ограничение по году рождения можно настроить гибче, если нужно
        if date.year < 1900 or date.year > datetime.now().year + 5: # +5 для предотвращения ошибок с датами из близкого будущего (если вдруг)
            return False
        return True
    except ValueError:
        return False

def is_valid_name(name: str) -> bool:
    name_stripped = name.strip()
    if len(name_stripped) < 2:
        return False
    # Проверка, что имя не является датой
    if validate_date_format(name_stripped):
        return False
    # Разрешаем буквы (включая Ёё), пробелы, дефисы, апострофы. Имя должно содержать хотя бы одну букву.
    if re.fullmatch(r"^[A-Za-zА-Яа-яЁё\s'-]+$", name_stripped) and any(char.isalpha() for char in name_stripped):
        return True
    return False

async def retry_operation(coro, max_retries=CONFIG["MAX_RETRIES"], delay=CONFIG["RETRY_DELAY"]):
    for attempt in range(max_retries):
        try:
            return await coro()
        except Exception as e:
            logger.warning(f"Попытка {attempt + 1} не удалась: {e}")
            if attempt == max_retries - 1:
                raise
            await asyncio.sleep(delay * (2 ** attempt)) # Экспоненциальная задержка
    return None # Should not be reached if max_retries > 0

semaphore = asyncio.Semaphore(CONFIG["OPENAI_MAX_CONCURRENT"])

async def ask_gpt(system_prompt_template: str, user_prompt_content: str, max_tokens: int, context: ContextTypes.DEFAULT_TYPE, user_id_for_error: int) -> Optional[str]:
    async with semaphore:
        async def gpt_call():
            now = datetime.now()
            months_genitive = ["января", "февраля", "марта", "апреля", "мая", "июня",
                               "июля", "августа", "сентября", "октября", "ноября", "декабря"]
            current_date_str = f"конец {months_genitive[now.month-1]} {now.year} года"

            # Логика для future_start_date: следующий месяц, если текущая дата до 10-го числа, иначе через месяц
            if now.day <= 10:
                future_start_dt_obj = (now.replace(day=1) + timedelta(days=32)).replace(day=1) # Первый день следующего месяца
            else:
                future_start_dt_obj = (now.replace(day=1) + timedelta(days=63)).replace(day=1) # Первый день через один месяц

            future_start_date_str = f"начала {months_genitive[future_start_dt_obj.month-1]} {future_start_dt_obj.year} года"
            future_start_date_year_str = str(future_start_dt_obj.year)
            future_end_date_year_str = str(future_start_dt_obj.year + 3) # Прогноз на 3 года вперед

            system_prompt = system_prompt_template.format(
                current_date=current_date_str,
                future_start_date=future_start_date_str,
                future_start_date_year=future_start_date_year_str,
                future_end_date_year=future_end_date_year_str
            )

            logger.info(f"OpenAI запрос для {user_id_for_error}: system_prompt (начало): {system_prompt[:200]}...") # Увеличена длина лога для system_prompt
            logger.info(f"OpenAI запрос для {user_id_for_error}: user_prompt (начало): {user_prompt_content[:200]}...") # Увеличена длина лога для user_prompt

            response = await openai_client.chat.completions.create(
                model="gpt-4o", # Рекомендуется использовать актуальную модель
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt_content}
                ],
                temperature=0.75, # Можно немного варьировать для креативности
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content.strip()

        try:
            await context.bot.send_chat_action(chat_id=user_id_for_error, action=ChatAction.TYPING)
            return await retry_operation(gpt_call)
        except Exception as e:
            error_msg = f"Критическая ошибка OpenAI для пользователя {user_id_for_error}: {e}"
            logger.error(error_msg, exc_info=True)
            # Уведомление администратора теперь в retry_operation, но здесь можно добавить специфичное для ask_gpt
            await send_admin_notification(context, error_msg, critical=True) # Убедимся, что админ уведомлен
            return None

async def send_long_message(chat_id: int, message: str, bot_instance):
    parts = [message[i:i + CONFIG["MAX_MESSAGE_LENGTH"]] for i in range(0, len(message), CONFIG["MAX_MESSAGE_LENGTH"])]
    for part_idx, part in enumerate(parts):
        if part.strip():
            try:
                await bot_instance.send_message(chat_id=chat_id, text=part)
                if part_idx < len(parts) - 1: # Задержка между частями, кроме последней
                    await asyncio.sleep(1.5) 
            except Exception as e:
                logger.error(f"Ошибка отправки части сообщения пользователю {chat_id}:{e}")
                # Можно добавить логику повторной отправки или уведомление администратору

async def send_admin_notification(context: ContextTypes.DEFAULT_TYPE, message: str, critical: bool = False):
    full_message = f"🔔 Уведомление Бота Замиры ({'КРИТИЧЕСКАЯ ОШИБКА 🆘' if critical else 'Инфо'}) 🔔\n{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n{message}"
    for admin_id in CONFIG["ADMIN_IDS"]:
        try:
            await context.bot.send_message(chat_id=admin_id, text=full_message)
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление администратору {admin_id}: {e}")

# --- Callbacks для JobQueue ---
async def main_service_job(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    user_id: int = job_data["user_id"]
    result: str = job_data["result"]
    service_type: str = job_data["service_type"]
    user_name_for_log = job_data.get("user_name_for_log", str(user_id))

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
        # Используем clean_text для SATISFACTION_PROMPT_TEXT на всякий случай
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
    user_id: int = job_data["user_id"]
    service_type: str = job_data["service_type"]
    service_type_rus_map = {"tarot": "расклад Таро", "matrix": "разбор Матрицы Судьбы"}
    service_type_rus = service_type_rus_map.get(service_type, "услугу")
    logger.info(f"Отправка отложенного запроса на отзыв пользователю {user_id} для {service_type_rus}")
    try:
        # Используем clean_text для REVIEW_TEXT_DELAYED
        await context.bot.send_message(user_id, clean_text(REVIEW_TEXT_DELAYED.format(service_type_rus=service_type_rus)))
    except Exception as e:
        logger.error(f"Ошибка при отправке запроса на отзыв пользователю {user_id}: {e}", exc_info=True)

# --- ConversationHandler состояния ---
(CHOOSE_SERVICE,
 ASK_MATRIX_NAME, ASK_MATRIX_DOB, CONFIRM_MATRIX_DATA,
 ASK_TAROT_MAIN_PERSON_NAME, ASK_TAROT_MAIN_PERSON_DOB,
 ASK_TAROT_BACKSTORY, ASK_TAROT_OTHER_PEOPLE, ASK_TAROT_QUESTIONS,
 SHOW_TAROT_CONFIRM_OPTIONS) = range(10)

CANCEL_CALLBACK_DATA = "cancel_conv_inline"
EDIT_PREFIX_TAROT = "edit_field_tarot_"

# --- Клавиатуры ---
def get_cancel_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отменить", callback_data=CANCEL_CALLBACK_DATA)]])

def get_tarot_edit_keyboard():
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

    if context.user_data: # Очистка user_data при каждом новом /start
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
    if user_data is None: # На всякий случай, хотя после /start должно быть user_data
        user_data = context.user_data = {}

    service_type_or_action = query.data

    if service_type_or_action == "contact_direct":
        await query.edit_message_text(clean_text(CONTACT_TEXT), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад в меню", callback_data="back_to_start")]]))
        return CHOOSE_SERVICE # Остаемся в том же состоянии
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
        # Удаляем предыдущее сообщение с кнопками выбора услуг, если возможно
        try:
            await query.delete_message()
        except Exception as e:
            logger.warning(f"Не удалось удалить сообщение в choose_service_callback при переходе в help: {e}")
        # Вызываем help_command, который отправит новое сообщение
        await help_command(update, context) # help_command не возвращает состояние для ConversationHandler
        return ConversationHandler.END # Завершаем текущий диалог, если help - это отдельная команда
    else: # tarot или matrix
        user_data["service_type"] = service_type_or_action
        user_data["current_step"] = 1 # Начальный шаг для выбранной услуги

        if service_type_or_action == "tarot":
            user_data["total_steps"] = 5
            await query.edit_message_text(text=clean_text(TAROT_INTRO_TEXT), reply_markup=None) # Убираем кнопки предыдущего сообщения
            prompt_text = clean_text(ASK_TAROT_MAIN_PERSON_NAME_TEXT)
            await query.message.reply_text(prompt_text, reply_markup=get_cancel_keyboard())
            return ASK_TAROT_MAIN_PERSON_NAME
        elif service_type_or_action == "matrix":
            user_data["total_steps"] = 2
            await query.edit_message_text(text=clean_text(MATRIX_INTRO_TEXT), reply_markup=None) # Убираем кнопки
            prompt_text = clean_text(ASK_MATRIX_NAME_TEXT)
            await query.message.reply_text(prompt_text, reply_markup=get_cancel_keyboard())
            return ASK_MATRIX_NAME
        else: # Неожиданное значение
            logger.warning(f"Неизвестный service_type_or_action в choose_service_callback: {service_type_or_action}")
            # Возвращаем пользователя в начальное состояние на всякий случай
            keyboard_main_fallback = [
                [InlineKeyboardButton("🃏 Расклад Таро", callback_data="tarot")],
                [InlineKeyboardButton("🌟 Матрица Судьбы", callback_data="matrix")],
                [InlineKeyboardButton("📩 Связь со мной", callback_data="contact_direct")],
                [InlineKeyboardButton("💡 Помощь / FAQ", callback_data="help_section")]
            ]
            await query.edit_message_text(clean_text(WELCOME_TEXT), reply_markup=InlineKeyboardMarkup(keyboard_main_fallback))
            return CHOOSE_SERVICE


async def ask_matrix_name_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_data = context.user_data
    name_input = update.message.text
    if not name_input or not is_valid_name(name_input):
        error_msg = f"Хм, «{name_input or ''}» не очень похоже на имя. Имя должно содержать только буквы, пробелы, дефисы или апострофы, и быть не короче двух символов. Попробуйте еще раз, пожалуйста."
        await update.message.reply_text(clean_text(error_msg), reply_markup=get_cancel_keyboard())
        return ASK_MATRIX_NAME # Остаемся в том же состоянии

    user_data["matrix_name"] = clean_text(name_input.strip())
    user_data["current_step"] = 2

    # Варианты ответа для следующего шага
    # ASK_MATRIX_DOB_TEXT уже содержит (Шаг 2 из 2)
    reply_variants = [
        ASK_MATRIX_DOB_TEXT, # Используем утвержденный текст напрямую
        f"Отлично, {user_data['matrix_name']}! (Шаг 2 из 2) Теперь нужна ваша дата рождения (ДД.ММ.ГГГГ).",
        f"Записала, {user_data['matrix_name']}. (Шаг 2 из 2) Далее, пожалуйста, дату вашего рождения в формате ДД.ММ.ГГГГ."
    ]
    await update.message.reply_text(clean_text(get_random_variant(reply_variants)), reply_markup=get_cancel_keyboard())
    return ASK_MATRIX_DOB

async def ask_matrix_dob_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_data = context.user_data
    dob_text_input = update.message.text
    if not dob_text_input: # Проверка на пустое сообщение
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
                [InlineKeyboardButton("❌ Отменить", callback_data=CANCEL_CALLBACK_DATA)]] # Можно добавить кнопку "Исправить"
    await update.message.reply_text(clean_text(confirm_text), reply_markup=InlineKeyboardMarkup(keyboard))
    return CONFIRM_MATRIX_DATA


async def ask_tarot_main_person_name_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_data = context.user_data
    name_input = update.message.text
    if not name_input or not is_valid_name(name_input):
        error_msg = f"Хм, «{name_input or ''}» не очень похоже на имя. Имя должно содержать только буквы, пробелы, дефисы или апострофы. Попробуйте еще раз."
        await update.message.reply_text(clean_text(error_msg), reply_markup=get_cancel_keyboard())
        return ASK_TAROT_MAIN_PERSON_NAME

    user_data["tarot_main_person_name"] = clean_text(name_input.strip())

    # Если мы находимся в режиме редактирования этого поля
    if user_data.pop("editing_this_specific_field", None) == f"{EDIT_PREFIX_TAROT}main_person_name":
        return await show_tarot_confirm_options_message(update, context) # Возвращаемся к подтверждению

    user_data["current_step"] = 2
    prompt_text = clean_text(ASK_TAROT_MAIN_PERSON_DOB_TEXT.format(name=user_data["tarot_main_person_name"]))
    await update.message.reply_text(prompt_text, reply_markup=get_cancel_keyboard())
    return ASK_TAROT_MAIN_PERSON_DOB

async def ask_tarot_main_person_dob_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_data = context.user_data
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

    if user_data.pop("editing_this_specific_field", None) == f"{EDIT_PREFIX_TAROT}main_person_dob":
        return await show_tarot_confirm_options_message(update, context)

    user_data["current_step"] = 3
    await update.message.reply_text(clean_text(ASK_TAROT_BACKSTORY_TEXT), reply_markup=get_cancel_keyboard())
    return ASK_TAROT_BACKSTORY

async def ask_tarot_backstory_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_data = context.user_data
    backstory_input = update.message.text
    min_len = CONFIG.get("MIN_TEXT_LENGTH_TAROT_BACKSTORY", 100) 
    if not backstory_input or len(backstory_input.strip()) < min_len:
        await update.message.reply_text(f"Пожалуйста, опишите ситуацию подробнее (не менее {min_len} символов). Это важно для точности расклада.", reply_markup=get_cancel_keyboard())
        return ASK_TAROT_BACKSTORY
    
    user_data["tarot_backstory"] = clean_text(backstory_input.strip())

    if user_data.pop("editing_this_specific_field", None) == f"{EDIT_PREFIX_TAROT}backstory":
        return await show_tarot_confirm_options_message(update, context)

    user_data["current_step"] = 4
    await update.message.reply_text(clean_text(ASK_TAROT_OTHER_PEOPLE_TEXT), reply_markup=get_cancel_keyboard())
    return ASK_TAROT_OTHER_PEOPLE

async def ask_tarot_other_people_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_data = context.user_data
    other_people_input = update.message.text
    if not other_people_input or len(other_people_input.strip()) < 2: # "да", "нет" - минимум 2 символа
        await update.message.reply_text("Пожалуйста, укажите других участников или напишите 'нет', если их нет.", reply_markup=get_cancel_keyboard())
        return ASK_TAROT_OTHER_PEOPLE
    
    user_data["tarot_other_people"] = clean_text(other_people_input.strip())

    if user_data.pop("editing_this_specific_field", None) == f"{EDIT_PREFIX_TAROT}other_people":
        return await show_tarot_confirm_options_message(update, context)
    
    user_data["current_step"] = 5
    await update.message.reply_text(clean_text(ASK_TAROT_QUESTIONS_TEXT), reply_markup=get_cancel_keyboard())
    return ASK_TAROT_QUESTIONS

async def ask_tarot_questions_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_data = context.user_data
    questions_input = update.message.text
    min_len = CONFIG.get("MIN_TEXT_LENGTH_TAROT_QUESTION", 100)
    if not questions_input or len(questions_input.strip()) < min_len:
        await update.message.reply_text(f"Пожалуйста, сформулируйте ваш вопрос(ы) к картам (не менее {min_len} символов). Если вопросов несколько, напишите их все в одном сообщении.", reply_markup=get_cancel_keyboard())
        return ASK_TAROT_QUESTIONS
    
    user_data["tarot_questions"] = clean_text(questions_input.strip())
    user_data.pop("editing_this_specific_field", None) # Очищаем флаг редактирования, если он был
    return await show_tarot_confirm_options_message(update, context)


async def show_tarot_confirm_options_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_data = context.user_data
    
    # update.message может быть None, если мы пришли сюда из callback_query после редактирования
    effective_message_source = update.message or (update.callback_query.message if update.callback_query else None)
    if not effective_message_source:
        logger.error("show_tarot_confirm_options_message: не найден источник сообщения (message или callback_query.message)")
        # Попытка отправить новое сообщение, если известен chat_id
        if update.effective_chat:
             await context.bot.send_message(update.effective_chat.id, "Произошла ошибка отображения данных для подтверждения. Пожалуйста, попробуйте начать заново с /start.")
        return ConversationHandler.END

    if not user_data or user_data.get("service_type") != "tarot":
        await effective_message_source.reply_text(clean_text("Произошла ошибка при сборе данных для Таро. Давайте начнем сначала."), reply_markup=get_cancel_keyboard())
        if user_data: user_data.clear()
        return CHOOSE_SERVICE # Или ConversationHandler.END, если отмена должна полностью прерывать

    confirm_text_display = CONFIRM_DETAILS_TAROT_TEXT_DISPLAY.format(
        main_person_name=user_data.get("tarot_main_person_name", "-"),
        main_person_dob=user_data.get("tarot_main_person_dob", "-"),
        backstory=user_data.get("tarot_backstory", "-"),
        other_people=user_data.get("tarot_other_people", "-"),
        questions=user_data.get("tarot_questions", "-")
    )

    keyboard = get_tarot_edit_keyboard()
    
    # Отправляем два сообщения: одно с данными, другое с кнопками
    await effective_message_source.reply_text(clean_text(confirm_text_display))
    new_message_with_buttons = await effective_message_source.reply_text(clean_text(EDIT_CHOICE_TEXT), reply_markup=keyboard)

    if user_data and new_message_with_buttons : # Сохраняем ID сообщения с кнопками для возможного удаления/редактирования
         user_data["tarot_confirm_options_message_id"] = new_message_with_buttons.message_id
    
    return SHOW_TAROT_CONFIRM_OPTIONS

async def edit_field_tarot_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_data = context.user_data
    if not user_data: return ConversationHandler.END # На всякий случай

    # Удаляем сообщение с кнопками редактирования
    if query.message and user_data.get("tarot_confirm_options_message_id") == query.message.message_id:
        try:
            await query.delete_message()
            user_data.pop("tarot_confirm_options_message_id", None)
        except Exception as e:
            logger.warning(f"Не удалось удалить сообщение ({query.message.message_id}) с выбором редактирования: {e}")
    
    field_to_edit_key_from_callback = query.data 
    
    user_data["editing_this_specific_field"] = field_to_edit_key_from_callback # Флаг, что мы редактируем поле

    # Определяем, какое поле пользователь хочет отредактировать и какое сообщение отправить
    # Также очищаем значение этого поля в user_data, чтобы пользователь ввел его заново
    field_name_in_user_data = field_to_edit_key_from_callback.replace(EDIT_PREFIX_TAROT, "tarot_")
    user_data.pop(field_name_in_user_data, None)


    next_state_map = {
        f"{EDIT_PREFIX_TAROT}main_person_name": (ASK_TAROT_MAIN_PERSON_NAME, ASK_TAROT_MAIN_PERSON_NAME_TEXT),
        # Для DOB_TEXT нужно передать имя, если оно уже есть
        f"{EDIT_PREFIX_TAROT}main_person_dob": (ASK_TAROT_MAIN_PERSON_DOB, ASK_TAROT_MAIN_PERSON_DOB_TEXT.format(name=user_data.get("tarot_main_person_name", "для него/нее"))),
        f"{EDIT_PREFIX_TAROT}backstory": (ASK_TAROT_BACKSTORY, ASK_TAROT_BACKSTORY_TEXT),
        f"{EDIT_PREFIX_TAROT}other_people": (ASK_TAROT_OTHER_PEOPLE, ASK_TAROT_OTHER_PEOPLE_TEXT),
        f"{EDIT_PREFIX_TAROT}questions": (ASK_TAROT_QUESTIONS, ASK_TAROT_QUESTIONS_TEXT),
    }

    if field_to_edit_key_from_callback in next_state_map:
        next_state, prompt_text_template = next_state_map[field_to_edit_key_from_callback]
        
        # Для ASK_TAROT_BACKSTORY_TEXT и ASK_TAROT_QUESTIONS_TEXT, если они f-string
        # их нужно отформатировать здесь, если они зависят от CONFIG
        # Но в данном случае они уже отформатированы при определении константы
        prompt_text_to_send = clean_text(prompt_text_template)

        chat_id_to_reply = query.message.chat_id if query.message else query.from_user.id
        await context.bot.send_message(chat_id=chat_id_to_reply, text=prompt_text_to_send, reply_markup=get_cancel_keyboard())
        return next_state

    logger.warning(f"Неизвестное поле для редактирования Таро: {field_to_edit_key_from_callback}")
    # Если что-то пошло не так, возвращаем к подтверждению (он отправит новое сообщение)
    return await show_tarot_confirm_options_message(update, context)


async def process_final_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE, service_type: str) -> int:
    query = update.callback_query
    await query.answer()
    user_data = context.user_data
    user_id = query.from_user.id
    user_name_for_log = query.from_user.full_name or str(user_id) # Для логов
    user_data["user_name_for_log"] = user_name_for_log

    # Удаляем или редактируем сообщение с кнопками подтверждения
    message_id_to_remove_or_edit = None
    if service_type == "tarot":
        message_id_to_remove_or_edit = user_data.pop("tarot_confirm_options_message_id", None)
    elif query.message: # Для Матрицы, если есть сообщение с кнопкой "Подтвердить"
        message_id_to_remove_or_edit = query.message.message_id

    response_wait_text = get_random_variant(RESPONSE_WAIT_VARIANTS)
    sent_confirmation_msg = None

    if message_id_to_remove_or_edit and query.message and query.message.chat:
        try:
            # Редактируем сообщение, убирая кнопки и показывая текст ожидания
            sent_confirmation_msg = await context.bot.edit_message_text(
                chat_id=query.message.chat.id, message_id=message_id_to_remove_or_edit,
                text=clean_text(response_wait_text), reply_markup=None)
        except TelegramError as e:
            # Если сообщение не изменено или не найдено, отправляем новое
            if "Message is not modified" not in str(e) and "message to edit not found" not in str(e).lower():
                logger.error(f"Ошибка edit_message_text в process_final_confirmation: {e}. Отправляю новое.")
            # В любом случае, если редактирование не удалось, отправляем новое сообщение
            sent_confirmation_msg = await query.message.reply_text(text=clean_text(response_wait_text))
    else: # Если не было ID сообщения для редактирования, просто отправляем новое
        sent_confirmation_msg = await query.message.reply_text(text=clean_text(response_wait_text))
    
    # Добавляем реакцию на сообщение об ожидании
    if sent_confirmation_msg:
        try:
            await context.bot.set_message_reaction(
                chat_id=sent_confirmation_msg.chat_id, message_id=sent_confirmation_msg.message_id,
                reaction=[ReactionTypeEmoji("⚡")]) # Пример реакции
        except Exception as e_react:
            logger.warning(f"Не удалось поставить реакцию на сообщение {sent_confirmation_msg.message_id}: {e_react}")


    # Подготовка данных для OpenAI
    input_for_gpt = ""
    system_prompt_template = ""
    user_prompt_base_template = "" # Базовый шаблон для PROMPT_TAROT_USER и PROMPT_MATRIX_USER
    max_tokens_val = 0
    confirm_text_on_error_template = "" # Шаблон для текста подтверждения при ошибке
    next_confirm_state_on_error = ConversationHandler.END


    if service_type == "tarot":
        input_for_gpt = (
            f"Основное имя (кверента): {user_data.get('tarot_main_person_name', 'Не указано')}\n"
            f"Дата рождения (кверента): {user_data.get('tarot_main_person_dob', 'Не указано')}\n"
            f"Описание ситуации: {user_data.get('tarot_backstory', 'Не указано')}\n"
            f"Другие участники: {user_data.get('tarot_other_people', 'Не указано')}\n"
            f"Вопросы к картам: {user_data.get('tarot_questions', 'Не указано')}")
        system_prompt_template = PROMPT_TAROT_SYSTEM
        user_prompt_base_template = "Данные клиента и его запрос: {input_text}"
        max_tokens_val = CONFIG["OPENAI_MAX_TOKENS_TAROT"]
        # Шаблон для текста подтверждения при ошибке (будет отформатирован ниже)
        confirm_text_on_error_template = CONFIRM_DETAILS_TAROT_TEXT_DISPLAY 
        next_confirm_state_on_error = SHOW_TAROT_CONFIRM_OPTIONS
    elif service_type == "matrix":
        input_for_gpt = (
            f"Имя: {user_data.get('matrix_name', 'Не указано')}\n"
            f"Дата рождения: {user_data.get('matrix_dob', 'Не указано')}")
        system_prompt_template = PROMPT_MATRIX_SYSTEM
        user_prompt_base_template = "Данные клиента: {input_text}"
        max_tokens_val = CONFIG["OPENAI_MAX_TOKENS_MATRIX"]
        # Шаблон для текста подтверждения при ошибке (будет отформатирован ниже)
        confirm_text_on_error_template = CONFIRM_DETAILS_MATRIX_TEXT
        next_confirm_state_on_error = CONFIRM_MATRIX_DATA

    final_user_prompt = user_prompt_base_template.format(input_text=input_for_gpt)
    result = await ask_gpt(system_prompt_template, final_user_prompt, max_tokens_val, context, user_id)

    if result is None: # Ошибка OpenAI
        await query.message.reply_text(clean_text(OPENAI_ERROR_MESSAGE))
        
        # Формируем текст для повторного подтверждения
        if service_type == "tarot":
            current_confirm_text_on_error = confirm_text_on_error_template.format(
                main_person_name=user_data.get('tarot_main_person_name', '?'),
                main_person_dob=user_data.get('tarot_main_person_dob', '?'),
                backstory=user_data.get('tarot_backstory', '?'),
                other_people=user_data.get('tarot_other_people', '?'),
                questions=user_data.get('tarot_questions', '?')
            ) + "\n\n" + clean_text(EDIT_CHOICE_TEXT)
            keyboard_retry_buttons = get_tarot_edit_keyboard().inline_keyboard
        else: # matrix
            current_confirm_text_on_error = confirm_text_on_error_template.format(
                name=user_data.get('matrix_name', '?'), 
                dob=user_data.get('matrix_dob', '?')
            )
            keyboard_retry_buttons = [[InlineKeyboardButton("Попробовать подтвердить снова", callback_data=f"confirm_final_{service_type}")],
                                      [InlineKeyboardButton("❌ Отменить", callback_data=CANCEL_CALLBACK_DATA)]]
        try:
            await query.message.reply_text(text=clean_text(current_confirm_text_on_error), reply_markup=InlineKeyboardMarkup(keyboard_retry_buttons))
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
    context.job_queue.run_once(main_service_job, CONFIG["DELAY_SECONDS_MAIN_SERVICE"], data=job_payload, name=f"main_job_{user_id}")

    logger.info(f"Заявка пользователя {user_name_for_log} ({user_id}) ({service_type}) принята и запланирована.")
    await send_admin_notification(context, f"📨 Новая заявка от {user_name_for_log} (ID: {user_id}) на {service_type}. Запланирована.")
    if user_data: user_data.clear() # Очищаем данные пользователя после успешного планирования
    return ConversationHandler.END

async def confirm_matrix_data_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await process_final_confirmation(update, context, "matrix")

async def confirm_tarot_data_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await process_final_confirmation(update, context, "tarot")

async def common_cancel_logic(update: Update, context: ContextTypes.DEFAULT_TYPE, query: Optional[CallbackQuery] = None) -> int:
    user_data = context.user_data
    if user_data:
        user_data.clear()

    cancel_message_text = clean_text(CANCEL_TEXT)
    
    # Определяем, откуда пришел запрос на отмену
    effective_message_source = query.message if query else update.message
    chat_to_reply_id = None

    if effective_message_source:
        chat_to_reply_id = effective_message_source.chat_id
        try:
            if query: # Если это callback_query, редактируем сообщение
                await query.edit_message_text(text=cancel_message_text, reply_markup=None)
            else: # Если это команда /cancel, отвечаем на сообщение
                await effective_message_source.reply_text(text=cancel_message_text)
        except TelegramError as e:
            # Если не удалось отредактировать (например, сообщение слишком старое или не найдено)
            # или ответить, отправляем новое сообщение, если есть chat_id
            if chat_to_reply_id:
                 if "Message is not modified" not in str(e): # Логируем только значимые ошибки
                    logger.warning(f"Не удалось отредактировать/ответить на сообщение при отмене: {e}. Отправляю новое.")
                 await context.bot.send_message(chat_id=chat_to_reply_id, text=cancel_message_text)
            else:
                logger.error(f"Не удалось определить chat_id для отправки сообщения об отмене: {e}")

    elif query: # Если есть query, но нет query.message (маловероятно, но возможно)
        chat_to_reply_id = query.from_user.id
        await context.bot.send_message(chat_id=chat_to_reply_id, text=cancel_message_text)
    else: # Если нет ни update.message, ни query (очень маловероятно)
        logger.error("Не удалось определить источник для отмены диалога.")


    # Отправляем приветственное сообщение с главным меню, если удалось определить чат
    if chat_to_reply_id:
        keyboard_main = [
            [InlineKeyboardButton("🃏 Расклад Таро", callback_data="tarot")],
            [InlineKeyboardButton("🌟 Матрица Судьбы", callback_data="matrix")],
            [InlineKeyboardButton("📩 Связь со мной", callback_data="contact_direct")],
            [InlineKeyboardButton("💡 Помощь / FAQ", callback_data="help_section")]
        ]
        try:
            await context.bot.send_message(chat_id=chat_to_reply_id, text=clean_text(WELCOME_TEXT), reply_markup=InlineKeyboardMarkup(keyboard_main))
        except Exception as e:
            logger.error(f"Не удалось отправить WELCOME_TEXT после отмены в чат {chat_to_reply_id}: {e}")
            
    return ConversationHandler.END


async def cancel_conv_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id_log = update.effective_user.id if update.effective_user else "UnknownUser"
    logger.info(f"Пользователь {user_id_log} отменил диалог командой /cancel.")
    return await common_cancel_logic(update, context)

async def cancel_conv_inline_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer() # Важно ответить на callback_query
    logger.info(f"Пользователь {query.from_user.id} отменил диалог через инлайн кнопку.")
    return await common_cancel_logic(update, context, query=query)


async def handle_satisfaction_and_other_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data: return # Проверка на пустые данные
    await query.answer()
    user_id = query.from_user.id

    # Обработка ответа на вопрос об удовлетворенности
    if query.data.startswith("satisfaction_"):
        parts = query.data.split("_")
        answer = parts[1] # yes или no
        service_type = parts[2] if len(parts) > 2 else "услугу" # tarot или matrix

        original_message_text = query.message.text if query.message else clean_text(SATISFACTION_PROMPT_TEXT.format(service_type_rus="консультацию")) # Базовый текст, если исходное сообщение недоступно

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
                    reply_markup=detailed_feedback_keyboard)
            except TelegramError as e: # Если не удалось отредактировать
                logger.warning(f"Не удалось отредактировать сообщение для детального фидбека: {e}. Отправляю новое.")
                await query.message.reply_text(text=clean_text(DETAILED_FEEDBACK_PROMPT_TEXT), reply_markup=detailed_feedback_keyboard)
        
        elif answer == "no":
            # Редактируем исходное сообщение, добавляя текст NO_PROBLEM_TEXT
            try:
                await query.edit_message_text(text=f"{original_message_text}\n\n{clean_text(NO_PROBLEM_TEXT)}", reply_markup=None)
            except TelegramError as e:
                 logger.warning(f"Не удалось отредактировать сообщение 'no satisfaction': {e}. Отправляю новое.")
                 await query.message.reply_text(text=clean_text(NO_PROBLEM_TEXT))


    # Обработка детального фидбека
    elif query.data.startswith("detailed_fb_"):
        feedback_parts = query.data.split("_")
        feedback_type = feedback_parts[2] # accurate, useful_qs, general, skip
        service_type = feedback_parts[3] if len(feedback_parts) > 3 else "услугу"

        logger.info(f"Пользователь {user_id} дал детальный фидбек: {feedback_type} для {service_type}")

        thank_you_for_feedback_text = "Спасибо за ваш отклик! Это очень помогает мне становиться лучше."
        if feedback_type == "skip":
            thank_you_for_feedback_text = "Понимаю. Спасибо за использование сервиса!"
        
        # Попытка сохранить часть исходного сообщения, если оно редактировалось
        original_satisfaction_text_segment = ""
        if query.message and query.message.text:
            # Ищем разделитель, который был добавлен при переходе к детальному фидбеку
            split_segments = query.message.text.split(clean_text(DETAILED_FEEDBACK_PROMPT_TEXT))
            if split_segments:
                original_satisfaction_text_segment = split_segments[0].strip() # Первая часть до разделителя
        
        final_text_after_detailed_fb = f"{original_satisfaction_text_segment}\n\n{thank_you_for_feedback_text}".strip()
        
        try:
            await query.edit_message_text(text=final_text_after_detailed_fb, reply_markup=None)
        except TelegramError as e:
            logger.warning(f"Не удалось отредактировать сообщение после детального фидбека: {e}. Отправляю новое.")
            # Отправляем только благодарность, если редактирование не удалось
            await query.message.reply_text(thank_you_for_feedback_text)


        # Если фидбек не "skip", обещаем напомнить про отзыв и планируем задачу
        if feedback_type != "skip":
            await query.message.reply_text(clean_text(REVIEW_PROMISE_TEXT)) # Отправляем как новое сообщение
            if not context.job_queue:
                logger.error(f"JobQueue не найден при планировании запроса отзыва после детального фидбека для {user_id}")
                return
            job_payload = {"user_id": user_id, "service_type": service_type}
            context.job_queue.run_once(review_request_job, CONFIG["DELAY_SECONDS_REVIEW_REQUEST"], data=job_payload, name=f"review_req_job_{user_id}")
            logger.info(f"Запланирован запрос отзыва для {user_id} через {CONFIG['DELAY_SECONDS_REVIEW_REQUEST']} секунд после детального фидбека '{feedback_type}'.")


async def post_fallback_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Этот обработчик вызывается для сообщений, которые не попали в ConversationHandler
    # или другие специфичные обработчики команд.
    if update.message and update.effective_user:
        user_id = update.effective_user.id
        if user_id in completed_users: # Если пользователь уже получил услугу
            await update.message.reply_text(clean_text(PRIVATE_MESSAGE))
            return

        # Проверяем, находится ли пользователь в активном диалоге (ConversationHandler)
        # ConversationHandler.STATE хранится в context.user_data или context.chat_data в зависимости от per_user/per_chat
        # Мы используем per_message=False, что по умолчанию per_user.
        # Однако, точный ключ состояния зависит от реализации и может быть не ConversationHandler.STATE.
        # Лучше проверять наличие user_data и какого-то ожидаемого ключа, если он есть.
        # В данном случае, если user_data не пустое, вероятно, диалог активен.
        # Более надежно - проверять конкретное состояние, если оно известно.
        # Здесь мы предполагаем, что если user_data есть и не пусто, то диалог мог быть активен.
        # Но если сообщение не обработано ConversationHandler, то он уже не в нем.
        # Поэтому, если это просто текстовое сообщение вне диалога:
        
        # Если нет активного диалога (т.е. user_data пусто или не содержит ключа состояния)
        # и пользователь не завершил услугу.
        current_conversation_state = context.user_data.get(ConversationHandler.STATE) if context.user_data else None
        
        if not current_conversation_state: # Если нет активного состояния диалога
            await update.message.reply_text(
                "Кажется, мы не находимся в процессе оформления запроса. Нажмите /start, чтобы начать или выбрать услугу 🔮."
            )
        # Если есть состояние, но сообщение все равно не обработано,
        # это может быть неожиданный ввод в середине диалога,
        # который не обрабатывается текущим MessageHandler-ом этого состояния.
        # В таком случае, можно либо игнорировать, либо отправить подсказку.
        # Для простоты, если есть состояние, но этот fallback сработал - ничего не делаем,
        # т.к. пользователь должен следовать инструкциям диалога.
        # Вышестоящая логика уже покрывает это.

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or user.id not in CONFIG["ADMIN_IDS"]:
        await update.message.reply_text("Эта команда доступна только администратору.")
        return

    completed_count = len(completed_users)
    active_jobs = context.job_queue.jobs() if context.job_queue else []
    pending_main_jobs = sum(1 for job in active_jobs if job.name and job.name.startswith("main_job_"))
    pending_review_jobs = sum(1 for job in active_jobs if job.name and job.name.startswith("review_req_job_"))

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
        save_completed_users(completed_users) # Сохраняем изменения
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

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("❓ Как задать вопрос для Таро?", callback_data="faq_tarot_question")],
        [InlineKeyboardButton("❓ Что нужно для Матрицы Судьбы?", callback_data="faq_matrix_data")],
        [InlineKeyboardButton("❓ Сколько ждать ответ?", callback_data="faq_wait_time")],
        [InlineKeyboardButton("❓ Это бесплатно?", callback_data="faq_free_service")],
        [InlineKeyboardButton("⬅️ Закрыть помощь", callback_data="faq_close")], # Кнопка для закрытия FAQ
    ]

    help_text = "Чем могу помочь? Выберите вопрос из списка ниже:"
    
    # Если команда пришла из callback_query (например, из главного меню)
    if update.callback_query:
        await update.callback_query.answer()
        try:
            # Пытаемся отредактировать сообщение, из которого пришел callback
            await update.callback_query.edit_message_text(clean_text(help_text), reply_markup=InlineKeyboardMarkup(keyboard))
        except TelegramError as e:
            if "Message is not modified" not in str(e): # Логируем только если ошибка не "сообщение не изменено"
                logger.warning(f"Не удалось отредактировать сообщение для /help из callback: {e}")
            # Если не удалось отредактировать (например, оно было удалено или слишком старое),
            # отправляем новое сообщение (если есть query.message)
            if update.callback_query.message:
                 await update.callback_query.message.reply_text(clean_text(help_text), reply_markup=InlineKeyboardMarkup(keyboard))
            else: # Редкий случай, если нет query.message, отправляем в чат пользователя
                 await context.bot.send_message(chat_id=update.effective_chat.id, text=clean_text(help_text), reply_markup=InlineKeyboardMarkup(keyboard))

    elif update.message: # Если команда пришла как /help
        await update.message.reply_text(clean_text(help_text), reply_markup=InlineKeyboardMarkup(keyboard))

async def faq_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "faq_close":
        try:
            await query.delete_message() # Пытаемся удалить сообщение с FAQ
        except Exception:
            try: # Если не удалилось, пытаемся отредактировать
                await query.edit_message_text("Раздел помощи закрыт.", reply_markup=None)
            except Exception as e_edit:
                logger.warning(f"Не удалось ни удалить, ни отредактировать сообщение помощи при закрытии: {e_edit}")
        return # Выходим из обработчика

    answer = FAQ_ANSWERS.get(query.data)
    if answer:
        keyboard_back = [[InlineKeyboardButton("⬅️ Назад к вопросам", callback_data="faq_back_to_list")]]
        try:
            await query.edit_message_text(clean_text(answer), reply_markup=InlineKeyboardMarkup(keyboard_back))
        except TelegramError as e:
            if "Message is not modified" not in str(e): logger.warning(f"Ошибка редактирования сообщения в faq_callback: {e}")

    elif query.data == "faq_back_to_list": # Возврат к списку вопросов FAQ
        keyboard_faq_list = [
            [InlineKeyboardButton("❓ Как задать вопрос для Таро?", callback_data="faq_tarot_question")],
            [InlineKeyboardButton("❓ Что нужно для Матрицы Судьбы?", callback_data="faq_matrix_data")],
            [InlineKeyboardButton("❓ Сколько ждать ответ?", callback_data="faq_wait_time")],
            [InlineKeyboardButton("❓ Это бесплатно?", callback_data="faq_free_service")],
            [InlineKeyboardButton("⬅️ Закрыть помощь", callback_data="faq_close")],
        ]
        try:
            await query.edit_message_text(
                clean_text("Чем могу помочь? Выберите вопрос из списка ниже:"),
                reply_markup=InlineKeyboardMarkup(keyboard_faq_list))
        except TelegramError as e:
            if "Message is not modified" not in str(e): logger.warning(f"Ошибка редактирования сообщения в faq_callback (back_to_list): {e}")


if __name__ == "__main__":
    logger.info("MAIN: Начало блока if __name__ == '__main__'")
    try:
        logger.info("MAIN: Создание ApplicationBuilder...")
        app_builder = ApplicationBuilder().token(BOT_TOKEN)
        logger.info("MAIN: ApplicationBuilder создан.")

        logger.info("MAIN: Установка concurrent_updates...")
        app_builder.concurrent_updates(True) # Рекомендуется True или число для асинхронности
        logger.info("MAIN: concurrent_updates установлен.")

        logger.info("MAIN: Создание и установка JobQueue...")
        # JobQueue создается по умолчанию, если не передан None.
        # app_builder.job_queue(JobQueue()) # Можно и так, если нужны кастомные настройки JobQueue
        logger.info("MAIN: JobQueue будет использован по умолчанию.")

        logger.info("MAIN: Сборка приложения (app = app_builder.build())...")
        application = app_builder.build() # Используем application как имя переменной для ясности
        logger.info("MAIN: Приложение собрано.")

        # ConversationHandler
        logger.info("MAIN: Определение ConversationHandler...")
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler("start", start_command)],
            states={
                CHOOSE_SERVICE: [
                    CallbackQueryHandler(choose_service_callback, pattern="^(tarot|matrix|contact_direct|back_to_start|help_section)$")
                ],
                ASK_MATRIX_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_matrix_name_message)],
                ASK_MATRIX_DOB: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_matrix_dob_message)],
                CONFIRM_MATRIX_DATA: [CallbackQueryHandler(confirm_matrix_data_callback, pattern="^confirm_final_matrix$")],
                
                ASK_TAROT_MAIN_PERSON_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_tarot_main_person_name_message)],
                ASK_TAROT_MAIN_PERSON_DOB: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_tarot_main_person_dob_message)],
                ASK_TAROT_BACKSTORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_tarot_backstory_message)],
                ASK_TAROT_OTHER_PEOPLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_tarot_other_people_message)],
                ASK_TAROT_QUESTIONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_tarot_questions_message)],
                SHOW_TAROT_CONFIRM_OPTIONS: [
                    CallbackQueryHandler(edit_field_tarot_callback, pattern=f"^{EDIT_PREFIX_TAROT}"), # Обработка всех кнопок редактирования Таро
                    CallbackQueryHandler(confirm_tarot_data_callback, pattern="^confirm_final_tarot$")
                ],
            },
            fallbacks=[
                CommandHandler("cancel", cancel_conv_command),
                CommandHandler("start", start_command), # Позволяет перезапустить диалог из любого состояния
                CallbackQueryHandler(cancel_conv_inline_callback, pattern=f"^{CANCEL_CALLBACK_DATA}$")
            ],
            per_message=False, # Состояние привязано к пользователю и чату, а не к сообщению
            # name="main_conversation", # Можно дать имя для отладки
            # persistent=True, # Если нужна персистентность состояний (требует настройки persistence)
        )
        logger.info("MAIN: ConversationHandler определен.")
        application.add_handler(conv_handler)
        logger.info("MAIN: ConversationHandler добавлен в приложение.")

        # Остальные обработчики
        logger.info("MAIN: Добавление обработчика handle_satisfaction_and_other_callbacks...")
        application.add_handler(CallbackQueryHandler(handle_satisfaction_and_other_callbacks, pattern="^(satisfaction_|detailed_fb_)"))
        logger.info("MAIN: Добавлен.")

        logger.info("MAIN: Добавление обработчика help_command (как CommandHandler)...")
        application.add_handler(CommandHandler("help", help_command)) # /help теперь обрабатывается здесь
        logger.info("MAIN: Добавлен.")
        
        logger.info("MAIN: Добавление обработчика faq_callback (для кнопок FAQ)...")
        application.add_handler(CallbackQueryHandler(faq_callback, pattern="^faq_")) # Обработка всех callback_data, начинающихся с faq_
        logger.info("MAIN: Добавлен.")


        logger.info("MAIN: Добавление админских команд...")
        application.add_handler(CommandHandler("stats", admin_stats))
        application.add_handler(CommandHandler("clear_user", admin_clear_user))
        application.add_handler(CommandHandler("get_logs", admin_get_logs))
        application.add_handler(CommandHandler("get_completed_list", admin_get_completed_list))
        logger.info("MAIN: Админские команды добавлены.")

        logger.info("MAIN: Добавление post_fallback_message (для сообщений вне диалогов)...")
        # Этот обработчик должен иметь низкий group, чтобы срабатывать после ConversationHandler
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, post_fallback_message), group=1) 
        logger.info("MAIN: Добавлен.")
        
        logger.info("MAIN: === Бот Замира (финальная настройка): Все обработчики добавлены. ===")
        logger.info("MAIN: === Попытка запуска application.run_polling()... ===")

        try:
            application.run_polling(allowed_updates=Update.ALL_TYPES)
            logger.info("MAIN: application.run_polling() завершился без ошибок (неожиданно, бот должен работать постоянно).")
        except Exception as e_poll:
            critical_error_msg = f"КРИТИЧЕСКАЯ ОШИБКА ВО ВРЕМЯ application.run_polling() ИЛИ ЕГО НАСТРОЙКИ: {e_poll}"
            logger.critical(critical_error_msg, exc_info=True)
            # Попытка отправить уведомление, если 'application' уже создано
            if 'application' in locals() and hasattr(application, 'bot'):
                temp_context = ContextTypes.DEFAULT_TYPE(application=application)
                asyncio.run(send_admin_notification(temp_context, critical_error_msg, critical=True))
            else: # Аварийная отправка, если application не успел создаться
                try:
                    temp_app_for_error = ApplicationBuilder().token(BOT_TOKEN).build()
                    temp_context_fallback = ContextTypes.DEFAULT_TYPE(application=temp_app_for_error)
                    asyncio.run(send_admin_notification(temp_context_fallback, critical_error_msg, critical=True))
                except Exception as e_send_fallback_critical:
                     logger.error(f"Не удалось отправить КРИТИЧЕСКОЕ уведомление администратору даже через временное приложение: {e_send_fallback_critical}")


    except Exception as e_main:
        critical_error_msg_main = f"КРИТИЧЕСКАЯ ОШИБКА В БЛОКЕ if __name__ == '__main__': {e_main}"
        logger.critical(critical_error_msg_main, exc_info=True)
        # Попытка отправить уведомление, если 'application' уже создано
        if 'application' in locals() and hasattr(application, 'bot'):
            temp_context_main = ContextTypes.DEFAULT_TYPE(application=application)
            asyncio.run(send_admin_notification(temp_context_main, critical_error_msg_main, critical=True))
        else: # Аварийная отправка, если application не успел создаться
            try:
                temp_app_main_error = ApplicationBuilder().token(BOT_TOKEN).build()
                temp_context_fallback_main = ContextTypes.DEFAULT_TYPE(application=temp_app_main_error)
                asyncio.run(send_admin_notification(temp_context_fallback_main, critical_error_msg_main, critical=True))
            except Exception as e_send_fallback_main:
                logger.error(f"Не удалось отправить уведомление администратору о КРИТИЧЕСКОЙ ошибке в main даже через временное приложение: {e_send_fallback_main}")

    logger.info("MAIN: Скрипт bot.py завершил свое выполнение (это сообщение не должно появляться, если бот работает нормально и запущен через run_polling).")
