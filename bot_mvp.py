from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.types import Message, KeyboardButton, ReplyKeyboardMarkup
import asyncio
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import matplotlib.pyplot as plt
from io import BytesIO
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import re
from aiogram import F
from db import create_pool, add_record, get_user_exercises, get_user_stats, delete_user_exercise, get_user_history


# ===== Настройки =====
API_TOKEN = "8442431194:AAHqrL5Uv-boQHXf_68f6or3i1pZmJDMqy0"
DATABASE_URL=psql 'postgresql://neondb_owner:npg_0eRPsTi9tJAj@ep-winter-snow-ab9o1qut-pooler.eu-west-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require'


# ===== FSM =====
storage = MemoryStorage()
bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=storage)
scheduler = AsyncIOScheduler()

class ProgressStates(StatesGroup):
    waiting_for_exercise = State()

class AddApproachStates(StatesGroup):
    waiting_for_exercise = State()
    waiting_for_new_exercise = State()
    waiting_for_sets = State()
    waiting_for_reps = State()
    waiting_for_weights = State()

class SearchStates(StatesGroup):
    waiting_for_exercise = State()

class ReminderStates(StatesGroup):
    waiting_for_reminder = State()
class DeleteExerciseStates(StatesGroup):
    waiting_for_exercise = State()




@dp.message(F.text == "↩ В меню")
async def back_to_main(message: Message, state: FSMContext):
    await state.clear()  # очищаем текущее состояние FSM
    await start(message, state)  # показываем главное меню

# ===== Главное меню =====
main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📜 История"), KeyboardButton(text="📈 Прогресс"), KeyboardButton(text="📊 Статистика")],
        [KeyboardButton(text="➕ Добавить подход"), KeyboardButton(text="⏮ Использовать прошлую тренировку")],
        [KeyboardButton(text="🔍 Найти упражнение"), KeyboardButton(text="❌ Удалить упражнение")],
        [KeyboardButton(text="⏰ Напоминания"), KeyboardButton(text="🔄 Рестарт бота")]
    ],
    resize_keyboard=True
)

# ===== НАПОМИНАНИЯ: работа с Google Sheets =====
def load_reminders():
    try:
        reminders_sheet = client.open(SPREADSHEET_NAME).worksheet("Reminders")
        return reminders_sheet.get_all_records()
    except gspread.WorksheetNotFound:
        reminders_sheet = client.open(SPREADSHEET_NAME).add_worksheet(title="Reminders", rows="100", cols="3")
        reminders_sheet.append_row(["UserID", "Days", "Time"])
        return []

def add_reminder(user_id, days, time):
    reminders_sheet = client.open(SPREADSHEET_NAME).worksheet("Reminders")
    reminders_sheet.append_row([user_id, days, time])

def remove_reminders(user_id):
    reminders_sheet = client.open(SPREADSHEET_NAME).worksheet("Reminders")
    all_rows = reminders_sheet.get_all_values()
    new_rows = [row for row in all_rows if not row or row[0] != str(user_id)]
    reminders_sheet.clear()
    reminders_sheet.append_row(["UserID", "Days", "Time"])
    for row in new_rows[1:]:
        reminders_sheet.append_row(row)

async def send_reminder(user_id, text="⏰ Напоминание: пора тренироваться!"):
    try:
        await bot.send_message(user_id, text)
    except:
        pass

def schedule_all_reminders():
    scheduler.remove_all_jobs()
    reminders = load_reminders()
    days_map = {"пн": "mon", "вт": "tue", "ср": "wed", "чт": "thu",
                "пт": "fri", "сб": "sat", "вс": "sun"}
    for r in reminders:
        user_id = str(r["UserID"])
        time_str = r["Time"]
        day_str = r["Days"]

        match = re.match(r"(\d{1,2}):(\d{2})", time_str)
        if not match:
            continue
        hour, minute = int(match.group(1)), int(match.group(2))
        day_list = [days_map[d.strip().lower()] for d in day_str.split(",") if d.strip().lower() in days_map]

        for d in day_list:
            scheduler.add_job(send_reminder, "cron", day_of_week=d, hour=hour, minute=minute, args=[user_id])

