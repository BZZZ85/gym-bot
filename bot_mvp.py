import os
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
import asyncpg
from dotenv import load_dotenv

# Загружаем локальный .env только если он существует
if os.path.exists("ton.env"):
    load_dotenv("ton.env")

# Сначала пробуем взять переменные из окружения (Railway Variables), если нет — из ton.env
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

# Подключение к базе данных
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

# Клавиатура меню
# Клавиатура меню
def main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="📜 История"),
                KeyboardButton(text="📈 Прогресс")
            ],
            [
                KeyboardButton(text="📊 Статистика"),
                KeyboardButton(text="⚙️ Настройки")
            ]
        ],
        resize_keyboard=True
    )

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
    # Обработка кнопки "Добавить подход"
@dp.message(F.text == "➕ Добавить подход")
async def add_reps_prompt(message: types.Message):
    await message.answer("Введите количество подходов (число):")
    dp.data[message.from_user.id] = {"state": "waiting_for_reps"}

# Получение количества подходов
@dp.message()
async def add_reps(message: types.Message):
    user_data = dp.data.get(message.from_user.id)
    if user_data and user_data.get("state") == "waiting_for_reps":
        try:
            reps = int(message.text)
            async with db_pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO exercises (user_id, reps) VALUES ($1, $2)",
                    message.from_user.id, reps
                )
            await message.answer(f"✅ Добавлено {reps} подходов!")
        except ValueError:
            await message.answer("❌ Введите корректное число.")
        finally:
            dp.data[message.from_user.id] = {}  # Сброс состояния


# Основной запуск
async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
