from aiogram.client.default import DefaultBotProperties
import asyncio
import csv
import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, date, time
from typing import Dict, Any, List

import pytz
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton
)

TOKEN = os.getenv("TOKEN", "")
ADMIN_ID = 1026494049
SUMMER_CODE = "летл2025"
SUMMER_TEST_LINK = "https://example.com/test"

GUIDES = [
    {"title": "1) Основы и этика", "url": "https://example.com/guide1"},
    {"title": "2) Техническая часть", "url": "https://example.com/guide2"},
    {"title": "3) Предмет", "url": "https://example.com/guide3"},
    {"title": "4) Основные проблемы", "url": "https://example.com/guide4"},
]

TASKS_COMMON = {
    1: {"title": "Задание к гайду 1", "text": "..."},
    2: {"title": "Задание к гайду 2", "text": "..."},
    4: {"title": "Задание к гайду 4", "text": "..."}
}
TASKS_SUBJECT = {
    "Химия": {"title": "Химия — задание к гайду 3", "text": "..."},
    "Биология": {"title": "Биология — задание к гайду 3", "text": "..."},
    "Обществознание": {"title": "Обществознание — задание к гайду 3", "text": "..."},
    "Русский язык": {"title": "Русский — задание к гайду 3", "text": "..."},
    "Информатика": {"title": "Информатика — задание к гайду 3", "text": "..."},
    "Профильная математика": {"title": "Математика — задание к гайду 3", "text": "..."},
    "Физика": {"title": "Физика — задание к гайду 3", "text": "..."},
}

SEND_HOUR, SEND_MINUTE = 8, 0
REMIND_14_HOUR, REMIND_14_MINUTE = 14, 0
REMIND_HOUR, REMIND_MINUTE = 21, 0
SUMMER_REMIND_HOUR, SUMMER_REMIND_MINUTE = 20, 0
TZ_MOSCOW = pytz.timezone("Europe/Moscow")

DATA_DIR = "data"
USERS_FILE = os.path.join(DATA_DIR, "users.json")
EXPORT_CSV = os.path.join(DATA_DIR, "export.csv")
os.makedirs(DATA_DIR, exist_ok=True)

SUBJECTS = [
    "Химия", "Биология", "Обществознание",
    "Русский язык", "Информатика", "Профильная математика", "Физика"
]

@dataclass
class Progress:
    role: str = ""
    fio: str = ""
    subject: str = ""
    current_day: int = 0
    guide_sent_dates: List[str] = None
    guide_read_dates: List[str] = None
    task_given_dates: List[str] = None
    task_done_dates: List[str] = None
    awaiting_read_confirm: bool = False
    last_guide_sent_date: str = ""
    summer_assigned_at: str = ""
    summer_deadline: str = ""
    summer_reminded: bool = False
    last_update: str = ""

    def __post_init__(self):
        self.guide_sent_dates = self.guide_sent_dates or []
        self.guide_read_dates = self.guide_read_dates or []
        self.task_given_dates = self.task_given_dates or []
        self.task_done_dates = self.task_done_dates or []
        if not self.last_update:
            self.last_update = datetime.now(TZ_MOSCOW).isoformat()

def load_users():
    if not os.path.exists(USERS_FILE):
        return {}
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_users(db):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

def get_user(db, user_id):
    uid = str(user_id)
    if uid not in db:
        db[uid] = asdict(Progress())
    return Progress(**db[uid])

def upsert_user(db, user_id, p):
    db[str(user_id)] = asdict(p)
    save_users(db)

class RegStates(StatesGroup):
    waiting_role = State()
    waiting_summer_code = State()
    waiting_fio = State()
    waiting_subject = State()

def main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📖 Я прочитал гайд"), KeyboardButton(text="✅ Я выполнил задание")],
            [KeyboardButton(text="📊 Мой прогресс")]
        ], resize_keyboard=True
    )

def subjects_kb():
    rows = []
    row = []
    for s in SUBJECTS:
        row.append(KeyboardButton(text=s))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)

def today_str():
    return datetime.now(TZ_MOSCOW).date().isoformat()

def is_today(dates):
    return today_str() in (dates or [])