# ===== Меню напоминаний =====
@dp.message(lambda m: m.text == "⏰ Напоминания")
async def reminders_menu(message: Message):
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Добавить напоминание")],
            [KeyboardButton(text="📜 Мои напоминания")],
            [KeyboardButton(text="❌ Удалить все напоминания")],
            [KeyboardButton(text="↩ В меню")]
        ],
        resize_keyboard=True
    )
    await message.answer("Меню напоминаний:", reply_markup=kb)

@dp.message(lambda m: m.text == "➕ Добавить напоминание")
async def add_reminder_start(message: Message, state: FSMContext):
    await message.answer("Введите дни недели и время в формате:\nПн, Ср, Пт 19:00")
    await state.set_state(ReminderStates.waiting_for_reminder)

@dp.message(ReminderStates.waiting_for_reminder)
async def save_reminder(message: Message, state: FSMContext):
    if message.text.strip() == "↩ В меню":
        await start(message, state)
        return
    user_id = str(message.from_user.id)
    text = message.text.strip()
    try:
        parts = text.split()
        if len(parts) < 2:
            raise ValueError("Неверный формат")
        days = " ".join(parts[:-1])
        time = parts[-1]
        if not re.match(r"^\d{1,2}:\d{2}$", time):
            raise ValueError("Неверный формат времени")
        add_reminder(user_id, days, time)
        schedule_all_reminders()
        await message.answer(f"✅ Напоминание сохранено: {days} в {time}", reply_markup=main_kb)
    except:
        await message.answer("❌ Ошибка. Используй формат: Пн, Ср, Пт 19:00")
    await state.clear()

@dp.message(lambda m: m.text == "📜 Мои напоминания")
async def my_reminders(message: Message):
    user_id = str(message.from_user.id)
    reminders = load_reminders()
    user_reminders = [f"{r['Days']} в {r['Time']}" for r in reminders if str(r['UserID']) == user_id]
    if not user_reminders:
        await message.answer("У тебя пока нет напоминаний.", reply_markup=main_kb)
    else:
        msg = "⏰ Твои напоминания:\n" + "\n".join(user_reminders)
        await message.answer(msg, reply_markup=main_kb)

@dp.message(lambda m: m.text == "❌ Удалить все напоминания")
async def delete_all_reminders(message: Message):
    user_id = str(message.from_user.id)
    remove_reminders(user_id)
    schedule_all_reminders()
    await message.answer("❌ Все твои напоминания удалены.", reply_markup=main_kb)

# ===== Работа с упражнениями =====
def get_exercises():
    try:
        exercises_sheet = client.open(SPREADSHEET_NAME).worksheet("Exercises")
        return [x.strip() for x in exercises_sheet.col_values(1) if x.strip()]
    except gspread.WorksheetNotFound:
        exercises_sheet = client.open(SPREADSHEET_NAME).add_worksheet(title="Exercises", rows="100", cols="1")
        default = ["Жим лёжа", "Приседания", "Становая тяга"]
        for ex in default:
            exercises_sheet.append_row([ex])
        return default

def add_exercise_to_sheet(ex_name):
    exercises_sheet = client.open(SPREADSHEET_NAME).worksheet("Exercises")
    exercises_sheet.append_row([ex_name])

def user_exercises_keyboard(user_id, with_custom=False, for_delete=False):
    records = get_all_records()
    user_records = [r for r in records if r['userid'] == str(user_id)]
    exercises_set = sorted({r['exercise'] for r in user_records if r['exercise']})

    kb_buttons = [[KeyboardButton(text=ex)] for ex in exercises_set]

    if with_custom:
        kb_buttons.append([KeyboardButton(text="➕ Добавить новое упражнение")])
    if for_delete:
        kb_buttons.append([KeyboardButton(text="❌ Удалить упражнение")])

    # Кнопка возврата в меню
    kb_buttons.append([KeyboardButton(text="↩ В меню")])

    return ReplyKeyboardMarkup(
        keyboard=kb_buttons,
        resize_keyboard=True,
        one_time_keyboard=True
    )

