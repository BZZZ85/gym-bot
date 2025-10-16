import os
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
import asyncpg
from dotenv import load_dotenv
from collections import defaultdict
from datetime import datetime
from itertools import groupby
import matplotlib.pyplot as plt
import io
from datetime import datetime
from aiogram import types
from aiogram.types import FSInputFile
from datetime import datetime, timedelta, time
import pytz
from aiogram.types import Message
from aiogram import F
import aiohttp
from aiogram import Router, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters.text import Text


# Загружаем локальный .env только если он есть
if os.path.exists("ton.env"):
    load_dotenv("ton.env")

# Получаем переменные окружения (Railway Variables или ton.env)
print("DEBUG: available env keys:", sorted(k for k in os.environ.keys() if "BOT" in k or "TOKEN" in k))


BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
bot = Bot(token=os.getenv("BOT_TOKEN"))
dp = Dispatcher(storage=MemoryStorage())
router = Router()
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN не найден. Проверь Variables в Railway или ton.env.")
if not DATABASE_URL:
    raise ValueError("❌ DATABASE_URL не найден. Проверь Variables в Railway или ton.env.")

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

db_pool = None
async def create_db_pool():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL)

# ===== FSM состояния =====
class AddApproachStates(StatesGroup):
    waiting_for_exercise = State()
    waiting_for_new_exercise = State()
    waiting_for_sets = State()
    waiting_for_reps = State()
    waiting_for_weight = State() 
class DeleteExerciseStates(StatesGroup):
    waiting_for_exercise_to_delete = State()
class ReminderState(StatesGroup):
    waiting_for_time = State()
class HistoryStates(StatesGroup):
    waiting_for_exercise = State()

class StatisticsStates(StatesGroup):
    waiting_for_exercise = State()




# ===== Подключение к БД и инициализация таблиц =====
async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL)

    async with db_pool.acquire() as conn:
        # ===== Таблица пользователей =====
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT
            )
        """)
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            user_id BIGINT PRIMARY KEY,
            time TIME,
            enabled BOOLEAN DEFAULT TRUE
        );
        """)

        # ===== Таблица упражнений =====
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS exercises (
                user_id BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
                exercise TEXT,
                approach INT,
                reps TEXT,
                weight TEXT,
                created_at TIMESTAMP DEFAULT now()
            )
        """)

        # Добавляем колонку id, если её нет
        await conn.execute("""
            ALTER TABLE exercises 
            ADD COLUMN IF NOT EXISTS id SERIAL PRIMARY KEY;
        """)

        # ===== Таблица записей тренировок =====
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS records (
                user_id BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
                exercise TEXT,
                sets INT,
                reps TEXT,
                date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Добавляем колонку weight, если её нет
        await conn.execute("""
            ALTER TABLE records 
            ADD COLUMN IF NOT EXISTS weight TEXT;
        """)
        # ===== Таблица напоминаний =====
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
            user_id BIGINT PRIMARY KEY,
            time TEXT,                -- время напоминания (например, "09:00")
            enabled BOOLEAN DEFAULT TRUE
    )
""")



        await conn.execute("ALTER TABLE records ADD COLUMN IF NOT EXISTS weight TEXT;")


# ===== Функция вставки упражнения в БД с весом =====
async def add_exercise_to_db(user_id, exercise_text, approach=1, reps="", weights=None):
    """
    Добавляет новое упражнение, если его ещё нет у пользователя.
    weights: список весов для каждого подхода
    """
    async with db_pool.acquire() as conn:
        exists = await conn.fetchrow(
            "SELECT id FROM exercises WHERE user_id=$1 AND exercise=$2",
            user_id, exercise_text.strip()
        )
        if exists:
            # Если упражнение уже есть, можно обновить reps и weights
            await conn.execute(
                "UPDATE exercises SET approach=$1, reps=$2, weight=$3 WHERE user_id=$4 AND exercise=$5",
                approach, reps, " ".join(map(str, weights)) if weights else None, user_id, exercise_text.strip()
            )
            return

        await conn.execute(
            """
            INSERT INTO exercises (user_id, exercise, approach, reps, weight)
            VALUES ($1, $2, $3, $4, $5)
            """,
            user_id, exercise_text.strip(), approach, reps, " ".join(map(str, weights)) if weights else None
        )
@dp.message(Command("прогресс"))
async def progress_command(message: Message):
    """
    Анализ прогресса по заданному упражнению.
    Пример: /прогресс Жим лёжа
    """
    user_id = message.from_user.id
    parts = message.text.split(maxsplit=1)

    if len(parts) < 2:
        await message.answer("⚠️ Укажи упражнение после команды.\nНапример: <b>/прогресс Жим лёжа</b>")
        return

    exercise = parts[1].strip()

    try:
        suggestion = await suggest_next_progress(user_id, exercise)
        await message.answer(suggestion, parse_mode="HTML")
    except Exception as e:
        print(f"❌ Ошибка при анализе прогресса: {e}")
        await message.answer("❌ Не удалось получить прогресс. Проверь, есть ли записи для этого упражнения.")
