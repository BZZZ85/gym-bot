import os
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
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
async def create_db_pool():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL)

# ===== FSM состояния =====
class AddApproachStates(StatesGroup):
    waiting_for_exercise = State()
    waiting_for_new_exercise = State()
    waiting_for_sets = State()
    waiting_for_reps = State()

# ===== Подключение к БД =====

# ===== Подключение к БД и инициализация таблиц =====
async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL)

    async with db_pool.acquire() as conn:
        # ===== Таблица упражнений =====
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS exercises (
                id SERIAL PRIMARY KEY,
                user_id BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
                exercise TEXT,
                approach INT,
                reps TEXT,
                weight TEXT,
                created_at TIMESTAMP DEFAULT now()
            )
        """)

        # Удаляем старую колонку name, если она осталась
        try:
            await conn.execute("ALTER TABLE exercises DROP COLUMN IF EXISTS name;")
        except Exception as e:
            print("Ошибка при удалении колонки name:", e)

        # ===== Таблица записей тренировок =====
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


# ===== Функция вставки упражнения в БД с весом =====
async def add_exercise_to_db(user_id, exercise_text, approach=1, reps="", weight=None):
    """
    Вставляет новое упражнение для пользователя в таблицу exercises.
    approach: количество подходов
    reps: строка повторений через пробел, например "10 12 15"
    weight: вес в кг
    """
    if not exercise_text or exercise_text.strip() == "":
        return  # пустой текст игнорируем

    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO exercises (user_id, exercise, approach, reps, weight)
            VALUES ($1, $2, $3, $4, $5)
            """,
            user_id, exercise_text.strip(), approach, reps.strip(), weight
        )
def parse_exercise_input(text: str):
    """
    Пример ввода: "Жим лежа 3 10 12 15 60"
    Формат: <название упражнения> <подходы> <повторения через пробел> <вес>
    """
    parts = text.strip().split()
    if len(parts) < 4:
        return None

    try:
        weight = float(parts[-1])              # последний элемент — вес
        approach = int(parts[-(len(parts)-2)]) # количество подходов
        reps = " ".join(parts[-(len(parts)-2)+1:-1])  # все элементы между подходами и весом
        exercise_text = " ".join(parts[:-(len(parts)-2)])  # название упражнения
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
            [KeyboardButton(text="➕ Добавить подход")],
            [KeyboardButton(text="⏰ Напоминания"), KeyboardButton(text="🔄 Рестарт бота")]
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


async def save_record(user_id, exercise, sets, reps_list):
    reps_str = " ".join(map(str, reps_list))
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO records (user_id, exercise, sets, reps) VALUES ($1, $2, $3, $4)",
            user_id, exercise, sets, reps_str
        )

async def get_user_records(user_id):
    global db_pool
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

# ===== Обработчик кнопки 📈 Прогресс =====
# ===== Обработчик кнопки 📈 Прогресс =====
@dp.message(lambda m: m.text == "📈 Прогресс")
async def progress(message: types.Message):
    user_id = message.from_user.id
    await show_progress_graph(message, user_id)
# ===== Показ прогресса =====
async def show_progress_graph(message: types.Message, user_id: int):
    records = await get_user_records(user_id)
    if not records:
        await message.answer("У вас пока нет записей.", reply_markup=main_kb())
        return

    from collections import defaultdict
    exercises_dict = defaultdict(list)
    for r in records:
        exercises_dict[r['exercise']].append(r)

    for exercise, recs in exercises_dict.items():
        dates = [r['date'].strftime('%d-%m-%Y') for r in recs]
        sets = [r['sets'] for r in recs]
        repetitions = ['-'.join(r['reps'].split()) for r in recs]

        fig, ax = plt.subplots(figsize=(6, 3))
        ax.bar(dates, sets, color="skyblue")
        ax.set_title(f"Динамика подходов: {exercise}")
        ax.set_ylabel("Подходы")
        ax.set_xlabel("Дата")

        # Подписи повторений
        for i, rep in enumerate(repetitions):
            ax.text(i, sets[i]+0.1, rep, ha='center', fontsize=8)

        filename = f"progress_{user_id}_{exercise}.png"
        plt.tight_layout()
        plt.savefig(filename, format='png', dpi=100)
        plt.close(fig)

        # Отправка фото
        try:
            await message.answer_photo(FSInputFile(filename))
        except Exception as e:
            await message.answer(f"Не удалось отправить график для {exercise}: {e}")

        if os.path.exists(filename):
            os.remove(filename)

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
