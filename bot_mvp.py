import os
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
import asyncpg
from dotenv import load_dotenv

# Загружаем локальный .env только если он есть
if os.path.exists("ton.env"):
    load_dotenv("ton.env")

# Получаем переменные окружения (Railway Variables или ton.env)
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN не найден. Проверь Variables в Railway или ton.env.")
if not DATABASE_URL:
    raise ValueError("❌ DATABASE_URL не найден. Проверь Variables в Railway или ton.env.")

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

db_pool = None

# ===== FSM состояния =====
class AddApproachStates(StatesGroup):
    waiting_for_exercise = State()
    waiting_for_new_exercise = State()
    waiting_for_sets = State()
    waiting_for_reps = State()

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

        # Таблица упражнений (без колонок, которые могут добавляться позже)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS exercises (
                id SERIAL PRIMARY KEY,
                user_id BIGINT REFERENCES users(user_id) ON DELETE CASCADE
            )
        """)

        # Проверяем и добавляем колонки, если их нет
        columns = await conn.fetch("""
            SELECT column_name FROM information_schema.columns 
            WHERE table_name='exercises'
        """)
        column_names = [c['column_name'] for c in columns]

        if 'exercise' not in column_names:
            await conn.execute("ALTER TABLE exercises ADD COLUMN exercise TEXT;")
        if 'approach' not in column_names:
            await conn.execute("ALTER TABLE exercises ADD COLUMN approach INT;")
        if 'reps' not in column_names:
            await conn.execute("ALTER TABLE exercises ADD COLUMN reps TEXT;")
        if 'weight' not in column_names:
            await conn.execute("ALTER TABLE exercises ADD COLUMN weight TEXT;")
        if 'created_at' not in column_names:
            await conn.execute("ALTER TABLE exercises ADD COLUMN created_at TIMESTAMP DEFAULT now();")

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


# ===== Главное меню =====
def main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📜 История"), KeyboardButton(text="📈 Прогресс"), KeyboardButton(text="📊 Статистика")],
            [KeyboardButton(text="➕ Добавить подход")],
            [KeyboardButton(text="⏰ Напоминания"), KeyboardButton(text="🔄 Рестарт бота")]
        ],
        resize_keyboard=True
    )

# ===== Работа с БД =====
async def add_user(user_id, username):
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (user_id, username) VALUES ($1, $2) ON CONFLICT (user_id) DO NOTHING",
            user_id, username
        )

async def get_exercises(user_id):
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT exercise FROM exercises WHERE user_id=$1", user_id)
        return [r['exercise'] for r in rows]

async def add_exercise(user_id, exercise):
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO exercises (user_id, exercise) VALUES ($1, $2)", user_id, exercise)

async def save_record(user_id, exercise, sets, reps_list):
    reps_str = " ".join(map(str, reps_list))
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO records (user_id, exercise, sets, reps) VALUES ($1, $2, $3, $4)",
            user_id, exercise, sets, reps_str
        )

async def get_user_records(user_id):
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT exercise, sets, reps, date FROM records WHERE user_id=$1 ORDER BY date DESC LIMIT 10",
            user_id
        )
        return rows

# ===== Обработчики =====
@dp.message(CommandStart())
async def start(message: types.Message, state: FSMContext = None):
    await add_user(message.from_user.id, message.from_user.username)
    await message.answer("Привет! Бот для учёта тренировок.\n\nВыберите действие:", reply_markup=main_kb())
    if state:
        await state.clear()

# ===== Добавить подход =====
# ===== Функции для клавиатур =====
def main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📜 История"), KeyboardButton(text="📈 Прогресс"), KeyboardButton(text="📊 Статистика")],
            [KeyboardButton(text="➕ Добавить подход")],
            [KeyboardButton(text="⏰ Напоминания"), KeyboardButton(text="🔄 Рестарт бота")]
        ],
        resize_keyboard=True
    )

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

def exercises_kb(exercises: list[str]):
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


# ===== Добавить подход =====
# ===== Добавить подход =====
@dp.message(lambda m: m.text == "➕ Добавить подход")
async def start_add_approach(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    exercises = await get_exercises(user_id)  # вернем список названий

    # Формируем клавиатуру
    kb_buttons = [[KeyboardButton(text=ex)] for ex in exercises] if exercises else []
    kb_buttons += [
        [KeyboardButton("➕ Добавить новое упражнение")],
        [KeyboardButton("↩ В меню")]
    ]
    kb = ReplyKeyboardMarkup(keyboard=kb_buttons, resize_keyboard=True, one_time_keyboard=True)

    await message.answer("Выберите упражнение или добавьте новое:", reply_markup=kb)
    await state.set_state(AddApproachStates.waiting_for_exercise)

# ===== Обработчик выбора упражнения =====
@dp.message(AddApproachStates.waiting_for_exercise)
async def process_exercise(message: types.Message, state: FSMContext):
    text = message.text.strip()
    user_id = message.from_user.id

    if text == "↩ В меню":
        await start(message, state)
        return

    exercises = [ex.lower() for ex in await get_exercises(user_id)]
    if text == "➕ Добавить новое упражнение":
        await message.answer("Введите название нового упражнения:")
        await state.set_state(AddApproachStates.waiting_for_new_exercise)
        return
    elif text.lower() not in exercises:
        await message.answer("❗ Выберите упражнение из списка или добавьте новое.")
        return

    await state.update_data(exercise=text)
    await ask_for_sets(message, state)

# ===== Добавление нового упражнения =====
@dp.message(AddApproachStates.waiting_for_new_exercise)
async def add_new_exercise(message: types.Message, state: FSMContext):
    text = message.text.strip()
    user_id = message.from_user.id

    if text == "↩ В меню":
        await start(message, state)
        return
    if not text:
        await message.answer("❗ Название упражнения не может быть пустым.")
        return

    # Вставляем в колонку name
    await add_exercise(user_id, text)
    await state.update_data(exercise=text)
    await message.answer(f"✅ Упражнение '{text}' добавлено!")
    await ask_for_sets(message, state)

# ===== Вспомогательная функция для выбора количества подходов =====
async def ask_for_sets(message: types.Message, state: FSMContext):
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="1️⃣"), KeyboardButton(text="2️⃣"), KeyboardButton(text="3️⃣")],
            [KeyboardButton(text="4️⃣"), KeyboardButton(text="5️⃣")],
            [KeyboardButton("↩ В меню")]
        ],
        resize_keyboard=True, one_time_keyboard=True
    )
    await message.answer("Выберите количество подходов:", reply_markup=kb)
    await state.set_state(AddApproachStates.waiting_for_sets)

# ===== Обработчик выбора подходов =====
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

# ===== Обработчик ввода повторений =====
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

    await save_record(message.from_user.id, data['exercise'], sets, reps)
    await message.answer(f"✅ Записано: {data['exercise']} — подходы: {sets}, повторений: {reps}", reply_markup=main_kb())
    await state.clear()


# ===== История =====
@dp.message(lambda m: m.text == "📜 История")
async def history(message: types.Message):
    user_id = message.from_user.id
    records = await get_user_records(user_id)
    if not records:
        await message.answer("У вас пока нет записей.", reply_markup=main_kb())
        return
    msg_text = "📊 Последние тренировки:\n\n"
    for r in records:
        reps_list = r['reps'].split()
        msg_text += f"{r['date'].strftime('%d-%m-%Y')} — {r['exercise']}:\n"
        for i in range(r['sets']):
            rep = reps_list[i] if i < len(reps_list) else reps_list[-1]
            msg_text += f"{i+1}️⃣ {rep} повторений\n"
        msg_text += "-"*20 + "\n"
    await message.answer(msg_text, reply_markup=main_kb())

# ===== Рестарт =====
@dp.message(lambda m: m.text == "🔄 Рестарт бота")
async def restart_bot(message: types.Message):
    await start(message)

# ===== Запуск =====
async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