def next_run_delay_sec(hour, minute):
    now = datetime.now(TZ_MOSCOW)
    run_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if run_at <= now:
        run_at += timedelta(days=1)
    return (run_at - now).total_seconds()

async def send_guide_if_due(bot, uid, db):
    p = get_user(db, uid)
    if p.role != "novice" or p.current_day >= len(GUIDES):
        return
    if is_today(p.guide_sent_dates) or p.awaiting_read_confirm:
        return
    if is_today(p.task_given_dates) and not is_today(p.task_done_dates):
        return
    guide = GUIDES[p.current_day]
    await bot.send_message(uid, f"📖 Гайд на сегодня:\n<b>{guide['title']}</b>\n{guide['url']}", parse_mode=ParseMode.HTML)
    p.guide_sent_dates.append(today_str())
    p.last_guide_sent_date = today_str()
    p.awaiting_read_confirm = True
    upsert_user(db, uid, p)

async def catchup_after_reboot(bot):
    db = load_users()
    now = datetime.now(TZ_MOSCOW)
    if now.hour > SEND_HOUR or (now.hour == SEND_HOUR and now.minute >= SEND_MINUTE):
        for uid in db.keys():
            try:
                await send_guide_if_due(bot, int(uid), db)
            except:
                continue

async def daily_broadcast(bot):
    while True:
        await asyncio.sleep(next_run_delay_sec(SEND_HOUR, SEND_MINUTE))
        db = load_users()
        for uid in db.keys():
            await send_guide_if_due(bot, int(uid), db)

async def reminders(bot):
    while True:
        await asyncio.sleep(next_run_delay_sec(REMIND_14_HOUR, REMIND_14_MINUTE))
        db = load_users()
        for uid, raw in db.items():
            p = Progress(**raw)
            if p.role == "novice" and is_today(p.task_given_dates) and not is_today(p.task_done_dates):
                await bot.send_message(uid, "⏰ Напоминание: задание нужно сдать до 22:00, иначе оно пропадёт!")
        await asyncio.sleep(next_run_delay_sec(REMIND_HOUR, REMIND_MINUTE))
        db = load_users()
        for uid, raw in db.items():
            p = Progress(**raw)
            if p.role == "novice" and is_today(p.task_given_dates) and not is_today(p.task_done_dates):
                await bot.send_message(uid, "⏰ Осталось меньше часа до конца сдачи задания!")

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

@dp.message(CommandStart())
async def on_start(m: Message, state: FSMContext):
    db = load_users()
    p = get_user(db, m.from_user.id)
    upsert_user(db, m.from_user.id, p)
    await state.clear()
    await state.set_state(RegStates.waiting_role)
    await m.answer("Выбери, кто ты:", reply_markup=ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Я новичок"), KeyboardButton(text="Я летник")]], resize_keyboard=True
    ))

@dp.message(F.text.lower() == "я летник")
async def choose_summer(m: Message, state: FSMContext):
    await state.set_state(RegStates.waiting_summer_code)
    await m.answer("Введи код доступа:")

@dp.message(F.text.lower() == "я новичок")
async def choose_novice(m: Message, state: FSMContext):
    await state.set_state(RegStates.waiting_fio)
    await m.answer("Введи своё ФИО:")

@dp.message(RegStates.waiting_summer_code)
async def summer_check_code(m: Message, state: FSMContext):
    if m.text.strip().lower() != SUMMER_CODE.lower():
        await m.answer("❌ Неверный код, попробуй ещё раз:")
        return
    await state.update_data(role="summer")
    await state.set_state(RegStates.waiting_fio)
    await m.answer("Код верный. Введи своё ФИО:")

@dp.message(RegStates.waiting_fio)
async def reg_fio(m: Message, state: FSMContext):
    if len(m.text.strip()) < 2:
        await m.answer("Введи настоящее ФИО:")
        return
    await state.update_data(fio=m.text.strip())
    await state.set_state(RegStates.waiting_subject)
    await m.answer("Выбери предмет:", reply_markup=subjects_kb())