@dp.message(lambda message: message.text == "📈 Прогресс")
async def progress_button_handler(message: Message, state: FSMContext):
    user_id = message.from_user.id
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT DISTINCT exercise FROM exercises
            WHERE user_id = $1 AND exercise IS NOT NULL AND exercise != ''
        """, user_id)
        exercises = [r["exercise"] for r in rows]

    await show_progress_menu(message, exercises, state)


def parse_exercise_input(text: str):
    """
    Пример ввода: "Жим лежа 3 10 12 15 60"
    Формат: <название упражнения> <подходы> <повторения через пробел> <вес>
    """
    parts = text.strip().split()
    if len(parts) < 4:
        return None
    try:
        weight = float(parts[-1])           # последний элемент — вес
        approach = int(parts[-(len(parts)-2)])  # количество подходов
        reps = " ".join(parts[-(len(parts)-2)+1:-1])  # повторения
        exercise_text = " ".join(parts[:-(len(parts)-2)])  # название
    except ValueError:
        return None
    return exercise_text, approach, reps, weight


# ===== FSM-хэндлер для добавления нового упражнения =====
@dp.message(AddApproachStates.waiting_for_new_exercise)
async def process_new_exercise(message: types.Message, state: FSMContext):
    text = message.text.strip()
    user_id = message.from_user.id

    if text == "↩ В меню":
        await start(message, state)
        return

    # Добавляем упражнение в БД
    await add_exercise_to_db(user_id, text)

    await state.update_data(exercise=text)
    await message.answer(f"✅ Упражнение '{text}' добавлено!")

    await ask_for_sets(message, state)

# ===== Главное меню =====


def main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📜 История"), KeyboardButton(text="📈 Прогресс"), KeyboardButton(text="📊 Статистика")],
            [KeyboardButton(text="➕ Добавить подход"), KeyboardButton(text="🗑 Удалить упражнение")],
            [KeyboardButton(text="⏰ Напоминания"), KeyboardButton(text="🔄 Рестарт бота")],
            [KeyboardButton(text="🥗 Рацион")]  # новая кнопка для дневника питания
        ],
        resize_keyboard=True
    )


# ===== Работа с БД =====
async def add_user(user_id, username):
    global db_pool  # используем глобальный пул соединений
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users (user_id, username)
            VALUES ($1, $2)
            ON CONFLICT (user_id) DO NOTHING
            """,
            user_id,
            username
        )



async def get_exercises(user_id):
    global db_pool
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT exercise FROM exercises WHERE user_id=$1", user_id)
        return [r['exercise'] for r in rows]

async def add_exercise(user_id, exercise):
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO exercises (user_id, exercise) VALUES ($1, $2)",
            user_id,
            exercise
        )


async def save_record(user_id, exercise, sets, reps_list, weights_list=None):
    """
    Сохраняет запись тренировки с повторениями и весами по подходам.
    """
    reps_str = " ".join(map(str, reps_list))

    # Если веса переданы
    if weights_list:
        # Если весов меньше чем повторов, дублируем последний
        if len(weights_list) < len(reps_list):
            weights_list = weights_list + [weights_list[-1]] * (len(reps_list) - len(weights_list))
        weight_str = " ".join(map(str, weights_list))
    else:
        weight_str = None

    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO records (user_id, exercise, sets, reps, weight) VALUES ($1, $2, $3, $4, $5)",
            user_id, exercise, sets, reps_str, weight_str
        )

async def get_user_records(user_id):
    global db_pool
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT exercise, sets, reps, weight, date FROM records WHERE user_id=$1 ORDER BY date DESC LIMIT 10",
            user_id
        )
        return rows
        # Клавиатура с одной кнопкой
food_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🍽 Что ел")]
    ],
    resize_keyboard=True,
    one_time_keyboard=True
)

# Доступные блины
AVAILABLE_WEIGHTS = [20, 15, 10, 5, 2.5, 1.25]

def round_up_weight(weight: float) -> float:
    """
    Округляем вес вверх до ближайшего доступного блина.
    """
    higher_options = [w for w in AVAILABLE_WEIGHTS if w >= weight]
    return min(higher_options) if higher_options else max(AVAILABLE_WEIGHTS)