@dp.message(DeleteExerciseStates.waiting_for_exercise)
async def process_delete_exercise(message: Message, state: FSMContext):
    exercise_to_delete = message.text.strip()
    user_id = str(message.from_user.id)

    if exercise_to_delete == "↩ В меню":
        await start(message, state)
        return

    records = sheet.get_all_records()
    rows_to_keep = []

    for i, r in enumerate(records, start=2):  # start=2 потому что в Google Sheets 1-я строка – заголовки
        if not (str(r.get("UserID")) == user_id and r.get("Упражнение") == exercise_to_delete):
            rows_to_keep.append([r.get("UserID"), r.get("Username"), r.get("Дата"),
                                 r.get("Упражнение"), r.get("Подходы"), r.get("Повторения"),
                                 r.get("Вес"), r.get("Объём")])

    # очищаем лист и вставляем оставшиеся строки
    sheet.clear()
    sheet.append_row(["UserID", "Username", "Дата", "Упражнение", "Подходы", "Повторения", "Вес", "Объём"])
    for row in rows_to_keep:
        sheet.append_row(row)

    await message.answer(f"✅ Упражнение '{exercise_to_delete}' удалено!", reply_markup=main_kb)
    await state.clear()


@dp.message(lambda m: m.text == "❌ Удалить упражнение")
async def delete_exercise_menu(message: Message, state: FSMContext):
    user_id = str(message.from_user.id)
    kb = user_exercises_delete_keyboard(user_id)
    if len(kb.keyboard) == 1:  # только кнопка "↩ В меню"
        await message.answer("У вас нет упражнений для удаления.", reply_markup=main_kb)
        return
    await message.answer("Выберите упражнение, которое хотите удалить:", reply_markup=kb)
    # Здесь исправляем:
    await state.set_state(DeleteExerciseStates.waiting_for_exercise)
def user_exercises_delete_keyboard(user_id):
    records = get_all_records()
    user_records = [r for r in records if r['userid'] == str(user_id)]
    exercises_set = set(r['exercise'] for r in user_records if r['exercise'])

    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=ex)] for ex in exercises_set] +
                 [[KeyboardButton(text="↩ В меню")]],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    return kb


@dp.message(DeleteExerciseStates.waiting_for_exercise)
async def delete_exercise(message: Message, state: FSMContext):
    exercise_to_delete = message.text.strip()
    if exercise_to_delete == "↩ В меню":
        await start(message, state)
        return
    
    user_id = str(message.from_user.id)
    all_rows = sheet.get_all_records()
    
    # Найти строки, которые нужно оставить (без выбранного упражнения)
    new_rows = [
        [r['UserID'], r['Username'], r['Дата'], r['Упражнение'], r['Подходы'], r['Повторения'], r['Вес'], r['Объём']]
        for r in all_rows
        if not (r['UserID'] == user_id and r['Упражнение'] == exercise_to_delete)
    ]

    # Очистить лист и записать обратно
    sheet.clear()
    sheet.append_row(["UserID", "Username", "Дата", "Упражнение", "Подходы", "Повторения", "Вес", "Объём"])
    for row in new_rows:
        sheet.append_row(row)
    
    await message.answer(f"✅ Упражнение '{exercise_to_delete}' удалено.", reply_markup=main_kb)
    await state.clear()


# ===== Утилиты =====
def normalize(s: str):
    return str(s or "").strip().lower().replace("ё", "е")

def get_all_records():
    all_records = sheet.get_all_records()
    parsed = []
    for r in all_records:
        parsed.append({
            'userid': str(r.get('UserID','')).strip(),
            'username': str(r.get('Username','')).strip(),
            'date': str(r.get('Дата','')).strip(),
            'exercise': str(r.get('Упражнение','')).strip(),
            'approach': str(r.get('Подходы','')).strip(),
            'reps': str(r.get('Повторения','')).strip(),
            'weight': str(r.get('Вес','')).strip(),
            'volume': str(r.get('Объём','')).strip()
        })
    return parsed

# ===== /start =====
@dp.message(CommandStart())
async def start(message: Message, state: FSMContext = None):
    await message.answer("Привет! Бот для учёта тренировок.\n\nВыберите действие:", reply_markup=main_kb)
    if state:
        await state.clear()

