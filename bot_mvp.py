import os
import asyncio
import pytz
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, FSInputFile
import asyncpg
from dotenv import load_dotenv
from datetime import datetime, timedelta
import matplotlib.pyplot as plt

# ===== Загрузка переменных окружения =====
if os.path.exists("ton.env"):
    load_dotenv("ton.env")

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN не найден. Проверь Variables в Railway или ton.env.")
if not DATABASE_URL:
    raise ValueError("❌ DATABASE_URL не найден. Проверь Variables в Railway или ton.env.")

# ===== Инициализация бота и диспетчера =====
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

class ShowProgressStates(StatesGroup):
    waiting_for_exercise = State()

# ===== Инициализация БД =====
async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL)

    async with db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT
            )
        """)
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
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS records (
                id SERIAL PRIMARY KEY,
                user_id BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
                exercise TEXT,
                sets INT,
                reps TEXT,
                weight TEXT,
                date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                user_id BIGINT PRIMARY KEY,
                time TEXT,
                enabled BOOLEAN DEFAULT TRUE
            )
        """)

# ===== Работа с пользователями и упражнениями =====
async def add_user(user_id, username):
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO users (user_id, username)
            VALUES ($1, $2)
            ON CONFLICT (user_id) DO NOTHING
        """, user_id, username)

async def get_exercises(user_id):
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT exercise FROM exercises WHERE user_id=$1", user_id)
        return [r['exercise'] for r in rows]

async def add_exercise_to_db(user_id, exercise_text, approach=1, reps="", weights=None):
    async with db_pool.acquire() as conn:
        exists = await conn.fetchrow(
            "SELECT id FROM exercises WHERE user_id=$1 AND exercise=$2",
            user_id, exercise_text.strip()
        )
        weight_str = " ".join(map(str, weights)) if weights else None

        if exists:
            await conn.execute(
                "UPDATE exercises SET approach=$1, reps=$2, weight=$3 WHERE user_id=$4 AND exercise=$5",
                approach, reps, weight_str, user_id, exercise_text.strip()
            )
        else:
            await conn.execute(
                "INSERT INTO exercises (user_id, exercise, approach, reps, weight) VALUES ($1,$2,$3,$4,$5)",
                user_id, exercise_text.strip(), approach, reps, weight_str
            )

async def save_record(user_id, exercise, sets, reps_list, weights_list=None):
    reps_str = " ".join(map(str, reps_list))
    if weights_list:
        if len(weights_list) < len(reps_list):
            weights_list = weights_list + [weights_list[-1]] * (len(reps_list) - len(weights_list))
        weight_str = " ".join(map(str, weights_list))
    else:
        weight_str = None

    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO records (user_id, exercise, sets, reps, weight) VALUES ($1,$2,$3,$4,$5)",
            user_id, exercise, sets, reps_str, weight_str
        )

async def get_user_records(user_id, limit=50):
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT exercise, sets, reps, weight, date FROM records WHERE user_id=$1 ORDER BY date DESC LIMIT $2",
            user_id, limit
        )
        return rows

# ===== Вспомогательные клавиатуры =====
def main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("📜 История"), KeyboardButton("📈 Прогресс"), KeyboardButton("📊 Статистика")],
            [KeyboardButton("➕ Добавить подход"), KeyboardButton("🗑 Удалить упражнение")],
            [KeyboardButton("⏰ Напоминания"), KeyboardButton("🔄 Рестарт бота")]
        ], resize_keyboard=True
    )

def exercises_kb(exercises: list[str]):
    exercises = [ex for ex in exercises if ex]
    if exercises:
        kb_buttons = [[KeyboardButton(text=ex)] for ex in exercises] + [
            [KeyboardButton("➕ Добавить новое упражнение")],
            [KeyboardButton("↩ В меню")]
        ]
    else:
        kb_buttons = [[KeyboardButton("➕ Добавить новое упражнение")],
                      [KeyboardButton("↩ В меню")]]
    return ReplyKeyboardMarkup(keyboard=kb_buttons, resize_keyboard=True, one_time_keyboard=True)

# ===== Парсер ввода упражнения =====
def parse_exercise_input(text: str):
    parts = text.strip().split()
    if len(parts) < 4:
        return None
    try:
        weight = float(parts[-1])
        approach = int(parts[-(len(parts)-2)])
        reps = " ".join(parts[-(len(parts)-2)+1:-1])
        exercise_text = " ".join(parts[:-(len(parts)-2)])
    except ValueError:
        return None
    return exercise_text, approach, reps, weight

# ===== FSM и обработчики =====
@dp.message(CommandStart())
async def start(message: types.Message, state: FSMContext = None):
    await add_user(message.from_user.id, message.from_user.username)
    await message.answer("Привет! Бот для учёта тренировок.\nВыберите действие:", reply_markup=main_kb())
    if state:
        await state.clear()

# ===== Добавление подхода =====
@dp.message(lambda m: m.text == "➕ Добавить подход")
async def add_approach_button(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    exercises = await get_exercises(user_id)
    if not exercises:
        await message.answer("У вас пока нет упражнений. Добавьте новое!",
                             reply_markup=ReplyKeyboardMarkup(
                                 keyboard=[
                                     [KeyboardButton("➕ Добавить новое упражнение")],
                                     [KeyboardButton("↩ В меню")]
                                 ],
                                 resize_keyboard=True
                             ))
        await state.set_state(AddApproachStates.waiting_for_exercise)
        return
    kb = exercises_kb(exercises)
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

@dp.message(AddApproachStates.waiting_for_new_exercise)
async def process_new_exercise(message: types.Message, state: FSMContext):
    text = message.text.strip()
    user_id = message.from_user.id
    if text == "↩ В меню":
        await start(message, state)
        return
    await add_exercise_to_db(user_id, text)
    await state.update_data(exercise=text)
    await message.answer(f"✅ Упражнение '{text}' добавлено!")
    await ask_for_sets(message, state)

async def ask_for_sets(message: types.Message, state: FSMContext):
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("1️⃣"), KeyboardButton("2️⃣"), KeyboardButton("3️⃣")],
            [KeyboardButton("4️⃣"), KeyboardButton("5️⃣")],
            [KeyboardButton("↩ В меню")]
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
    try:
        reps = list(map(int, message.text.strip().split()))
    except ValueError:
        await message.answer("❗ Введите числа через пробел.")
        return
    data = await state.get_data()
    sets = data.get("sets")
    if len(reps) != sets:
        await message.answer(f"❗ Вы должны ввести {sets} чисел.")
        return
    await state.update_data(reps=reps)
    await message.answer(f"Введите вес для каждого подхода через пробел (например: 60 70 80):")
    await state.set_state(AddApproachStates.waiting_for_weight)

@dp.message(AddApproachStates.waiting_for_weight)
async def process_weight(message: types.Message, state: FSMContext):
    try:
        weights = list(map(float, message.text.strip().split()))
    except ValueError:
        await message.answer("❗ Введите числа через пробел.")
        return

    data = await state.get_data()
    reps = data['reps']
    sets = data['sets']
    exercise = data['exercise']

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
        weights_list = r['weight'].split() if r.get('weight') else ['0']*r['sets']
        msg_text += f"{r['date'].strftime('%d-%m-%Y')} — {r['exercise']}:\n"
        for i in range(r['sets']):
            rep = reps_list[i] if i < len(reps_list) else reps_list[-1]
            w = weights_list[i] if i < len(weights_list) else weights_list[-1]
            msg_text += f"{i+1}️⃣ {rep} повторений | {w} кг\n"
        msg_text += "-"*20 + "\n"
    await message.answer(msg_text, reply_markup=main_kb())

# ===== Планировщик напоминаний =====
MOSCOW_TZ = pytz.timezone("Europe/Moscow")

async def reminder_scheduler(bot: Bot):
    """Планировщик напоминаний с учётом локального времени."""
    global db_pool
    sent_today = set()
    while True:
        if db_pool is None:
            await asyncio.sleep(5)
            continue

        now = datetime.now(MOSCOW_TZ).replace(second=0, microsecond=0)
        now_str = now.strftime("%H:%M")

        try:
            async with db_pool.acquire() as conn:
                reminders = await conn.fetch("SELECT user_id, time FROM reminders WHERE enabled = TRUE")
                for r in reminders:
                    reminder_time = str(r["time"]).strip()[:5]
                    try:
                        reminder_time = datetime.strptime(reminder_time, "%H:%M").strftime("%H:%M")
                    except ValueError:
                        continue
                    key = (r["user_id"], reminder_time)
                    if reminder_time == now_str and key not in sent_today:
                        try:
                            await bot.send_message(r["user_id"], "🏋️ Время тренировки! Не забудьте разминку 💪")
                            sent_today.add(key)
                        except Exception as e:
                            print(f"❌ Ошибка отправки уведомления пользователю {r['user_id']}: {e}")
                if now.hour == 0 and now.minute == 0:
                    sent_today.clear()
        except Exception as e:
            print(f"❌ Ошибка в reminder_scheduler: {e}")

        await asyncio.sleep(60)

# ===== Запуск бота =====
async def main():
    await init_db()
    asyncio.create_task(reminder_scheduler(bot))
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