async def suggest_next_progress(user_id: int, exercise: str) -> str:
    """
    Возвращает рекомендации по весам на каждый подход на следующей тренировке.
    Увеличение на 5% и округление вверх.
    """
    async with db_pool.acquire() as conn:
        # Берём последнюю запись по упражнению
        row = await conn.fetchrow("""
            SELECT weight 
            FROM records 
            WHERE user_id=$1 AND exercise=$2
            ORDER BY date DESC
            LIMIT 1
        """, user_id, exercise)

    if not row or not row['weight']:
        return "Нет данных для анализа прогресса. Добавьте подходы для упражнения."

    try:
        last_weights = [float(w) for w in row['weight'].split()]
    except ValueError:
        return "Ошибка в данных о весах. Проверьте последние записи упражнения."

    # Рассчитываем +5% и округляем вверх
    suggested_weights = [round_up_weight(w * 1.05) for w in last_weights]
    recommended_weights = []
    for i, (reps, weight) in enumerate(zip(last_reps, last_weights)):
        if new_reps and len(new_reps) > i:
            adjusted = adjust_weight_for_reps(weight, reps, new_reps[i])
        else:
            adjusted = math.ceil(weight * 1.05)  # стандартное +5%
        recommended_weights.append(adjusted)

    # Формируем текст рекомендации
    text = "💡 Рекомендуемый вес для следующей тренировки по подходам:\n"
    for i, w in enumerate(recommended_weights, 1):
        text += f"Подход {i}: {w} кг\n"

    await message.answer(text)

API_KEY = "LzcoYkPzgepXTwXcMaEm+w==yAt1WFOi0dH7cwO3"

# Словарь для перевода популярных продуктов с русского на английский
RU_TO_EN = {
    "яйца": "eggs",
    "яйцо": "egg",
    "овсянка": "oats",
    "молоко": "milk",
    "курица": "chicken breast",
    "рис": "rice",
    "банан": "banana",
    "яблоко": "apple",
    # добавь свои продукты
}
def food_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton(text="🍝 Макароны", callback_data="food_макароны"),
        InlineKeyboardButton(text="🥛 Молоко", callback_data="food_молоко"),
        InlineKeyboardButton(text="🍬 Конфета", callback_data="food_конфета"),
        InlineKeyboardButton(text="🍌 Банан", callback_data="food_банан"),
    )
    return kb
@router.callback_query(Text(startswith="food_"))
async def process_food_selection(callback: types.CallbackQuery):
    food_name = callback.data[5:]  # убираем "food_"
    await callback.message.answer(f"Сколько грамм {food_name}?")
    await callback.answer()  # убрать "часики" на кнопке

# Дневник пользователей
user_diary = {}  # ключ: user_id, значение: список всех приёмов пищи
@router.message(lambda message: message.text == "🥗 Рацион")
async def open_food_diary(message: types.Message):
    await message.answer(
        "🍽 Отлично! Напиши, что ты ел сегодня.\nНапример: <code>Ел 2 яйца и 100 г овсянки</code>",
        parse_mode="HTML"
    )
async def get_nutrition_info(food_query: str):
    """Запрос к CalorieNinjas API"""
    url = f"https://api.calorieninjas.com/v1/nutrition?query={food_query}"
    headers = {"X-Api-Key": API_KEY}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            if response.status != 200:
                return None
            data = await response.json()
            return data.get("items", [])

def translate_to_english(text: str) -> str:
    """Перевод русских продуктов на английский через словарь"""
    words = text.lower().split()
    translated = []
    for word in words:
        translated.append(RU_TO_EN.get(word, word))
    return " ".join(translated)

# --- Суммируем КБЖУ ---
def summarize_nutrition(items):
    total = {"calories": 0, "protein": 0, "fat": 0, "carbohydrates": 0}
    details = []

    for item in items:
        name = item["name"].capitalize()
        kcal = item["calories"]
        protein = item["protein_g"]
        fat = item["fat_total_g"]
        carbs = item["carbohydrates_total_g"]

        details.append(f"{name}: {kcal:.0f} ккал | Б:{protein:.1f} Ж:{fat:.1f} У:{carbs:.1f}")
        total["calories"] += kcal
        total["protein"] += protein
        total["fat"] += fat
        total["carbohydrates"] += carbs

    summary = (
        f"🔥 Итого за приём пищи: {total['calories']:.0f} ккал\n"
        f"Б: {total['protein']:.1f} г | Ж: {total['fat']:.1f} г | У: {total['carbohydrates']:.1f} г"
    )
    return details, summary, total
# --- Команда /start ---
@router.message(Command("start"))
async def start(message: types.Message):
    await message.answer(
        "Привет! Я твой рациональный помощник 🦾\n"
        "Нажми кнопку ниже, чтобы добавить приём пищи:",
        reply_markup=food_keyboard()
    )
# --- Нажатие кнопки продукта ---
@router.callback_query(lambda c: c.data.startswith("food_"))
async def choose_food(callback: types.CallbackQuery):
    food_name = callback.data.replace("food_", "")
    user_id = callback.from_user.id

    if user_id not in user_diary:
        user_diary[user_id] = {"meals": []}

    user_diary[user_id]["current_food"] = food_name
    await callback.message.answer(f"Сколько грамм {food_name}?")
    await callback.answer()  # закрываем "часики"

