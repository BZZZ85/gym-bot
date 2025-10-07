import asyncio
from datetime import datetime
from io import BytesIO

import matplotlib.pyplot as plt
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, FSInputFile

# ===== База данных (заглушки, замените своими функциями) =====
from db import create_pool, add_user, get_exercises, add_exercise, add_record, get_user_records, get_reminders

API_TOKEN = "8442431194:AAHqrL5Uv-boQHXf_68f6or3i1pZmJDMqy0"

# ===== FSM =====
class AddApproachStates(StatesGroup):
    waiting_for_exercise = State()
    waiting_for_new_exercise = State()
    waiting_for_sets = State()
    waiting_for_reps = State()
    waiting_for_weights = State()

# ===== Бот и диспетчер =====
storage = MemoryStorage()
bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=storage)

# ===== Главное меню =====
def main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("📜 История"), KeyboardButton("📈 Прогресс"), KeyboardButton("📊 Статистика")],
            [KeyboardButton("➕ Добавить подход"), KeyboardButton("⏮ Использовать прошлую тренировку")],
            [KeyboardButton("🔍 Найти упражнение"), KeyboardButton("❌ Удалить упражнение")],
            [KeyboardButton("⏰ Напоминания"), KeyboardButton("🔄 Рестарт бота")]
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
@dp.message(F.text == "➕ Добавить подход")
async def start_add_approach(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    user_exercises = await get_exercises(user_id)
    kb_buttons = [[KeyboardButton(ex)] for ex in user_exercises] + [
        [KeyboardButton("➕ Добавить новое упражнение")],
        [KeyboardButton("↩ В меню")]
    ]
    kb = ReplyKeyboardMarkup(keyboard=kb_buttons, resize_keyboard=True)
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

@dp.message(AddApproachStates.waiting_for_sets)
async def process_sets(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("Введите число подходов (например, 3):")
        return
    await state.update_data(sets=int(message.text))
    await message.answer("Введите количество повторений через пробел для каждого подхода (например, 10 12 10):")
    await state.set_state(AddApproachStates.waiting_for_reps)

@dp.message(AddApproachStates.waiting_for_reps)
async def process_reps(message: types.Message, state: FSMContext):
    reps_list = message.text.strip().split()
    if not all(rep.isdigit() for rep in reps_list):
        await message.answer("Введите числа через пробел (например, 10 12 10):")
        return
    await state.update_data(reps=reps_list)
    await message.answer("Введите вес через пробел для каждого подхода (например, 20 25 20):")
    await state.set_state(AddApproachStates.waiting_for_weights)

@dp.message(AddApproachStates.waiting_for_weights)
async def process_weights(message: types.Message, state: FSMContext):
    weight_list = message.text.strip().split()
    if not all(w.isdigit() for w in weight_list):
        await message.answer("Введите числа через пробел (например, 20 25 20):")
        return
    data = await state.get_data()
    exercise = data['exercise']
    sets = data['sets']
    reps = data['reps']
    await add_record(
        user_id=message.from_user.id,
        exercise=exercise,
        approach=sets,
        reps=' '.join(reps),
        weight=' '.join(weight_list),
        date=datetime.now().strftime("%Y-%m-%d %H:%M")
    )
    await message.answer(f"✅ Подходы для '{exercise}' добавлены!", reply_markup=main_kb())
    await state.clear()

# ===== История =====
@dp.message(F.text == "📜 История")
async def history(message: types.Message, state: FSMContext):
    await state.clear()
    records = await get_user_records(message.from_user.id)
    if not records:
        await message.answer("У вас пока нет записей.", reply_markup=main_kb())
        return
    msg = "📊 Ваши последние тренировки:\n\n"
    for r in records[:10]:
        approaches = int(r['approach'])
        reps_list = r['reps'].split()
        weights_list = r['weight'].split()
        msg += f"{r['date']} — {r['exercise']}:\n"
        for i in range(approaches):
            rep = reps_list[i] if i < len(reps_list) else reps_list[-1]
            weight = weights_list[i] if i < len(weights_list) else weights_list[-1]
            msg += f"{i+1} подход — {rep} повторений × {weight} кг\n"
        msg += "—" * 20 + "\n"
    await message.answer(msg, reply_markup=main_kb())

# ===== Прогресс =====
@dp.message(F.text == "📈 Прогресс")
async def progress(message: types.Message):
    records = await get_user_records(message.from_user.id)
    if not records:
        await message.answer("Нет данных для графика.", reply_markup=main_kb())
        return
    dates = [r['date'] for r in records[-10:]]
    scores = [sum(int(r['approach']) for r in records[-10:])]  # пример суммы подходов
    plt.figure(figsize=(6, 4))
    plt.plot(dates, scores, marker='o')
    plt.title("Прогресс по упражнениям")
    plt.xlabel("Дата")
    plt.ylabel("Подходы")
    plt.grid(True)
    buf = BytesIO()
    plt.savefig(buf, format='png')
    buf.seek(0)
    plt.close()
    photo = FSInputFile(buf, filename="progress.png")
    try:
        await bot.send_photo(message.chat.id, photo=photo, caption="📈 Ваш прогресс")
    except Exception:
        await message.answer("❗ Не удалось отправить график.", reply_markup=main_kb())

# ===== Статистика =====
@dp.message(F.text == "📊 Статистика")
async def statistics(message: types.Message):
    records = await get_user_records(message.from_user.id)
    if not records:
        await message.answer("У вас пока нет записей.", reply_markup=main_kb())
        return
    stats = {}
    for r in records:
        stats[r['exercise']] = stats.get(r['exercise'], 0) + int(r['approach'])
    msg = "📊 Статистика:\n"
    for ex, cnt in stats.items():
        msg += f"{ex}: {cnt} подходов\n"
    await message.answer(msg, reply_markup=main_kb())

# ===== Напоминания =====
@dp.message(F.text == "⏰ Напоминания")
async def reminders(message: types.Message):
    reminders_list = await get_reminders(message.from_user.id)
    if not reminders_list:
        await message.answer("У вас пока нет активных напоминаний.", reply_markup=main_kb())
        return
    msg = "⏰ Ваши напоминания:\n"
    for r in reminders_list:
        msg += f"{r['days']} в {r['time']}\n"
    await message.answer(msg, reply_markup=main_kb())

# ===== Рестарт =====
@dp.message(F.text == "🔄 Рестарт бота")
async def restart_bot(message: types.Message):
    await start(message)

# ===== Запуск =====
async def main():
    await create_pool()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
