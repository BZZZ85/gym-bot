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
    waiting_for_weight = State() 

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


async def save_record(user_id, exercise, sets, reps_list, weights_list=None):
    """
    Сохраняет запись тренировки с повторениями и весами по подходам.
    """
    reps_str = " ".join(map(str, reps_list))
    weight_str = " ".join(map(str, weights_list)) if weights_list else None

    # Если весов меньше, чем повторений, дублируем последний
    if weights_list and len(weights_list) < len(reps_list):
        weight_str = " ".join(map(str, weights_list + [weights_list[-1]] * (len(reps_list) - len(weights_list))))

    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO records (user_id, exercise, sets, reps, weight) VALUES ($1, $2, $3, $4, $5)",
            user_id, exercise, sets, reps_str, weight_str
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
        dates, avg_weights, volumes = [], [], []
        report_text = f"🏋️ Прогресс: {exercise}\n\n"

        for r in recs:
            date_str = r['date'].strftime('%d-%m-%Y')
            dates.append(date_str)

            # --- Парсим повторения и веса ---
            reps = [int(x) for x in r['reps'].split()] if r['reps'] else []
            weights = []
            if 'weight' in r and r['weight']:
                try:
                    weights = [float(x) for x in r['weight'].split()]
                except ValueError:
                    weights = []

            # Если весов меньше, чем повторений — дублируем последний
            while len(weights) < len(reps):
                weights.append(weights[-1] if weights else 0)

            # Средний вес и общий тоннаж
            avg_weight = round(sum(weights) / len(weights), 1) if weights else 0
            volume = sum(w * r_ for w, r_ in zip(weights, reps))

            avg_weights.append(avg_weight)
            volumes.append(volume)

            # --- Текстовый отчёт ---
            report_text += (
                f"{date_str} — подходы: {r['sets']} | "
                f"повторений: {'-'.join(map(str, reps))} | "
                f"вес(кг): {'-'.join(map(str, weights))} | "
                f"тоннаж: {volume} кг\n"
            )

        # --- Рисуем график ---
        fig, ax1 = plt.subplots(figsize=(6, 3))
        ax1.plot(dates, avg_weights, color="orange", marker="o", label="Средний вес (кг)")
        ax1.set_xlabel("Дата")
        ax1.set_ylabel("Вес (кг)", color="orange")

        ax2 = ax1.twinx()
        ax2.bar(dates, volumes, color="skyblue", alpha=0.6, label="Общий тоннаж (кг)")
        ax2.set_ylabel("Тоннаж (кг)", color="blue")

        fig.suptitle(f"{exercise}")
        fig.legend(loc="upper left", bbox_to_anchor=(0.1, 0.9))
        plt.xticks(rotation=30, ha='right')
        plt.tight_layout()

        filename = f"progress_{user_id}_{exercise}.png"
        plt.savefig(filename, format='png', dpi=120)
        plt.close(fig)

        # --- Отправка фото с текстом ---
        try:
            await message.answer_photo(FSInputFile(filename), caption=report_text)
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