# --- Ввод количества грамм ---
@router.message(lambda message: message.text.isdigit())
async def process_food_amount(message: types.Message):
    user_id = message.from_user.id
    if user_id not in user_diary or "current_food" not in user_diary[user_id]:
        return

    food_name = user_diary[user_id]["current_food"]
    grams = int(message.text)
    query = f"{grams} g {RU_TO_EN.get(food_name, food_name)}"

    items = await get_nutrition_info(query)
    if not items:
        await message.answer("❌ Не удалось получить данные по продукту.")
        return

    details, summary, total_meal = summarize_nutrition(items)
    user_diary[user_id]["meals"].append(total_meal)
    del user_diary[user_id]["current_food"]

    # Суммарно за день
    total_day = {"calories": 0, "protein": 0, "fat": 0, "carbohydrates": 0}
    for meal in user_diary[user_id]["meals"]:
        total_day["calories"] += meal["calories"]
        total_day["protein"] += meal["protein"]
        total_day["fat"] += meal["fat"]
        total_day["carbohydrates"] += meal["carbohydrates"]

    day_summary = (
        f"📊 Всего за день: {total_day['calories']:.0f} ккал | "
        f"Б: {total_day['protein']:.1f} г | Ж: {total_day['fat']:.1f} г | У: {total_day['carbohydrates']:.1f} г"
    )

    text = "🍽 Твое питание:\n\n" + "\n".join(details) + "\n\n" + summary + "\n\n" + day_summary
    await message.answer(text)


dp.include_router(router)

# ===== Обработчики =====
@dp.message(CommandStart())
async def start(message: types.Message, state: FSMContext = None):
    await add_user(message.from_user.id, message.from_user.username)
    await message.answer("Привет! Бот для учёта тренировок.\n\nВыберите действие:", reply_markup=main_kb())
    if state:
        await state.clear()

# ===== Добавить подход =====


def sets_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="1️⃣"), KeyboardButton(text="2️⃣"), KeyboardButton(text="3️⃣")],
            [KeyboardButton(text="4️⃣"), KeyboardButton(text="5️⃣")],
            [KeyboardButton(text="↩ В меню")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
# ===== Функция удаления упражнения из БД =====
async def delete_exercise_from_db(user_id: int, exercise: str):
    """
    Удаляет упражнение пользователя из таблицы exercises.
    """
    async with db_pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM exercises WHERE user_id=$1 AND exercise=$2",
            user_id,
            exercise
        )

# ===== Добавить подход =====
# ===== Функция для клавиатуры с упражнениями =====
def exercises_kb(exercises: list[str]):
    # оставляем только валидные строки
    exercises = [ex for ex in exercises if ex and isinstance(ex, str)]

    if exercises:
        kb_buttons = [[KeyboardButton(text=ex)] for ex in exercises] + [
            [KeyboardButton(text="➕ Добавить новое упражнение")],
            [KeyboardButton(text="↩ В меню")]
        ]
    else:
        kb_buttons = [
            [KeyboardButton(text="➕ Добавить новое упражнение")],
            [KeyboardButton(text="↩ В меню")]
        ]
    return ReplyKeyboardMarkup(keyboard=kb_buttons, resize_keyboard=True, one_time_keyboard=True)
# ===== Обработчик кнопки "➕ Добавить подход" =====
@dp.message(lambda m: m.text == "➕ Добавить подход")
async def add_approach_button(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    exercises = await get_exercises(user_id)

    if not exercises:
        await message.answer(
            "У вас пока нет упражнений. Добавьте новое!",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text="➕ Добавить новое упражнение")],
                    [KeyboardButton(text="↩ В меню")]
                ],
                resize_keyboard=True
            )
        )
        await state.set_state(AddApproachStates.waiting_for_exercise)
        return

    kb = exercises_kb(exercises)
    await message.answer("Выберите упражнение или добавьте новое:", reply_markup=kb)
    await state.set_state(AddApproachStates.waiting_for_exercise)

@dp.message(lambda m: m.text == "🔔 Установить время напоминания")
async def ask_time(message: types.Message, state: FSMContext):
    await message.answer("🕒 Введите время напоминания в формате HH:MM (например, 09:00):")
    await state.set_state(ReminderState.waiting_for_time)