# ===== Добавление подхода =====
@dp.message(lambda m: m.text == "➕ Добавить подход")
async def start_add_approach(message: types.Message, state: FSMContext):
    # Получаем индивидуальные упражнения пользователя
    user_records = [r for r in get_all_records() if r['userid'] == str(message.from_user.id)]
    user_exercises = {r['exercise'] for r in user_records if r['exercise']}
    
    # Создаём клавиатуру
    kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=ex)] for ex in user_exercises
    ] + [[KeyboardButton(text="➕ Добавить новое упражнение")],
         [KeyboardButton(text="↩ В меню")]],
    resize_keyboard=True,
    one_time_keyboard=True
)

    await message.answer("Выберите упражнение или добавьте новое:", reply_markup=kb)
    await state.set_state(AddApproachStates.waiting_for_exercise)

@dp.message(AddApproachStates.waiting_for_exercise)
async def process_exercise(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if text == "↩ В меню":
        await start(message, state)
        return
    
    # Получаем индивидуальные упражнения пользователя
    user_records = [r for r in get_all_records() if r['userid'] == str(message.from_user.id)]
    user_exercises = {r['exercise'] for r in user_records if r['exercise']}

    if text == "➕ Добавить новое упражнение":
        await message.answer("Введите название нового упражнения:")
        await state.set_state(AddApproachStates.waiting_for_new_exercise)
        return

    # Проверяем, что выбранное упражнение есть в индивидуальном списке
    if text not in user_exercises:
        await message.answer("❗ Выберите упражнение из списка или добавьте новое.")
        return

    await state.update_data(exercise=text)
    await message.answer("Сколько подходов?")
    await state.set_state(AddApproachStates.waiting_for_sets)

@dp.message(AddApproachStates.waiting_for_new_exercise)
async def add_new_exercise(message: types.Message, state: FSMContext):
    if message.text.strip() == "↩ В меню":
        await start(message, state)
        return
    
    new_ex = message.text.strip()
    # Проверяем глобально, если уже есть в таблице
    if new_ex in get_exercises():
        await message.answer("Это упражнение уже есть. Выберите другое.")
        await state.set_state(AddApproachStates.waiting_for_exercise)
        return

    # Добавляем новое упражнение в глобальный лист
    add_exercise_to_sheet(new_ex)

    # Сохраняем для текущей сессии пользователя
    await state.update_data(exercise=new_ex)
    await message.answer(f"✅ Упражнение '{new_ex}' добавлено!\nСколько подходов?")
    await state.set_state(AddApproachStates.waiting_for_sets)

# ===== История =====
@dp.message(lambda m: m.text.strip() == "📜 История")
async def history(message: Message):
    records = get_all_records()
    user_id = str(message.from_user.id)
    user_records = [r for r in records if r['userid'] == user_id]
    if not user_records:
        await message.answer("У вас пока нет записей.", reply_markup=main_kb)
        return
    user_records.sort(key=lambda x: x['date'], reverse=True)
    last_records = user_records[:10]
    msg_text = "📊 Ваши последние тренировки:\n\n"
    for r in last_records:
        approaches = int(r['approach'])
        reps_list = r['reps'].split()
        weights_list = r['weight'].split()
        msg_text += f"{r['date']} — {r['exercise']}:\n"
        for i in range(approaches):
            rep = reps_list[i] if i < len(reps_list) else reps_list[-1]
            weight = weights_list[i] if i < len(weights_list) else weights_list[-1]
            msg_text += f"{i+1}️⃣ {rep} повторений — {weight} кг\n"
        msg_text += "—" * 20 + "\n"
    await message.answer(msg_text, reply_markup=main_kb)

# ===== Прогресс =====
@dp.message(lambda m: m.text == "📈 Прогресс")
async def progress(message: Message, state: FSMContext):
    kb = user_exercises_keyboard(message.from_user.id, with_custom=False)
    if not kb.keyboard[:-1]:
        await message.answer("У вас пока нет записей для построения графика.", reply_markup=main_kb)
        return
    await message.answer("Выберите упражнение для графика:", reply_markup=kb)
    await state.set_state(ProgressStates.waiting_for_exercise)

@dp.message(ProgressStates.waiting_for_exercise)
async def send_graph(message: Message, state: FSMContext):
    user_id = str(message.from_user.id)
    exercise_input = message.text.strip()
    if exercise_input == "↩ В меню":
        await start(message, state)
        return
    records = get_all_records()
    user_records = [r for r in records if r['userid'] == user_id]
    ex_records = [r for r in user_records if normalize(r.get("exercise","")) == normalize(exercise_input)]
    if not ex_records:
        await message.answer("Нет данных для этого упражнения.", reply_markup=main_kb)
        await state.clear()
        return
    dates, weights = [], []
    for r in ex_records:
        w = r.get("weight","").strip()
        d = r.get("date","").strip()
        if w and d:
            try:
                weights_list = [float(x) for x in w.split()]
                weights.append(sum(weights_list)/len(weights_list))
                dates.append(d)
            except:
                continue
    if not weights:
        await message.answer("Нет корректных данных для построения графика.", reply_markup=main_kb)
        await state.clear()
        return
    plt.figure(figsize=(8,4))
    plt.plot(dates, weights, marker="o")
    plt.title(f"Прогресс по {exercise_input}")
    plt.xlabel("Дата")
    plt.ylabel("Средний вес (кг)")
    plt.grid(True)
    plt.xticks(rotation=45)
    plt.tight_layout()
    buf = BytesIO()
    plt.savefig(buf, format="png")
    buf.seek(0)
    plt.close()
    file = types.BufferedInputFile(buf.read(), filename="progress.png")
    await message.answer_photo(photo=file, reply_markup=main_kb)
    await state.clear()

# ===== Статистика =====
@dp.message(lambda m: m.text == "📊 Статистика")
async def stats_choose(message: Message, state: FSMContext):
    kb = user_exercises_keyboard(message.from_user.id, with_custom=False)
    
    # вместо kb.add(...) делаем так:
    buttons = kb.keyboard  # существующие кнопки
    buttons.append([KeyboardButton(text="Все упражнения")])
    
    kb = ReplyKeyboardMarkup(
        keyboard=buttons,
        resize_keyboard=True,
        one_time_keyboard=True
    )
    
    await message.answer("Выберите упражнение для статистики:", reply_markup=kb)
    await state.set_state(SearchStates.waiting_for_exercise)


@dp.message(SearchStates.waiting_for_exercise)
async def stats_exercise(message: Message, state: FSMContext):
    exercise_input = message.text.strip()
    if exercise_input == "↩ В меню":
        await start(message, state)
        return
    user_id = str(message.from_user.id)
    records = get_all_records()
    user_records = [r for r in records if r['userid'] == user_id]
    if not user_records:
        await message.answer("❗ У вас пока нет записей.", reply_markup=main_kb)
        await state.clear()
        return
    msg_parts = []
    if exercise_input == "Все упражнения":
        grouped = {}
        for r in user_records:
            ex = r.get("exercise","").strip()
            key = normalize(ex)
            if key not in grouped:
                grouped[key] = {"name": ex, "weights": [], "volumes": []}
            if r['weight']:
                grouped[key]["weights"].extend([float(x) for x in r['weight'].split()])
            if r['volume']:
                grouped[key]["volumes"].append(float(r['volume']))
        for g in grouped.values():
            msg = f"🏋️ {g['name']}:\n"
            if g['weights']:
                msg += f"   Средний вес: {sum(g['weights'])/len(g['weights']):.1f} кг\n"
                msg += f"   Личный рекорд: {max(g['weights']):.1f} кг\n"
            if g['volumes']:
                msg += f"   Средний объём: {sum(g['volumes'])/len(g['volumes']):.1f} кг\n"
                msg += f"   Рекордный объём: {max(g['volumes']):.1f} кг\n"
            msg += "—"*20 + "\n"
            msg_parts.append(msg)
    else:
        ex_records = [r for r in user_records if normalize(r['exercise']) == normalize(exercise_input)]
        if not ex_records:
            await message.answer(f"Записей по '{exercise_input}' не найдено.", reply_markup=main_kb)
            await state.clear()
            return
        weights, volumes = [], []
        for r in ex_records:
            if r['weight']:
                weights.extend([float(x) for x in r['weight'].split()])
            if r['volume']:
                volumes.append(float(r['volume']))
        msg = f"📊 Статистика по {exercise_input}:\n"
        if weights:
            msg += f"   Средний вес: {sum(weights)/len(weights):.1f} кг\n"
            msg += f"   Личный рекорд: {max(weights):.1f} кг\n"
        if volumes:
            msg += f"   Средний объём: {sum(volumes)/len(volumes):.1f} кг\n"
            msg += f"   Рекордный объём: {max(volumes):.1f} кг\n"
        msg_parts.append(msg)
    for part in msg_parts:
        await message.answer(part)
    await message.answer("Возврат в меню:", reply_markup=main_kb)
    await state.clear()
# ===== Рестарт =====
@dp.message(lambda m: m.text == "🔄 Рестарт бота")
async def restart_bot(message: Message):
    await message.answer("Бот перезапущен!")
    await start(message)

# ===== Последняя тренировка =====
@dp.message(lambda m: m.text == "⏮ Использовать прошлую тренировку")
async def use_last(message: Message, state: FSMContext):
    records = get_all_records()
    user_id = str(message.from_user.id)
    user_records = [r for r in records if r['userid']==user_id]
    if not user_records:
        await message.answer("У вас нет прошлых тренировок.", reply_markup=main_kb)
        return
    user_records.sort(key=lambda x: x['date'], reverse=True)
    last = user_records[0]
    await message.answer(
        f"Последняя тренировка: {last['exercise']}, "
        f"подходы {last['approach']}, "
        f"повторы {last['reps']}, "
        f"вес {last['weight']}",
        reply_markup=main_kb
    )

# ===== Поиск упражнений =====
@dp.message(lambda m: m.text == "🔍 Найти упражнение")
async def find_exercise(message: Message, state: FSMContext):
    kb = user_exercises_keyboard(message.from_user.id, with_custom=False)
    await message.answer(
        "Выберите упражнение для просмотра статистики и прогресса или напишите его вручную:",
        reply_markup=kb
    )
    await state.set_state(SearchStates.waiting_for_exercise)

@dp.message(SearchStates.waiting_for_exercise)
async def search_exercise(message: Message, state: FSMContext):
    exercise_input = message.text.strip()
    if exercise_input == "↩ В меню":
        await start(message, state)
        return

    user_id = str(message.from_user.id)
    records = get_all_records()
    user_records = [r for r in records if r['userid'] == user_id]

    if not user_records:
        await message.answer("❗ У вас пока нет записей.", reply_markup=main_kb)
        await state.clear()
        return

    ex_records = [r for r in user_records if normalize(r['exercise']) == normalize(exercise_input)]
    if not ex_records:
        await message.answer(f"Записей по '{exercise_input}' не найдено.", reply_markup=main_kb)
        await state.clear()
        return

    # ===== Статистика =====
    weights, volumes = [], []
    for r in ex_records:
        if r['weight']:
            weights.extend([float(x) for x in r['weight'].split()])
        if r['volume']:
            volumes.append(float(r['volume']))

    stats_text = f"📊 Статистика по {exercise_input}:\n"
    if weights:
        stats_text += f"   Средний вес: {sum(weights)/len(weights):.1f} кг\n"
        stats_text += f"   Личный рекорд: {max(weights):.1f} кг\n"
    if volumes:
        stats_text += f"   Средний объём: {sum(volumes)/len(volumes):.1f} кг\n"
        stats_text += f"   Рекордный объём: {max(volumes):.1f} кг\n"

    await message.answer(stats_text)

    # ===== График прогресса =====
    dates, avg_weights = [], []
    for r in ex_records:
        w = r.get("weight","").strip()
        d = r.get("date","").strip()
        if w and d:
            try:
                weights_list = [float(x) for x in w.split()]
                avg_weights.append(sum(weights_list)/len(weights_list))
                dates.append(d)
            except:
                continue

    if avg_weights:
        plt.figure(figsize=(8,4))
        plt.plot(dates, avg_weights, marker="o")
        plt.title(f"Прогресс по {exercise_input}")
        plt.xlabel("Дата")
        plt.ylabel("Средний вес (кг)")
        plt.grid(True)
        plt.xticks(rotation=45)
        plt.tight_layout()
        buf = BytesIO()
        plt.savefig(buf, format="png")
        buf.seek(0)
        plt.close()
        file = types.BufferedInputFile(buf.read(), filename="progress.png")
        await message.answer_photo(photo=file)

    await message.answer("Возврат в меню:", reply_markup=main_kb)
    await state.clear()

# ===== Запуск бота =====
async def main():
    schedule_all_reminders()  # подгружаем напоминания из Google Sheets
    scheduler.start()          # стартуем планировщик
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