@dp.message(RegStates.waiting_subject)
async def reg_subject(m: Message, state: FSMContext):
    if m.text not in SUBJECTS:
        await m.answer("Выбери предмет кнопкой:", reply_markup=subjects_kb())
        return
    data = await state.get_data()
    fio = data.get("fio", "")
    role_flag = data.get("role", "novice")
    db = load_users()
    p = get_user(db, m.from_user.id)
    p.fio = fio
    p.subject = m.text
    p.last_update = datetime.now(TZ_MOSCOW).isoformat()
    if role_flag == "summer":
        p.role = "summer"
        now = datetime.now(TZ_MOSCOW)
        p.summer_assigned_at = now.isoformat()
        p.summer_deadline = (now + timedelta(hours=24)).isoformat()
        upsert_user(db, m.from_user.id, p)
        guides_list = "\n".join([f"• {g['title']}: {g['url']}" for g in GUIDES])
        await state.clear()
        await m.answer(f"Готово, {p.fio}! Ты летник ({p.subject}).\n{guides_list}\nТест: {SUMMER_TEST_LINK}", reply_markup=main_kb())
    else:
        p.role = "novice"
        upsert_user(db, m.from_user.id, p)
        await state.clear()
        await m.answer(f"Отлично, {p.fio}! Ты новичок ({p.subject}). Гайды будут приходить в 08:00.", reply_markup=main_kb())

@dp.message(F.text == "📖 Я прочитал гайд")
async def confirm_read(m: Message):
    db = load_users()
    p = get_user(db, m.from_user.id)
    if p.role != "novice" or not p.awaiting_read_confirm:
        await m.answer("Сначала дождись гайда.")
        return
    p.awaiting_read_confirm = False
    p.guide_read_dates.append(today_str())
    day_idx = p.current_day + 1
    if day_idx == 3:
        task = TASKS_SUBJECT.get(p.subject)
    else:
        task = TASKS_COMMON.get(day_idx)
    if task:
        await m.answer(f"📝 Задание:\n<b>{task['title']}</b>\n{task['text']}", parse_mode=ParseMode.HTML)
        p.task_given_dates.append(today_str())
    p.last_update = datetime.now(TZ_MOSCOW).isoformat()
    upsert_user(db, m.from_user.id, p)

@dp.message(F.text == "✅ Я выполнил задание")
async def task_done(m: Message):
    now_time = datetime.now(TZ_MOSCOW).time()
    if now_time >= time(22, 0):
        await m.answer("❌ Время сдачи задания истекло.")
        return
    db = load_users()
    p = get_user(db, m.from_user.id)
    if p.role != "novice" or not is_today(p.task_given_dates):
        await m.answer("Сначала получи задание.")
        return
    if is_today(p.task_done_dates):
        await m.answer("Уже отмечено.")
        return
    p.task_done_dates.append(today_str())
    p.current_day += 1
    upsert_user(db, m.from_user.id, p)
    await m.answer("✅ Задание выполнено!")

@dp.message(Command("progress"))
async def my_progress(m: Message):
    db = load_users()
    p = get_user(db, m.from_user.id)
    await m.answer(f"Роль: {p.role}\nФИО: {p.fio}\nПредмет: {p.subject}\nДень: {p.current_day}/{len(GUIDES)}")

@dp.message(Command("admin"))
async def admin_panel(m: Message):
    if m.from_user.id != ADMIN_ID:
        return
    db = load_users()
    total = len(db)
    novices = sum(1 for u in db.values() if u.get("role") == "novice")
    summers = sum(1 for u in db.values() if u.get("role") == "summer")
    lines = [f"Всего: {total}", f"Новичков: {novices}", f"Летников: {summers}"]
    await m.answer("\n".join(lines))

@dp.message(Command("export"))
async def export_csv(m: Message):
    if m.from_user.id != ADMIN_ID:
        return
    db = load_users()
    headers = list(Progress().__dict__.keys())
    with open(EXPORT_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["user_id"] + headers)
        for uid, raw in db.items():
            p = Progress(**raw)
            w.writerow([uid] + [getattr(p, h) for h in headers])
    await m.answer("CSV сохранён.")

async def main():
    await catchup_after_reboot(bot)
    asyncio.create_task(daily_broadcast(bot))
    asyncio.create_task(reminders(bot))
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())


