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
        # ===== Таблица пользователей =====
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT
            )
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
        weights_list = r['weight'].split() if r.get('weight') else ['0'] * r['sets']
        
        msg_text += f"{r['date'].strftime('%d-%m-%Y')} — {r['exercise']}:\n"
        for i in range(r['sets']):
            rep = reps_list[i] if i < len(reps_list) else reps_list[-1]
            w = weights_list[i] if i < len(weights_list) else weights_list[-1]
            msg_text += f"{i+1}️⃣ {rep} повторений | {w} кг\n"
        msg_text += "-"*20 + "\n"

    await message.answer(msg_text, reply_markup=main_kb())

# ===== Обработчик кнопки 📈 Прогресс =====

from aiogram.fsm.state import State, StatesGroup

# ===== FSM состояние для выбора упражнения =====
class ProgressStates(StatesGroup):
    waiting_for_exercise = State()

# ===== Обработчик кнопки "📈 Прогресс" =====
@dp.message(lambda m: m.text == "📈 Прогресс")
async def progress(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    exercises = await get_exercises(user_id)

    if not exercises:
        await message.answer("У вас пока нет упражнений.", reply_markup=main_kb())
        return

    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=ex)] for ex in exercises] +
                 [[KeyboardButton(text="↩ В меню")]],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await message.answer("Выберите упражнение для просмотра прогресса:", reply_markup=kb)
    await state.set_state(ProgressStates.waiting_for_exercise)

# ===== Обработчик выбора упражнения =====
@dp.message(ProgressStates.waiting_for_exercise)
async def progress_select_exercise(message: types.Message, state: FSMContext):
    text = message.text.strip()

    if text == "↩ В меню":
        await start(message, state)
        return

    user_id = message.from_user.id
    exercises = await get_exercises(user_id)

    if text not in exercises:
        await message.answer("❗ Выберите упражнение из списка.")
        return

    await show_progress_graph(message, user_id, exercise=text)
    await state.clear()

# ===== Построение графика без тоннажа =====
async def show_progress_graph(message: types.Message, user_id: int, exercise: str):
    records = await get_user_records(user_id)
    records = [r for r in records if r['exercise'] == exercise]

    if not records:
        await message.answer(f"Записей для упражнения '{exercise}' пока нет.", reply_markup=main_kb())
        return

    dates, sets_list, reps_text_list, weights_text_list = [], [], [], []
    report_text = f"🏋️ Прогресс: {exercise}\n\n"

    for r in records:
        date_str = r['date'].strftime('%d-%m-%Y')
        dates.append(date_str)
        sets = r['sets']
        reps = [int(x) for x in r['reps'].split()] if r['reps'] else [0]*sets
        weights = [float(x) for x in r['weight'].split()] if r.get('weight') else [0]*sets

        while len(weights) < sets:
            weights.append(weights[-1] if weights else 0)
        while len(reps) < sets:
            reps.append(reps[-1] if reps else 0)

        sets_list.append(sets)
        reps_text_list.append("-".join(map(str, reps)))
        weights_text_list.append("-".join(map(str, weights)))

        report_text += (
            f"{date_str} — подходы: {sets} | "
            f"повторений: {'-'.join(map(str, reps))} | "
            f"вес(кг): {'-'.join(map(str, weights))}\n"
        )

    # --- График ---
    fig, ax = plt.subplots(figsize=(6, 3))
    ax.bar(dates, sets_list, color="skyblue")
    ax.set_xlabel("Дата")
    ax.set_ylabel("Подходы")
    ax.set_title(f"Прогресс: {exercise}")

    for i in range(len(dates)):
        ax.text(i, sets_list[i]+0.1, f"{reps_text_list[i]}\n{weights_text_list[i]} кг",
                ha='center', fontsize=8)

    plt.xticks(rotation=30, ha='right')
    plt.tight_layout()

    filename = f"progress_{user_id}_{exercise}.png"
    plt.savefig(filename, format='png', dpi=120)
    plt.close(fig)

    try:
        await message.answer_photo(FSInputFile(filename), caption=report_text)
    except Exception as e:
        await message.answer(f"Не удалось отправить график: {e}")

    if os.path.exists(filename):
        os.remove(filename)


#статистика
@dp.message(lambda m: m.text == "📊 Статистика")
async def show_statistics(message: types.Message):
    user_id = message.from_user.id
    async with db_pool.acquire() as conn:
        records = await conn.fetch("SELECT exercise, sets, reps, weight FROM records WHERE user_id=$1", user_id)

    if not records:
        await message.answer("У вас пока нет записей для статистики.", reply_markup=main_kb())
        return

    total_workouts = len(records)
    total_sets = sum(r['sets'] for r in records)

    # Собираем все веса и повторения
    weights_all = []
    exercise_reps = defaultdict(list)  # {exercise: [reps]}
    exercise_max_weight = defaultdict(float)  # {exercise: max_weight}
    for r in records:
        # Вес
        if r['weight']:
            try:
                weights_list = [float(w) for w in r['weight'].split()]
                weights_all.extend(weights_list)
                exercise_max_weight[r['exercise']] = max(exercise_max_weight[r['exercise']], max(weights_list))
            except ValueError:
                pass

        # Повторения
        if r['reps']:
            try:
                reps_list = [int(x) for x in r['reps'].split()]
                exercise_reps[r['exercise']].extend(reps_list)
            except ValueError:
                pass

    avg_weight = round(sum(weights_all) / len(weights_all), 1) if weights_all else 0

    msg = (
        f"📊 Ваша статистика:\n"
        f"Количество тренировок: {total_workouts}\n"
        f"Общее количество подходов: {total_sets}\n"
        f"Средний вес по подходам: {avg_weight} кг\n\n"
        f"Статистика по упражнениям:\n"
    )

    for exercise, reps in exercise_reps.items():
        avg_reps = round(sum(reps) / len(reps), 1) if reps else 0
        max_reps = max(reps) if reps else 0
        max_weight = exercise_max_weight[exercise] if exercise_max_weight.get(exercise) else 0
        msg += f"- {exercise}: средние {avg_reps} повторений, макс {max_reps} повторений, макс вес {max_weight} кг\n"

    await message.answer(msg, reply_markup=main_kb())



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
