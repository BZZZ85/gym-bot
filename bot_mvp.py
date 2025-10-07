import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, FSInputFile
from io import BytesIO
from datetime import datetime
import matplotlib.pyplot as plt
from aiogram.types import FSInputFile


from db import create_pool, add_user, get_exercises, add_exercise, get_user_records, get_reminders

API_TOKEN = "8442431194:AAHqrL5Uv-boQHXf_68f6or3i1pZmJDMqy0"

storage = MemoryStorage()
bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=storage)

# ===== FSM =====
class AddApproachStates(StatesGroup):
    waiting_for_exercise = State()
    waiting_for_new_exercise = State()
    waiting_for_sets = State()
    waiting_for_reps = State()
    waiting_for_weights = State()

# ===== Главное меню =====
def main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📜 История"), KeyboardButton(text="📈 Прогресс"), KeyboardButton(text="📊 Статистика")],
            [KeyboardButton(text="➕ Добавить подход"), KeyboardButton(text="⏮ Использовать прошлую тренировку")],
            [KeyboardButton(text="🔍 Найти упражнение"), KeyboardButton(text="❌ Удалить упражнение")],
            [KeyboardButton(text="⏰ Напоминания"), KeyboardButton(text="🔄 Рестарт бота")]
        ],
        resize_keyboard=True
    )

# ===== /start =====
@dp.message(CommandStart())
async def start(message: types.Message, state: FSMContext = None):
    await add_user(message.from_user.id, message.from_user.username)
    if state:
        await state.clear()
    await message.answer("Привет! Бот для учёта тренировок.\n\nВыберите действие:", reply_markup=main_kb())

# ===== Добавление подхода =====
@dp.message(lambda m: m.text == "➕ Добавить подход")
async def start_add_approach(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    user_exercises = await get_exercises(user_id)
    kb_buttons = [[KeyboardButton(text=ex)] for ex in user_exercises] + [
        [KeyboardButton(text="➕ Добавить новое упражнение")],
        [KeyboardButton(text="↩ В меню")]
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
    user_id = message.from_user.id
    user_exercises = await get_exercises(user_id)
    if text == "➕ Добавить новое упражнение":
        await message.answer("Введите название нового упражнения:")
        await state.set_state(AddApproachStates.waiting_for_new_exercise)
        return
    if text not in user_exercises:
        await message.answer("❗ Выберите упражнение из списка или добавьте новое.")
        return
    await state.update_data(exercise=text)
    await message.answer("Сколько подходов?")
    await state.set_state(AddApproachStates.waiting_for_sets)

@dp.message(AddApproachStates.waiting_for_new_exercise)
async def add_new_exercise(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if text == "↩ В меню":
        await start(message, state)
        return
    user_id = message.from_user.id
    await add_exercise(user_id, text)
    await state.update_data(exercise=text)
    await message.answer(f"✅ Упражнение '{text}' добавлено!\nСколько подходов?")
    await state.set_state(AddApproachStates.waiting_for_sets)

# ===== История =====
@dp.message(lambda m: m.text == "📜 История")
async def history(message: types.Message, state: FSMContext):
    await state.clear()
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
            msg_text += f"{i+1} подход — {rep} повторений × {weight} кг\n"
        msg_text += "—" * 20 + "\n"
    await message.answer(msg_text, reply_markup=main_kb())

# ===== Прогресс =====
@dp.message(lambda m: m.text == "📈 Прогресс")
async def progress(message, bot: Bot):
    try:
        # Пример данных, обычно берется из БД
        user_data = [
            {"date": datetime(2025, 10, 1), "score": 5},
            {"date": datetime(2025, 10, 2), "score": 7},
            {"date": datetime(2025, 10, 3), "score": 6},
        ]

        dates = []
        scores = []

        for r in user_data:
            # Если date уже datetime, используем его
            date = r['date'] if isinstance(r['date'], datetime) else datetime.strptime(r['date'], "%Y-%m-%d")
            dates.append(date.strftime("%d.%m"))
            scores.append(r['score'])

        # Рисуем график
        plt.figure(figsize=(6, 4))
        plt.plot(dates, scores, marker='o')
        plt.title("Прогресс по упражнениям")
        plt.xlabel("Дата")
        plt.ylabel("Баллы")
        plt.grid(True)

        # Сохраняем график в BytesIO
        buf = BytesIO()
        plt.savefig(buf, format='png')
        buf.seek(0)
        plt.close()

        # Создаем FSInputFile
        photo = FSInputFile(buf, filename="progress.png")

        # Отправляем с таймаутом и обработкой ошибок
        await bot.send_photo(
            chat_id=message.chat.id,
            photo=photo,
            caption="📈 Ваш прогресс по упражнениям",
            request_timeout=60
        )

    except Exception as e:
        # Ловим любые ошибки (сеть, файл и др.)
        await message.answer(f"❗ Не удалось отправить график: {e}")
# ===== Статистика =====
@dp.message(lambda m: m.text == "📊 Статистика")
async def statistics(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    records = await get_user_records(user_id)
    if not records:
        await message.answer("У вас пока нет записей.", reply_markup=main_kb())
        return
    exercises = {}
    for r in records:
        exercises[r['exercise']] = exercises.get(r['exercise'], 0) + int(r['approach'])
    msg = "📊 Статистика по упражнениям:\n"
    for ex, cnt in exercises.items():
        msg += f"{ex}: {cnt} подходов\n"
    await message.answer(msg, reply_markup=main_kb())

# ===== Напоминания =====
@dp.message(lambda m: m.text == "⏰ Напоминания")
async def reminders(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    reminders_list = await get_reminders(user_id)
    if not reminders_list:
        await message.answer("У вас пока нет активных напоминаний.", reply_markup=main_kb())
        return
    msg = "⏰ Ваши напоминания:\n"
    for r in reminders_list:
        msg += f"{r['days']} в {r['time']}\n"
    await message.answer(msg, reply_markup=main_kb())

# ===== Рестарт =====
@dp.message(lambda m: m.text == "🔄 Рестарт бота")
async def restart_bot(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Бот перезапущен!")
    await start(message)

# ===== Запуск бота =====
async def main():
    await create_pool()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
