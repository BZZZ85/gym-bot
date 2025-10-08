import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, FSInputFile
from datetime import datetime
from io import BytesIO
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import asyncpg

API_TOKEN = "8442431194:AAHqrL5Uv-boQHXf_68f6or3i1pZmJDMqy0"
DATABASE_URL = DATABASE_URL = "postgresql://neondb_owner:npg_0eRPsTi9tJAj@ep-winter-snow-ab9o1qut-pooler.eu-west-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require"


storage = MemoryStorage()
bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=storage)
pool: asyncpg.pool.Pool = None

# ===== FSM =====
class AddApproachStates(StatesGroup):
    waiting_for_exercise = State()
    waiting_for_new_exercise = State()
    waiting_for_sets = State()
    waiting_for_reps = State()
    waiting_for_weights = State()

# ===== Главное меню =====
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

def main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="🏋️ Добавить упражнение"),
                KeyboardButton(text="📋 Мои упражнения")
            ],
            [
                KeyboardButton(text="📜 История"),
                KeyboardButton(text="📈 Прогресс"),
                KeyboardButton(text="📊 Статистика")
            ],
            [
                KeyboardButton(text="⏰ Напоминание"),
                KeyboardButton(text="⚙️ Настройки")
            ]
        ],
        resize_keyboard=True
    )


# ===== Подключение к БД =====
async def create_pool():
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL)

async def add_user(user_id: int, username: str):
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO users(id, username) VALUES($1, $2) ON CONFLICT (id) DO NOTHING",
                user_id, username
            )
    except Exception as e:
        print(f"DB error add_user: {e}")

async def get_exercises(user_id: int):
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT name FROM exercises WHERE user_id=$1", user_id)
            return [r['name'] for r in rows]
    except Exception as e:
        print(f"DB error get_exercises: {e}")
        return []

async def add_exercise(user_id: int, name: str):
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO exercises(user_id, name) VALUES($1, $2)",
                user_id, name
            )
    except Exception as e:
        print(f"DB error add_exercise: {e}")

# ===== Старт =====
@dp.message(CommandStart())
async def start(message: types.Message, state: FSMContext = None):
    await add_user(message.from_user.id, message.from_user.username)
    if state:
        await state.clear()
    await message.answer("Привет! Выберите действие:", reply_markup=main_kb())

# ===== Добавление подхода =====
@dp.message(F.text == "➕ Добавить подход")
async def start_add_approach(message: types.Message, state: FSMContext):
    await state.clear()
    user_exercises = await get_exercises(message.from_user.id)
    kb_buttons = [[KeyboardButton(text=ex)] for ex in user_exercises] + [
        [KeyboardButton("➕ Добавить новое упражнение")],
        [KeyboardButton("↩ В меню")]
    ]
    kb = ReplyKeyboardMarkup(keyboard=kb_buttons, resize_keyboard=True, one_time_keyboard=True)
    await message.answer("Выберите упражнение или добавьте новое:", reply_markup=kb)
    await state.set_state(AddApproachStates.waiting_for_exercise)

@dp.message(AddApproachStates.waiting_for_exercise)
async def process_exercise(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if text == "↩ В меню":
        await start(message, state)
        return
    if text == "➕ Добавить новое упражнение":
        await message.answer("Введите название нового упражнения:")
        await state.set_state(AddApproachStates.waiting_for_new_exercise)
        return
    user_exercises = await get_exercises(message.from_user.id)
    if text not in user_exercises:
        await message.answer("❗ Выберите упражнение из списка или добавьте новое.")
        return
    await state.update_data(exercise=text)
    await message.answer("Сколько подходов?")
    await state.set_state(AddApproachStates.waiting_for_sets)

@dp.message(AddApproachStates.waiting_for_new_exercise)
async def add_new_exercise(message: types.Message, state: FSMContext):
    text = message.text.strip()
    await add_exercise(message.from_user.id, text)
    await state.update_data(exercise=text)
    await message.answer(f"✅ Упражнение '{text}' добавлено!\nСколько подходов?")
    await state.set_state(AddApproachStates.waiting_for_sets)

# ===== Прогресс =====
@dp.message(F.text == "📈 Прогресс")
async def progress(message: types.Message):
    # Пример данных
    user_data = [
        {"date": datetime(2025, 10, 1), "score": 5},
        {"date": datetime(2025, 10, 2), "score": 7},
        {"date": datetime(2025, 10, 3), "score": 6},
    ]
    dates = [d['date'].strftime("%d.%m") for d in user_data]
    scores = [d['score'] for d in user_data]

    # Рисуем график
    plt.figure(figsize=(6, 4))
    plt.plot(dates, scores, marker='o')
    plt.title("Прогресс")
    plt.xlabel("Дата")
    plt.ylabel("Баллы")
    plt.grid(True)

    buf = BytesIO()
    plt.savefig(buf, format='png')
    buf.seek(0)
    plt.close()

    photo = FSInputFile(buf, filename="progress.png")

    # Попытка отправки фото 3 раза
    for _ in range(3):
        try:
            await bot.send_photo(chat_id=message.chat.id, photo=photo, caption="📈 Ваш прогресс")
            break
        except Exception as e:
            await asyncio.sleep(1)
            print(f"Ошибка отправки фото: {e}")

# ===== Запуск бота =====
async def main():
    await create_pool()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
