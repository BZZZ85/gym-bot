import os
import asyncio
import asyncpg
from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from dotenv import load_dotenv

# Загружаем .env
load_dotenv("ton.env")

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN не найден. Проверь ton.env или Variables в Railway.")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
db_pool: asyncpg.Pool | None = None

# ===== Состояния FSM =====
class AddApproachStates(StatesGroup):
    waiting_for_exercise = State()
    waiting_for_new_exercise = State()
    waiting_for_sets = State()
    waiting_for_reps = State()

# ===== Главное меню =====
def main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("➕ Добавить подход")],
            [KeyboardButton("📋 Мои упражнения")]
        ],
        resize_keyboard=True
    )

# ===== Подключение к БД =====
async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL)

    async with db_pool.acquire() as conn:
        # Таблица пользователей
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT
            )
        """)

        # Таблица упражнений
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS exercises (
                id SERIAL PRIMARY KEY,
                user_id BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT now()
            )
        """)

        # Таблица записей тренировок
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS records (
                id SERIAL PRIMARY KEY,
                user_id BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
                exercise TEXT,
                sets INT,
                reps TEXT,
                date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

# ===== Сохранение пользователя =====
async def save_user(user_id: int, username: str):
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO users (user_id, username)
            VALUES ($1, $2)
            ON CONFLICT (user_id) DO NOTHING
        """, user_id, username)

# ===== Добавление упражнения =====
async def add_exercise(user_id: int, name: str):
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO exercises (user_id, name) VALUES ($1, $2)",
            user_id, name
        )

# ===== Получение списка упражнений =====
async def get_exercises(user_id: int):
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT name FROM exercises WHERE user_id=$1", user_id)
        return [r["name"] for r in rows if r["name"]]

# ===== Сохранение записи о подходах =====
async def save_record(user_id: int, exercise: str, sets: int, reps: list[int]):
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO records (user_id, exercise, sets, reps) VALUES ($1, $2, $3, $4)",
            user_id, exercise, sets, " ".join(map(str, reps))
        )

# ===== Клавиатура упражнений =====
def exercises_kb(exercises: list[str]):
    kb_buttons = [[KeyboardButton(text=ex)] for ex in exercises if ex]
    kb_buttons.append([KeyboardButton(text="➕ Добавить новое упражнение")])
    kb_buttons.append([KeyboardButton(text="↩ В меню")])
    return ReplyKeyboardMarkup(keyboard=kb_buttons, resize_keyboard=True, one_time_keyboard=True)

# ===== Клавиатура выбора подходов =====
def sets_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("1️⃣"), KeyboardButton("2️⃣"), KeyboardButton("3️⃣")],
            [KeyboardButton("4️⃣"), KeyboardButton("5️⃣")],
            [KeyboardButton("↩ В меню")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

# ===== Команда /start =====
@dp.message(CommandStart())
async def start(message: types.Message, state: FSMContext):
    await save_user(message.from_user.id, message.from_user.username or "")
    await message.answer("🏋 Добро пожаловать! Выберите действие:", reply_markup=main_kb())
    await state.clear()

# ===== Добавление подхода =====
@dp.message(lambda m: m.text == "➕ Добавить подход")
async def start_add_approach(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    exercises = await get_exercises(user_id)
    kb = exercises_kb(exercises)
    await message.answer("Выберите упражнение или добавьте новое:", reply_markup=kb)
    await state.set_state(AddApproachStates.waiting_for_exercise)

# ===== Выбор упражнения =====
@dp.message(AddApproachStates.waiting_for_exercise)
async def process_exercise(message: types.Message, state: FSMContext):
    text = message.text.strip()
    user_id = message.from_user.id

    if text == "↩ В меню":
        await start(message, state)
        return

    if text == "➕ Добавить новое упражнение":
        await message.answer("Введите название нового упражнения:")
        await state.set_state(AddApproachStates.waiting_for_new_exercise)
        return

    exercises = [ex.lower() for ex in await get_exercises(user_id)]
    if text.lower() not in exercises:
        await message.answer("❗ Выберите упражнение из списка или добавьте новое.")
        return

    await state.update_data(exercise=text)
    await message.answer("Выберите количество подходов:", reply_markup=sets_kb())
    await state.set_state(AddApproachStates.waiting_for_sets)

# ===== Добавление нового упражнения =====
@dp.message(AddApproachStates.waiting_for_new_exercise)
async def add_new_exercise(message: types.Message, state: FSMContext):
    text = message.text.strip()
    user_id = message.from_user.id

    if not text or text == "↩ В меню":
        await start(message, state)
        return

    await add_exercise(user_id, text)
    await state.update_data(exercise=text)
    await message.answer(f"✅ Упражнение '{text}' добавлено! Теперь выберите количество подходов:",
                         reply_markup=sets_kb())
    await state.set_state(AddApproachStates.waiting_for_sets)

# ===== Ввод количества подходов =====
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
    await message.answer(f"Введите количество повторений для каждого из {sets} подходов через пробел (например: 10 12 10):")
    await state.set_state(AddApproachStates.waiting_for_reps)

# ===== Ввод повторений =====
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
        await message.answer(f"❗ Нужно ввести {sets} чисел — по количеству подходов.")
        return

    await save_record(message.from_user.id, data['exercise'], sets, reps)
    await message.answer(
        f"✅ Записано: {data['exercise']} — {sets} подход(ов), повторения: {reps}",
        reply_markup=main_kb()
    )
    await state.clear()

# ===== Запуск =====
async def main():
    await init_db()
    print("✅ Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