@dp.message(ReminderState.waiting_for_time)
async def save_reminder_time(message: types.Message, state: FSMContext):
    time_text = message.text.strip()
    user_id = message.from_user.id

    try:
        # Преобразуем текст в объект времени
        reminder_time = datetime.strptime(time_text, "%H:%M").time()

        async with db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO reminders (user_id, time, enabled)
                VALUES ($1, $2, TRUE)
                ON CONFLICT (user_id) DO UPDATE
                SET time = EXCLUDED.time,
                    enabled = TRUE
            """, user_id, reminder_time.strftime("%H:%M"))

        await message.answer(f"✅ Напоминание установлено на {time_text}")
        await state.clear()

    except ValueError:
        await message.answer("❌ Неверный формат времени. Используйте формат ЧЧ:ММ (например, 09:00)")
    except Exception as e:
        await message.answer(f"❌ Ошибка при сохранении напоминания: {e}")

    # простая валидация формата
    import re
    if not re.match(r"^\d{2}:\d{2}$", time_text):
        await message.answer("⚠️ Неверный формат. Введите время в формате HH:MM, например 07:30.")
        return

    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO reminders (user_id, time, enabled)
            VALUES ($1, $2, TRUE)
            ON CONFLICT (user_id)
            DO UPDATE SET time = EXCLUDED.time, enabled = TRUE
        """, user_id, time_text)

    await message.answer(f"✅ Напоминание установлено на {time_text}. Я напомню вам о тренировке 💪", reply_markup=main_kb())
    await state.clear()

@dp.message(lambda m: m.text == "🔕 Выключить напоминания")
async def disable_reminders(message: types.Message):
    user_id = message.from_user.id
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE reminders SET enabled = FALSE WHERE user_id = $1", user_id)
    await message.answer("🔕 Напоминания отключены.", reply_markup=main_kb())

# ===== Добавление подхода =====
@dp.message(lambda m: m.text.startswith("Добавить:"))
async def new_exercise(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    text = message.text.replace("Добавить:", "").strip()
    parsed = parse_exercise_input(text)

    if not parsed:
        await message.answer("❗ Неверный формат. Пример: Жим лежа 3 10 12 15 60")
        return

    exercise_text, approach, reps, weight = parsed
    await add_exercise_to_db(user_id, exercise_text, approach, reps, weight)
    await message.answer(
        f"✅ Добавлено:\nУпражнение: {exercise_text}\nПодходов: {approach}\nПовторений: {reps}\nВес: {weight} кг"
    )

    # Получаем актуальный список упражнений
    exercises = await get_exercises(user_id)
    kb = exercises_kb(exercises)
    await message.answer("Выберите упражнение или добавьте новое:", reply_markup=kb)
    await state.set_state(AddApproachStates.waiting_for_exercise)


    # Фильтруем None
    exercises = [ex for ex in exercises if ex]

    kb_buttons = [[KeyboardButton(text=ex)] for ex in exercises] + \
                 [[KeyboardButton(text="➕ Добавить новое упражнение")],
                  [KeyboardButton(text="↩ В меню")]] \
                 if exercises else [[KeyboardButton(text="➕ Добавить новое упражнение")],
                                    [KeyboardButton(text="↩ В меню")]]

    kb = ReplyKeyboardMarkup(keyboard=kb_buttons, resize_keyboard=True, one_time_keyboard=True)
    await message.answer("Выберите упражнение или добавьте новое:", reply_markup=kb)
    await state.set_state(AddApproachStates.waiting_for_exercise)


@dp.message(AddApproachStates.waiting_for_exercise)
async def process_exercise(message: types.Message, state: FSMContext):
    text = message.text.strip()
    user_id = message.from_user.id

    if text == "↩ В меню":
        await start(message, state)
        return

    exercises = [ex.lower() for ex in await get_exercises(user_id) if ex]
    if text == "➕ Добавить новое упражнение":
        await message.answer("Введите название нового упражнения:")
        await state.set_state(AddApproachStates.waiting_for_new_exercise)
        return
    elif text.lower() not in exercises:
        await message.answer("❗ Выберите упражнение из списка или добавьте новое.")
        return

    # 👉 Без вызова suggest_next_progress
    await state.update_data(exercise=text)
    await ask_for_sets(message, state)







async def ask_for_sets(message: types.Message, state: FSMContext):
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="1️⃣"), KeyboardButton(text="2️⃣"), KeyboardButton(text="3️⃣")],
            [KeyboardButton(text="4️⃣"), KeyboardButton(text="5️⃣")],
            [KeyboardButton(text="↩ В меню")]
        ],
        resize_keyboard=True, one_time_keyboard=True
    )
    await message.answer("Выберите количество подходов:", reply_markup=kb)
    await state.set_state(AddApproachStates.waiting_for_sets)


