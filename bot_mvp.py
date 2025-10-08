import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
from aiogram.filters import Command
import asyncpg
import os

# ================== Настройки ==================
BOT_TOKEN = os.getenv("8442431194:AAHqrL5Uv-boQHXf_68f6or3i1pZmJDMqy0")  # Telegram Bot Token
DATABASE_URL = os.getenv(
    "'postgresql://neondb_owner:npg_0eRPsTi9tJAj@ep-winter-snow-ab9o1qut-pooler.eu-west-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require'"
)  # Neon DB: postgresql://user:pass@host/db?sslmode=require

# ================== База данных ==================
async def create_db_pool():
    return await asyncpg.create_pool(DATABASE_URL)

async def create_tables(pool):
    async with pool.acquire() as conn:
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
            name TEXT,
            sets INT,
            reps INT
        )
        """)

async def add_user(pool, user_id: int, username: str):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (user_id, username) VALUES ($1, $2) ON CONFLICT (user_id) DO NOTHING",
            user_id, username
        )

# ================== Клавиатура ==================
def main_kb():
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("📜 История"), KeyboardButton("📈 Прогресс"), KeyboardButton("📊 Статистика")],
            [KeyboardButton("➕ Добавить упражнение")]
        ],
        resize_keyboard=True
    )
    return kb

# ================== Бот ==================
async def main():
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    pool = await create_db_pool()
    await create_tables(pool)

    @dp.message(Command("start"))
    async def start(message: types.Message):
        await add_user(pool, message.from_user.id, message.from_user.username or "")
        await message.answer(
            "Привет! Бот для учёта тренировок.\n\nВыберите действие:",
            reply_markup=main_kb()
        )

    print("Бот запущен...")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        await pool.close()

if __name__ == "__main__":
    asyncio.run(main())
