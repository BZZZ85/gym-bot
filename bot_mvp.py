import os
import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
import asyncpg
from dotenv import load_dotenv

# Загружаем .env
load_dotenv()

BOT_TOKEN = os.environ.get("BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")

if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN не найден. Проверь .env или Railway Variables.")
if not DATABASE_URL:
    raise ValueError("❌ DATABASE_URL не найден. Проверь .env или Railway Variables.")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Подключение к БД
async def get_db_pool():
    return await asyncpg.create_pool(DATABASE_URL)

db_pool = asyncio.run(get_db_pool())

# Состояния для FSM
class TrainingStates(StatesGroup):
    waiting_for_exercise = State()
    waiting_for_sets = State()
    waiting_for_reps = State()

# Главное меню
def main_kb():
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("➕ Новая тренировка")],
            [KeyboardButton("📜 История"), KeyboardButton("📈 Прогресс")]
        ],
        resize_keyboard=True
    )
    return kb

# Старт
@dp.message(Command("start"))
async def start(message: types.Message):
    async with db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS exercises (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                exercise TEXT,
                sets INT,
                reps INT
            )
        """)
    await message.answer(
        "Привет! Я бот для учёта тренировок 💪\n\nВыберите действие:",
        reply_markup=main_kb()
    )

# Новая тренировка
@dp.message(F.text == "➕ Новая тренировка")
async def new_training(message: types.Message, state: FSMContext):
    await message.answer("Введите название упражнения:")
    await state.set_state(TrainingStates.waiting_for_exercise)

# Получаем название упражнения
@dp.message(TrainingStates.waiting_for_exercise)
async def get_exercise(message: types.Message, state: FSMContext):
    await state.update_data(exercise=message.text)
    await message.answer("Введите количество подходов:")
    await state.set_state(TrainingStates.waiting_for_sets)

# Получаем количество подходов
@dp.message(TrainingStates.waiting_for_sets)
async def get_sets(message: types.Message, state: FSMContext):
    try:
        sets = int(message.text)
        await state.update_data(sets=sets)
        await message.answer("Введите количество повторений:")
        await state.set_state(TrainingStates.waiting_for_reps)
    except ValueError:
        await message.answer("Введите число, например 3")

# Получаем количество повторений
@dp.message(TrainingStates.waiting_for_reps)
async def get_reps(message: types.Message, state: FSMContext):
    try:
        reps = int(message.text)
        data = await state.get_data()
        async with db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO exercises (user_id, exercise, sets, reps)
                VALUES ($1, $2, $3, $4)
            """, message.from_user.id, data["exercise"], data["sets"], reps)

        await message.answer(
            f"✅ Упражнение '{data['exercise']}' сохранено!\n"
            f"Подходов: {data['sets']}, Повторений: {reps}",
            reply_markup=main_kb()
        )
        await state.clear()

    except ValueError:
        await message.answer("Введите число, например 10")

# Запуск
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