@dp.message(AddApproachStates.waiting_for_sets)
async def process_sets(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if text == "↩ В меню":
        await start(message, state)
        return

    try:
        sets = int(text[0])
    except ValueError:
        await message.answer("❗ Пожалуйста, выберите количество подходов с кнопок.")
        return

    await state.update_data(sets=sets)
    await message.answer(f"Введите количество повторений для каждого из {sets} подходов через пробел (например: 10 10 12):")
    await state.set_state(AddApproachStates.waiting_for_reps)


@dp.message(AddApproachStates.waiting_for_reps)
async def process_reps(message: types.Message, state: FSMContext):
    text = message.text.strip()
    try:
        reps = list(map(int, text.split()))
    except ValueError:
        await message.answer("❗ Введите числа через пробел.")
        return

    data = await state.get_data()
    sets = data.get("sets")
    if len(reps) != sets:
        await message.answer(f"❗ Вы должны ввести {sets} чисел.")
        return

    # Сохраняем в state и спрашиваем веса
    await state.update_data(reps=reps)
    await message.answer(f"Введите вес для каждого подхода через пробел (например: 60 70 80):")
    await state.set_state(AddApproachStates.waiting_for_weight)


@dp.message(AddApproachStates.waiting_for_weight)
async def process_weight(message: types.Message, state: FSMContext):
    text = message.text.strip()
    try:
        weights = list(map(float, text.split()))
    except ValueError:
        await message.answer("❗ Введите числа через пробел.")
        return

    data = await state.get_data()
    reps = data['reps']
    sets = data['sets']
    exercise = data['exercise']

    # Если весов меньше, чем подходов, дублируем последний
    while len(weights) < sets:
        weights.append(weights[-1])

    await save_record(message.from_user.id, exercise, sets, reps, weights)
    await message.answer(
        f"✅ Записано: {exercise} — подходы: {sets}, повторений: {reps}, вес: {weights}",
        reply_markup=main_kb()
    )
    await state.clear()

# ===== Обработчик кнопки "Удалить упражнение" =====
# ===== Обработчик кнопки "Удалить упражнение" =====
@dp.message(lambda m: m.text == "🗑 Удалить упражнение")
async def choose_exercise_to_delete(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    exercises = await get_exercises(user_id)

    # Фильтруем None или пустые значения
    exercises = [ex for ex in exercises if ex and isinstance(ex, str)]

    if not exercises:
        await message.answer("У вас пока нет упражнений для удаления.", reply_markup=main_kb())
        return

    kb_buttons = [[KeyboardButton(text=ex)] for ex in exercises]
    kb_buttons.append([KeyboardButton(text="↩ В меню")])
    kb = ReplyKeyboardMarkup(keyboard=kb_buttons, resize_keyboard=True, one_time_keyboard=True)

    await message.answer("Выберите упражнение для удаления:", reply_markup=kb)
    await state.set_state(DeleteExerciseStates.waiting_for_exercise_to_delete)


# ===== Обработка выбора упражнения для удаления =====
@dp.message(DeleteExerciseStates.waiting_for_exercise_to_delete)
async def process_exercise_deletion(message: types.Message, state: FSMContext):
    text = message.text.strip()
    user_id = message.from_user.id

    if text == "↩ В меню":
        await start(message, state)
        return

    exercises = await get_exercises(user_id)
    if text not in exercises:
        await message.answer("❗ Выберите упражнение из списка.")
        return

    # Удаляем упражнение
    await delete_exercise_from_db(user_id, text)
    await message.answer(f"✅ Упражнение '{text}' удалено.", reply_markup=main_kb())
    await state.clear()

# ===== История =====
@dp.message(lambda m: m.text == "📜 История")
async def history_menu(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    exercises = await get_exercises(user_id)

    if not exercises:
        await message.answer("У вас пока нет упражнений.", reply_markup=main_kb())
        return

    kb_buttons = [[KeyboardButton(text=ex)] for ex in exercises if ex]
    kb_buttons.append([KeyboardButton(text="↩ В меню")])
    kb = ReplyKeyboardMarkup(keyboard=kb_buttons, resize_keyboard=True, one_time_keyboard=True)

    await message.answer("Выберите упражнение для просмотра истории:", reply_markup=kb)
    await state.set_state(HistoryStates.waiting_for_exercise)
# ===== Обработка выбора упражнения для истории =====
@dp.message(HistoryStates.waiting_for_exercise)
async def show_history(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if text == "↩ В меню":
        await start(message, state)
        return

    user_id = message.from_user.id
    records = await get_user_records(user_id)
    selected_records = [r for r in records if r['exercise'] == text]

    if not selected_records:
        await message.answer("Нет записей по выбранному упражнению.", reply_markup=main_kb())
        await state.clear()
        return

    msg_text = f"📊 История: {text}\n\n"
    for r in selected_records:
        reps_list = r['reps'].split()
        weights_list = r['weight'].split() if r.get('weight') else ['0'] * r['sets']
        msg_text += f"{r['date'].strftime('%d-%m-%Y')} — подходы: {r['sets']}\n"
        for i in range(r['sets']):
            rep = reps_list[i] if i < len(reps_list) else reps_list[-1]
            w = weights_list[i] if i < len(weights_list) else weights_list[-1]
            msg_text += f"{i+1}️⃣ {rep} повторений | {w} кг\n"
        msg_text += "-"*20 + "\n"

    await message.answer(msg_text, reply_markup=main_kb())
    await state.clear()

# ===== Обработчик кнопки 📈 Прогресс =====

from aiogram.fsm.state import State, StatesGroup

# ===== FSM состояние для выбора упражнения =====
class ProgressStates(StatesGroup):
    waiting_for_exercise = State()

# ===== Обработчик кнопки 📈 Прогресс =====
# ===== Обработчик кнопки 📈 Прогресс =====
@dp.message(lambda m: m.text == "📈 Прогресс")
async def show_progress_menu(message: Message, exercises: list, state: FSMContext):
    """
    Показать меню выбора упражнения для анализа прогресса.
    """
    keyboard = [[KeyboardButton(text=ex)] for ex in exercises] + [[KeyboardButton(text="↩ В меню")]]
    markup = ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

    await message.answer("Выбери упражнение:", reply_markup=markup)
    await state.set_state(ShowProgressStates.waiting_for_exercise)



# ===== FSM для выбора упражнения =====
class ShowProgressStates(StatesGroup):
    waiting_for_exercise = State()




@dp.message(ShowProgressStates.waiting_for_exercise)
async def show_selected_progress(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if text == "↩ В меню":
        await start(message, state)
        return

    user_id = message.from_user.id
    records = await get_user_records(user_id)
    if not records:
        await message.answer("У вас пока нет записей.", reply_markup=main_kb())
        await state.clear()
        return

    # Фильтруем записи по выбранному упражнению
    selected_records = [r for r in records if r['exercise'] == text]
    if not selected_records:
        await message.answer("Нет записей по выбранному упражнению.", reply_markup=main_kb())
        await state.clear()
        return

    # ===== Подготовка данных =====
    dates, avg_weights = [], []
    report_text = f"🏋️ Прогресс: {text}\n\n"
    last_weights_per_set = []

    for r in selected_records:
        date_str = r['date'].strftime('%d-%m-%Y')
        dates.append(date_str)

        reps = [int(x) for x in r['reps'].split()] if r['reps'] else []
        weights = [float(x) for x in r['weight'].split()] if r.get('weight') else [0]*r['sets']

        while len(weights) < len(reps):
            weights.append(weights[-1] if weights else 0)

        avg_weight = round(sum(weights)/len(weights), 1) if weights else 0
        avg_weights.append(avg_weight)
        last_weights_per_set.append(weights)

        reps_str = "-".join(map(str, reps)) if reps else "0"
        weights_str = "-".join(map(str, weights)) if weights else "0"
        report_text += f"{date_str} — подходы: {r['sets']} | повторений: {reps_str} | вес(кг): {weights_str}\n"

    # ===== Рекомендации по каждому подходу =====
    recommendation = ""
    if last_weights_per_set:
        last_weights = last_weights_per_set[-1]
        suggested_weights = [round(w * 1.05 + 0.49)//1 for w in last_weights]  # +5% и округление вверх
        recommendation = "💡 Рекомендуемый вес для следующей тренировки по подходам:\n"
        for i, w in enumerate(suggested_weights, 1):
            recommendation += f"Подход {i}: {w} кг\n"

    # ===== Построение графика =====
    fig, ax = plt.subplots(figsize=(8, 4), constrained_layout=True)
    ax.plot(dates, avg_weights, color="orange", marker="o", label="Средний вес (кг)")
    ax.set_xlabel("Дата")
    ax.set_ylabel("Вес (кг)", color="orange")
    ax.tick_params(axis='y', labelcolor="orange")
    plt.xticks(rotation=45, ha='right')
    ax.set_title(f"Прогресс: {text}")
    ax.legend(loc="upper left")

    filename = f"progress_{user_id}_{text}.png"
    plt.savefig(filename, format='png', dpi=120)
    plt.close(fig)

    # ===== Отправка пользователю =====
    try:
        await message.answer_photo(
            FSInputFile(filename),
            caption=report_text + recommendation,
            reply_markup=main_kb()
        )
    except Exception as e:
        await message.answer(f"Не удалось отправить график: {e}")

    if os.path.exists(filename):
        os.remove(filename)

    await state.clear()


@dp.message(lambda m: m.text == "⏰ Напоминания")
async def reminders_menu(message: types.Message):
    kb = [
        [types.KeyboardButton(text="🔔 Установить время напоминания")],
        [types.KeyboardButton(text="🔕 Выключить напоминания")],
        [types.KeyboardButton(text="⬅️ Назад")]
    ]
    markup = types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)
    await message.answer(
        "⏰ Настройте напоминания о тренировках.\n"
        "Вы можете установить время или отключить уведомления.",
        reply_markup=markup
    )

@dp.message(lambda m: m.text == "⬅️ Назад")
async def back_to_main_from_reminders(message: types.Message, state: FSMContext):
    await start(message, state)



#статистика
# ===== Обработчик кнопки "Статистика" =====
@dp.message(lambda m: m.text == "📊 Статистика")
async def statistics_menu(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    exercises = await get_exercises(user_id)

    if not exercises:
        await message.answer("У вас пока нет упражнений.", reply_markup=main_kb())
        return

    kb_buttons = [[KeyboardButton(text=ex)] for ex in exercises if ex]
    kb_buttons.append([KeyboardButton(text="↩ В меню")])
    kb = ReplyKeyboardMarkup(keyboard=kb_buttons, resize_keyboard=True, one_time_keyboard=True)

    await message.answer("Выберите упражнение для просмотра статистики:", reply_markup=kb)
    await state.set_state(StatisticsStates.waiting_for_exercise)



# ===== Рестарт =====
@dp.message(lambda m: m.text == "🔄 Рестарт бота")
async def restart_bot(message: types.Message):
    await start(message)


# ===== Планировщик напоминаний =====
from datetime import datetime
import asyncio

import pytz
from datetime import datetime, timedelta

# Часовой пояс — например, Москва
MOSCOW_TZ = pytz.timezone("Europe/Moscow")

async def reminder_scheduler(bot):
    """Планировщик напоминаний с учётом локального времени."""
    global db_pool
    sent_today = set()

    while True:
        if db_pool is None:
            await asyncio.sleep(5)
            continue

        # текущее время в Москве
        now = datetime.now(MOSCOW_TZ)
        now_str = now.strftime("%H:%M")

        try:
            async with db_pool.acquire() as conn:
                reminders = await conn.fetch("SELECT user_id, time FROM reminders WHERE enabled = TRUE")

            for r in reminders:
                reminder_time = str(r["time"]).strip()[:5]

                # нормализуем формат времени (например, "9:5" -> "09:05")
                try:
                    reminder_time = datetime.strptime(reminder_time, "%H:%M").strftime("%H:%M")
                except Exception:
                    continue

                key = (r["user_id"], reminder_time)

                if reminder_time == now_str and key not in sent_today:
                    try:
                        await bot.send_message(
                            r["user_id"],
                            "🏋️ Время тренировки! Не забудьте разминку 💪"
                        )
                        print(f"✅ Напоминание отправлено пользователю {r['user_id']} в {now_str}")
                        sent_today.add(key)
                    except Exception as e:
                        print(f"❌ Ошибка отправки уведомления пользователю {r['user_id']}: {e}")

            # очищаем в полночь
            if now.hour == 0 and now.minute == 0:
                sent_today.clear()

        except Exception as e:
            print(f"❌ Ошибка в reminder_scheduler: {e}")

        await asyncio.sleep(20)
# ===== Обработка выбора упражнения для статистики =====
@dp.message(StatisticsStates.waiting_for_exercise)
async def show_statistics_for_exercise(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if text == "↩ В меню":
        await start(message, state)
        return

    user_id = message.from_user.id
    async with db_pool.acquire() as conn:
        records = await conn.fetch(
            "SELECT sets, reps, weight FROM records WHERE user_id=$1 AND exercise=$2",
            user_id, text
        )

    if not records:
        await message.answer("Нет записей по выбранному упражнению.", reply_markup=main_kb())
        await state.clear()
        return

    total_sets = sum(r['sets'] for r in records)
    reps_all = []
    max_weight = 0

    for r in records:
        if r['reps']:
            try:
                reps_list = [int(x) for x in r['reps'].split()]
                reps_all.extend(reps_list)
            except ValueError:
                pass

        if r['weight']:
            try:
                weights_list = [float(x) for x in r['weight'].split()]
                max_weight = max(max_weight, max(weights_list))
            except ValueError:
                pass

    avg_reps = round(sum(reps_all)/len(reps_all),1) if reps_all else 0

    msg = (
        f"📊 Статистика по упражнению '{text}':\n"
        f"Общее количество подходов: {total_sets}\n"
        f"Среднее количество повторений: {avg_reps}\n"
        f"Максимальный вес: {max_weight} кг\n"
    )

    await message.answer(msg, reply_markup=main_kb())
    await state.clear()
import math

def adjust_weight_for_reps(previous_weight: float, previous_reps: int, new_reps: int) -> float:
    """
    Корректирует рекомендуемый вес, если количество повторений изменилось.
    Формула основана на модели Эпли (Epley):
        1RM ≈ вес * (1 + повторы / 30)
    """
    if previous_reps <= 0 or new_reps <= 0:
        return previous_weight

    # Вычисляем примерный 1RM
    one_rm = previous_weight * (1 + previous_reps / 30)

    # Подбираем новый вес под другое количество повторений
    new_weight = one_rm / (1 + new_reps / 30)

    # Округляем в большую сторону до 1 кг
    new_weight = math.ceil(new_weight)

    return new_weight





# ===== Запуск =====
async def main():
    await create_db_pool()  # подключение к базе
    asyncio.create_task(reminder_scheduler(bot))  # <-- передаём bot сюда
    await dp.start_polling(bot)



if __name__ == "__main__":
    asyncio.run(main())
