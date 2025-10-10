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

# ===== /start =====
@dp.message(CommandStart())
async def start(message: types.Message, state: FSMContext = None):
    await add_user(message.from_user.id, message.from_user.username)
    await message.answer("Привет! Бот для учёта тренировок.\n\nВыберите действие:", reply_markup=main_kb())
    if state:
        await state.clear()

# ===== Добавление подхода =====
# ===== Добавление подхода =====
@dp.message(lambda m: m.text == "➕ Добавить подход")
async def start_add_approach(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    user_exercises = await get_exercises(user_id)
    
    if not user_exercises:
        kb_buttons = [[KeyboardButton(text="➕ Добавить новое упражнение")], [KeyboardButton(text="↩ В меню")]]
    else:
        kb_buttons = [[KeyboardButton(text=ex)] for ex in user_exercises] + \
                     [[KeyboardButton(text="➕ Добавить новое упражнение")], [KeyboardButton(text="↩ В меню")]]
    
    kb = ReplyKeyboardMarkup(keyboard=kb_buttons, resize_keyboard=True, one_time_keyboard=True)
    await message.answer("Выберите упражнение или добавьте новое:", reply_markup=kb)
    await state.set_state(AddApproachStates.waiting_for_exercise)

# ===== Выбор упражнения =====
@dp.message(AddApproachStates.waiting_for_exercise)
async def process_exercise(message: types.Message, state: FSMContext):
    text = message.text.strip()
    user_id = message.from_user.id

    if text == "↩ В меню":
        await start(message, state)
        return

    user_exercises = [ex.lower() for ex in await get_exercises(user_id)]

    if text == "➕ Добавить новое упражнение":
        await message.answer("Введите название нового упражнения:")
        await state.set_state(AddApproachStates.waiting_for_new_exercise)
        return

    if text.lower() not in user_exercises:
        await message.answer("❗ Выберите упражнение из списка или добавьте новое.")
        return

    await state.update_data(exercise=text)

    # Кнопки для выбора подходов
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="1️⃣"), KeyboardButton(text="2️⃣"), KeyboardButton(text="3️⃣")],
            [KeyboardButton(text="4️⃣"), KeyboardButton(text="5️⃣")],
            [KeyboardButton(text="↩ В меню")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await message.answer("Выберите количество подходов:", reply_markup=kb)
    await state.set_state(AddApproachStates.waiting_for_sets)

# ===== Добавление нового упражнения =====
@dp.message(AddApproachStates.waiting_for_new_exercise)
async def add_new_exercise(message: types.Message, state: FSMContext):
    text = message.text.strip()
    user_id = message.from_user.id
    if text == "↩ В меню":
        await start(message, state)
        return
    await add_exercise(user_id, text)
    await state.update_data(exercise=text)

    # Переходим к выбору подходов
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="1️⃣"), KeyboardButton(text="2️⃣"), KeyboardButton(text="3️⃣")],
            [KeyboardButton(text="4️⃣"), KeyboardButton(text="5️⃣")],
            [KeyboardButton(text="↩ В меню")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await message.answer(f"✅ Упражнение '{text}' добавлено!\nВыберите количество подходов:", reply_markup=kb)
    await state.set_state(AddApproachStates.waiting_for_sets)

# ===== Выбор количества подходов =====
@dp.message(AddApproachStates.waiting_for_sets)
async def process_sets(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if text == "↩ В меню":
        await start(message, state)
        return

    try:
        sets = int(text[0])  # Получаем цифру из "1️⃣", "2️⃣" и т.д.
    except ValueError:
        await message.answer("❗ Пожалуйста, выберите количество подходов с кнопок.")
        return

    await state.update_data(sets=sets)

    # Попросим ввести повторения через пробел
    await message.answer(f"Введите количество повторений для каждого из {sets} подходов через пробел (например: 10 10 12):")
    await state.set_state(AddApproachStates.waiting_for_reps)

# ===== Ввод повторений =====
@dp.message(AddApproachStates.waiting_for_reps)
async def process_reps(message: types.Message, state: FSMContext):



# ===== История =====
 @dp.message(lambda m: m.text == "📜 История")
 async def history(message: types.Message):
    user_id = message.from_user.id
    records = await get_user_records(user_id)
    if not records:
        await message.answer("У вас пока нет записей.", reply_markup=main_kb())
        return
    msg_text = "📊 Ваши последние тренировки:\n\n"
    for r in records[:10]:
        approaches = int(r['approach'])
        reps_list = r['reps'].split()
        weights_list = r['weight'].split()
        msg_text += f"{r['date']} — {r['exercise']}:\n"
        for i in range(approaches):
            rep = reps_list[i] if i < len(reps_list) else reps_list[-1]
            weight = weights_list[i] if i < len(weights_list) else weights_list[-1]
            msg_text += f"{i+1}️⃣ {rep} повторений — {weight} кг\n"
        msg_text += "—" * 20 + "\n"
    await message.answer(msg_text, reply_markup=main_kb())

# ===== Рестарт =====
@dp.message(lambda m: m.text == "🔄 Рестарт бота")
async def restart_bot(message: types.Message):
    await message.answer("Бот перезапущен!")
    await start(message)

# ===== Запуск бота =====
async def main():
    await create_pool()
    try:
        await dp.start_polling(bot)
    finally:
        await close_pool()

if __name__ == "__main__":
    asyncio.run(main())


