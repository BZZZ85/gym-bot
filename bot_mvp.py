import os
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
import asyncpg

# Загружаем переменные окружения из Railway Variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN не найден. Проверь Variables в Railway.")
if not DATABASE_URL:
    raise ValueError("❌ DATABASE_URL не найден. Проверь Variables в Railway.")

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

async def get_db_pool():
    return await asyncpg.create_pool(DATABASE_URL)

db_pool = asyncio.run(get_db_pool())

# Клавиатура
def main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("📜 История"), KeyboardButton("📈 Прогресс")],
            [KeyboardButton("📊 Статистика"), KeyboardButton("⚙️ Настройки")]
        ],
        resize_keyboard=True
    )

# Таблица пользователей
async def create_tables():
    async with db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT
            )
        """)

# Команда /start
@dp.message()
async def start(message: types.Message):
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (user_id, username) VALUES ($1, $2) ON CONFLICT (user_id) DO NOTHING",
            message.from_user.id, message.from_user.username
        )
    await message.answer(
        "Привет! Бот для учёта тренировок.\n\nВыберите действие:",
        reply_markup=main_kb()
    )

# Основной запуск
async def main():
    await create_tables()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
