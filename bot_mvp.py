import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
from datetime import datetime
import asyncpg
import matplotlib.pyplot as plt
from io import BytesIO

# ======== ТВОЙ ТОКЕН ========
API_TOKEN = "8442431194:AAHqrL5Uv-boQHXf_68f6or3i1pZmJDMqy0"

# ======== ССЫЛКА НА БАЗУ NEON ========
DB_URL = "postgresql://neondb_owner:npg_0eRPsTi9tJAj@ep-winter-snow-ab9o1qut-pooler.eu-west-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require"

# ======== FSM состояния ========
class AddApproach(StatesGroup):
    waiting_exercise = State()
    waiting_sets = State()
    waiting_reps = State()
    waiting_weights = State()

# ======== Создание клавиатуры ========
def main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Добавить подход")],
            [KeyboardButton(text="📜 История"), KeyboardButton(text="📈 Прогресс")],
        ],
        resize_keyboard=True
    )

# ======== Подключение к БД ========
async def create_pool():
    pool = await asyncpg.create_pool(DB_URL)
    async with pool.acquire() as conn:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS workouts (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            exercise TEXT,
            sets INT,
            reps TEXT,
            weights TEXT,
            date TIMESTAMP DEFAULT NOW()
        );
        """)
    return pool

# ======== Инициализация ========
storage = MemoryStorage()
bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=storage)
pool = None

# ======== START ========
@dp.message(CommandStart())
async def start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("👋 Привет! Я бот для учёта тренировок.\n\nВыберите действие:", reply_markup=main_kb())

# ======== ДОБАВИТЬ ПОДХОД ========
@dp.message(F.text == "➕ Добавить подход")
async def add_start(message: types.Message, state: FSMContext):
    await message.answer("Введите название упражнения:")
    await state.set_state(AddApproach.waiting_exercise)

@dp.message(AddApproach.waiting_exercise)
async def get_exercise(message: types.Message, state: FSMContext):
    await state.update_data(exercise=message.text.strip())
    await message.answer("Сколько подходов?")
    await state.set_state(AddApproach.waiting_sets)

@dp.message(AddApproach.waiting_sets)
async def get_sets(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("Введите число!")
        return
    await state.update_data(sets=int(message.text))
    await message.answer("Введите количество повторений через пробел (например: 12 10 8):")
    await state.set_state(AddApproach.waiting_reps)

@dp.message(AddApproach.waiting_reps)
async def get_reps(message: types.Message, state: FSMContext):
    await state.update_data(reps=message.text.strip())
    await message.answer("Введите веса через пробел (например: 60 70 80):")
    await state.set_state(AddApproach.waiting_weights)

@dp.message(AddApproach.waiting_weights)
async def get_weights(message: types.Message, state: FSMContext):
    data = await state.get_data()
    exercise = data['exercise']
    sets = data['sets']
    reps = data['reps']
    weights = message.text.strip()

    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO workouts (user_id, exercise, sets, reps, weights)
            VALUES ($1, $2, $3, $4, $5)
        """, message.from_user.id, exercise, sets, reps, weights)

    await message.answer(f"✅ Упражнение '{exercise}' сохранено!\n\nВыберите действие:", reply_markup=main_kb())
    await state.clear()

# ======== ИСТОРИЯ ========
@dp.message(F.text == "📜 История")
async def history(message: types.Message):
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT * FROM workouts WHERE user_id = $1 ORDER BY date DESC LIMIT 10
        """, message.from_user.id)
    if not rows:
        await message.answer("❌ У вас пока нет записей.")
        return

    text = "📜 Последние тренировки:\n\n"
    for r in rows:
        text += f"🏋️ {r['exercise']} — {r['sets']} подходов\nПовторения: {r['reps']}\nВес: {r['weights']}\n📅 {r['date'].strftime('%d.%m.%Y %H:%M')}\n\n"
    await message.answer(text)

# ======== ПРОГРЕСС ========
@dp.message(F.text == "📈 Прогресс")
async def progress(message: types.Message):
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT exercise, SUM((string_to_array(weights, ' '))[1]::int) AS total_weight
                FROM workouts
                WHERE user_id = $1
                GROUP BY exercise
            """, message.from_user.id)

        if not rows:
            await message.answer("📊 Недостаточно данных для графика.")
            return

        exercises = [r['exercise'] for r in rows]
        totals = [r['total_weight'] for r in rows]

        plt.figure(figsize=(6, 4))
        plt.bar(exercises, totals)
        plt.title("Прогресс по упражнениям")
        plt.xlabel("Упражнение")
        plt.ylabel("Сумма весов (кг)")
        plt.xticks(rotation=45)
        plt.tight_layout()

        buf = BytesIO()
        plt.savefig(buf, format='png')
        buf.seek(0)
        plt.close()

        await bot.send_photo(chat_id=message.chat.id, photo=buf, caption="📈 Ваш прогресс по упражнениям")
    except Exception as e:
        await message.answer(f"❗️ Не удалось отправить график: {e}")

# ======== ЗАПУСК ========
async def main():
    global pool
    pool = await create_pool()
    print("✅ Бот запущен и подключён к базе данных Neon")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
