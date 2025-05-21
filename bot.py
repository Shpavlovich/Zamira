import os
import logging
import openai
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters
)

# Настройки
openai.api_key = os.getenv("OPENAI_API_KEY")
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
user_data = {}

# Очистка текста от невалидных символов
def clean_text(text):
    return re.sub(r'[^\x20-\x7Eа-яА-ЯёЁ0-9—\n\r\t.,:;!?()"\[\]{}\-+=@#$%^&*/\\\'<>=|а-яА-ЯёЁ ]', '', text)

# Промт для Таро
PROMPT_TAROT = """Ты — Мира, 42 года. Женщина с эзотерическим даром... (оставьте весь промт как вы присылали, без изменений)"""

# Промт для Матрицы судьбы
PROMPT_MATRIX = """Ты — Мира, 42 года. Эзотерик, ясновидящая... (тоже оставьте полностью как вы указали)"""

WELCOME_TEXT = """Здравствуйте!
Первый расклад на Таро или разбор по матрице судьбы — бесплатно. Единственная просьба с моей стороны — оставить потом отзыв на Авито

Как оставить заявку? Всё просто:
1. Нажмите /start, если ещё не нажимали.
2. Выберите, что вам нужно — расклад на Таро или матрица судьбы.
3. Бот подскажет, какие данные нужно прислать. Просто отвечайте по списку — ничего лишнего придумывать не нужно.
4. Очень важно: без чёткого запроса я не работаю. Не нужно писать «просто посмотрите» или «а что вы скажете». Чем конкретнее ваш вопрос — тем точнее ответ.
5. Все ваши сообщения сразу приходят мне в личку. Никаких автоответов — всё читаю и разбираю лично, вручную.
6. После этого просто ждите. Обычно я отвечаю в течение 2–3 часов, в зависимости от загруженности.

Этот бот — просто помощник, чтобы собрать заявки.
Я — настоящая, всё делаю сама и очень стараюсь для каждого.
Спасибо, что выбрали меня.
С уважением,
Замира"""

INSTRUCTION_TAROT = """Чтобы я сделала расклад, пришлите, пожалуйста, следующие данные:

— Ваше имя и дату рождения.
— Имена и возраст других людей, если ваш вопрос касается не только вас.
— Краткую предысторию: что происходит сейчас и почему вы обратились.
— Чёткий вопрос к картам.

Когда всё напишете — нажмите кнопку «Подтвердить».

Нажимайте на кнопку только после того, как отправите всю нужную информацию.
Можно писать как в одном сообщении, так и по частям."""

INSTRUCTION_MATRIX = """Чтобы я сделала разбор по матрице судьбы, пришлите, пожалуйста:

— Дату рождения (ДД.ММ.ГГГГ)
— Имя (можно без фамилии)

Когда всё напишете — нажмите кнопку «Подтвердить».

Нажимайте на кнопку только после того, как отправите всю нужную информацию.
Можно писать как в одном сообщении, так и по частям."""

RESPONSE_WAIT = """Спасибо, я всё получила!
Ваша заявка ушла ко мне — как только подойду к ней, сразу начну работу.

Обычно отвечаю в течение 2 часов, но всё зависит от загруженности.
Пожалуйста, просто ожидайте — я иду по очереди, никого не пропускаю.

Благодарю вас за терпение и доверие!"""

REVIEW_TEXT = """Если вас устроил расклад или разбор по матрице,
для энергообмена обязательно оставьте отзыв на Авито.
Без этого прогноз может не сбыться или пойти совсем иначе."""

def get_main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Расклад Таро", callback_data="tarot")],
        [InlineKeyboardButton("Матрица судьбы", callback_data="matrix")]
    ])

def get_confirm_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Подтвердить", callback_data="confirm")]
    ])

async def ask_gpt(prompt: str) -> str:
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.85,
        max_tokens=3500
    )
    return response.choices[0].message.content.strip()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_data[update.effective_user.id] = {"type": None, "text": ""}
    await update.message.reply_text(clean_text(WELCOME_TEXT), reply_markup=get_main_keyboard())

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    if query.data == "tarot":
        user_data[user_id] = {"type": "tarot", "text": ""}
        await query.message.reply_text(clean_text(INSTRUCTION_TAROT), reply_markup=get_confirm_keyboard())
    elif query.data == "matrix":
        user_data[user_id] = {"type": "matrix", "text": ""}
        await query.message.reply_text(clean_text(INSTRUCTION_MATRIX), reply_markup=get_confirm_keyboard())
    elif query.data == "confirm":
        data = user_data.get(user_id)
        if not data or not data["text"].strip():
            await query.message.reply_text(clean_text("Вы ещё ничего не написали."))
            return
        await query.message.reply_text(clean_text(RESPONSE_WAIT))
        prompt = PROMPT_TAROT.format(input_text=data["text"]) if data["type"] == "tarot" else PROMPT_MATRIX.format(input_text=data["text"])
        result = await ask_gpt(prompt)
        await query.message.reply_text(clean_text(result))
        await query.message.reply_text(clean_text(REVIEW_TEXT))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text:
        user_id = update.message.from_user.id
        if user_id in user_data:
            user_data[user_id]["text"] += "\n" + update.message.text.strip()

async def ignore_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(clean_text("Пожалуйста, не отправляйте фото или вложения. Только текст."))

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.ATTACHMENT | filters.PHOTO | filters.VIDEO | filters.DOCUMENT, ignore_media))
    app.run_polling()
