import os
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
import asyncpg
from dotenv import load_dotenv


# Загружаем переменные из .env
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN не найден. Проверь .env или Variables в Railway.")
if not DATABASE_URL:
    raise ValueError("❌ DATABASE_URL не найден. Проверь .env или Variables в Railway.")


# Инициализация aiogram
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Подключение к базе данных (глобально)
db_pool = None

async def get_db_pool():
    global db_pool
    if db_pool is None:
        db_pool = await asyncpg.create_pool(DATABASE_URL)
    return db_pool


# Клавиатура
def main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("📜 История"), KeyboardButton("📈 Прогресс")],
            [KeyboardButton("📊 Статистика"), KeyboardButton("⚙️ Настройки")]
        ],
        resize_keyboard=True
    )


# Создание таблицы
async def create_tables():
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT
            )
        """)


# Команда /start
@dp.message()
async def start(message: types.Message):
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (user_id, username) VALUES ($1, $2) ON CONFLICT (user_id) DO NOTHING",
            message.from_user.id, message.from_user.username
        )

    await message.answer(
        "👋 Привет! Это бот для учёта тренировок.\n\nВыберите действие:",
        reply_markup=main_kb()
    )


# Основной запуск
async def main():
    await create_tables()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
