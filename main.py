# -*- coding: utf-8 -*-
"""
Бот-куратор (aiogram 3)
- Роли: Новичок / Летник (по коду "лето2025")
- Сбор ФИО и предмета (кнопки)
- Новички: 1 гайд/день в 08:00 Europe/Moscow (+догонялка, если бот был оффлайн утром),
  после подтверждения чтения выдаётся задание; напоминание о сдаче до 22:00
- Летники: сразу все гайды, 24 часа на тест (напоминание)
- /admin — сводка по всем пользователям (только ADMIN_ID)
- /export — CSV (data/export.csv)
- Хранение прогресса: data/users.json
"""

import asyncio
import csv
import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, date
from typing import Dict, Any, Optional, List

import pytz
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
)

# ======================= КОНФИГ =======================

# Токен — возьмётся из переменной окружения TOKEN, иначе — из дефолта ниже
TOKEN = os.getenv("TOKEN", "8222461922:AAEi2IxJfevX_LpL2bQ1s_dc_Uym7-rb2fk")

# Твой Telegram ID (админ)
ADMIN_ID = 1026494049

# Код доступа для летников
SUMMER_CODE = "лето2025"

# Ссылка на тест летников
SUMMER_TEST_LINK = "https://docs.google.com/forms/d/e/1FAIpQLSdR-iR1mhQBwlNMPKNa_ugjYMAnIYnPDRAdrAbcwRjhBVqoPA/viewform?usp=header"

# Гайды — заглушки (поменяешь ссылки)
GUIDES: List[Dict[str, str]] = [
    {"title": "1) Основы и этика",        "url": "https://example.com/guide1"},
    {"title": "2) Техническая часть",     "url": "https://example.com/guide2"},
    {"title": "3) Предмет",               "url": "https://example.com/guide3"},
    {"title": "4) Основные проблемы",     "url": "https://example.com/guide4"},
]

# Задания — заглушки (по соответствующим гайдам)
TASKS: List[Dict[str, str]] = [
    {"title": "Задание к гайду 1", "text": "Задание дня 1: [ссылка]"},
    {"title": "Задание к гайду 2", "text": "Задание дня 2: [ссылка]"},
    {"title": "Задание к гайду 3", "text": "Задание дня 3 (по предмету): [ссылка]"},
    {"title": "Задание к гайду 4", "text": "Задание дня 4: [ссылка]"},
]

# Время рассылки гайдов (МСК)
SEND_HOUR = 8
SEND_MINUTE = 0

# Напоминание новичкам о сдаче задания (МСК)
REMIND_HOUR = 21
REMIND_MINUTE = 0

# Напоминание летникам об истечении 24 часов на тест (МСК)
SUMMER_REMIND_HOUR = 20
SUMMER_REMIND_MINUTE = 0

TZ_MOSCOW = pytz.timezone("Europe/Moscow")

# Каталоги/файлы
DATA_DIR = "data"
USERS_FILE = os.path.join(DATA_DIR, "users.json")
EXPORT_CSV = os.path.join(DATA_DIR, "export.csv")
os.makedirs(DATA_DIR, exist_ok=True)

# Доступные предметы (кнопки)
SUBJECTS = [
    "Химия", "Биология", "Обществознание",
    "Русский язык", "Информатика", "Профильная математика", "Физика"
]


# ======================= МОДЕЛИ =======================

@dataclass
class Progress:
    role: str = ""              # "novice" | "summer"
    fio: str = ""
    subject: str = ""
    # Новички:
    current_day: int = 0        # сколько гайдов завершено (0..len(GUIDES))
    guide_sent_dates: List[str] = None
    guide_read_dates: List[str] = None
    task_given_dates: List[str] = None
    task_done_dates: List[str] = None
    awaiting_read_confirm: bool = False
    last_guide_sent_date: str = ""  # YYYY-MM-DD (когда последний гайд был отправлен)
    # Летники:
    summer_assigned_at: str = ""    # ISO-время, когда выдали тест
    summer_deadline: str = ""       # ISO-время дедлайна теста (assign+24ч)
    summer_reminded: bool = False   # напоминание отправляли?
    # Общие:
    last_update: str = ""

    def __post_init__(self):
        self.guide_sent_dates = self.guide_sent_dates or []
        self.guide_read_dates = self.guide_read_dates or []
        self.task_given_dates = self.task_given_dates or []
        self.task_done_dates = self.task_done_dates or []
        if not self.last_update:
            self.last_update = datetime.now(TZ_MOSCOW).isoformat()


# ======================= ХРАНИЛИЩЕ =======================

def load_users() -> Dict[str, Dict[str, Any]]:
    if not os.path.exists(USERS_FILE):
        return {}
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_users(db: Dict[str, Dict[str, Any]]) -> None:
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

def get_user(db: Dict[str, Dict[str, Any]], user_id: int) -> Progress:
    uid = str(user_id)
    if uid not in db:
        db[uid] = asdict(Progress())
    return Progress(**db[uid])

def upsert_user(db: Dict[str, Dict[str, Any]], user_id: int, p: Progress) -> None:
    db[str(user_id)] = asdict(p)
    save_users(db)


# ======================= FSM =======================

class RegStates(StatesGroup):
    waiting_role = State()
    waiting_summer_code = State()
    waiting_fio = State()
    waiting_subject = State()


# ======================= КЛАВИАТУРЫ =======================

def main_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Я новичок"), KeyboardButton(text="Я летник")],
            [KeyboardButton(text="📖 Я прочитал гайд"), KeyboardButton(text="✅ Я выполнил задание")],
            [KeyboardButton(text="📊 Мой прогресс")],
        ],
        resize_keyboard=True
    )

def subjects_kb() -> ReplyKeyboardMarkup:
    # 2-3 кнопки в ряд
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

# ======================= УТИЛИТЫ =======================

def today_date() -> date:
    return datetime.now(TZ_MOSCOW).date()

def today_str() -> str:
    return today_date().isoformat()

def is_today(dates: List[str]) -> bool:
    return today_str() in (dates or [])

def next_run_delay_sec(hour: int, minute: int) -> float:
    now = datetime.now(TZ_MOSCOW)
    run_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if run_at <= now:
        run_at += timedelta(days=1)
    return (run_at - now).total_seconds()

async def send_guide_if_due(bot: Bot, uid: int, db: Dict[str, Dict[str, Any]]) -> None:
    """
    Отправить новичку гайд сегодня, если:
    - он новичок
    - ещё не прошёл все гайды
    - сегодня ещё не отправляли
    - нет висящего неподтверждённого чтения
    - нет невыполненного сегодняшнего задания
    """
    p = get_user(db, uid)
    if p.role != "novice":
        return
    if p.current_day >= len(GUIDES):
        return
    if is_today(p.guide_sent_dates):
        return
    if p.awaiting_read_confirm:
        return
    # если сегодня уже выдали задание — ждём его выполнения
    if is_today(p.task_given_dates) and not is_today(p.task_done_dates):
        return

    guide = GUIDES[p.current_day]
    await bot.send_message(
        uid,
        f"📖 Твой гайд на сегодня:\n<b>{guide['title']}</b>\n{guide['url']}\n\n"
        f"После прочтения нажми «📖 Я прочитал гайд», и я пришлю задание.",
        parse_mode=ParseMode.HTML,
        reply_markup=main_kb()
    )
    p.guide_sent_dates.append(today_str())
    p.last_guide_sent_date = today_str()
    p.awaiting_read_confirm = True
    p.last_update = datetime.now(TZ_MOSCOW).isoformat()
    upsert_user(db, uid, p)

async def catchup_after_reboot(bot: Bot):
    """
    Догонялка при рестарте: если сейчас уже после 08:00,
    а у новичка на сегодня гайд ещё не отправлен — отправим немедленно.
    """
    db = load_users()
    now = datetime.now(TZ_MOSCOW)
    if now.hour > SEND_HOUR or (now.hour == SEND_HOUR and now.minute >= SEND_MINUTE):
        for uid in list(db.keys()):
            try:
                await send_guide_if_due(bot, int(uid), db)
            except Exception:
                continue

# ======================= ФОНЫ =======================

async def daily_broadcast(bot: Bot):
    # Ежедневная рассылка гайдов в 08:00 МСК
    while True:
        await asyncio.sleep(next_run_delay_sec(SEND_HOUR, SEND_MINUTE))
        db = load_users()
        for uid in list(db.keys()):
            try:
                await send_guide_if_due(bot, int(uid), db)
            except Exception:
                continue

async def daily_reminders(bot: Bot):
    # 21:00 — напоминания новичкам, у кого сегодня выдано задание, но не отмечено как выполнено
    while True:
        await asyncio.sleep(next_run_delay_sec(REMIND_HOUR, REMIND_MINUTE))
        db = load_users()
        for uid, raw in db.items():
            try:
                p = Progress(**raw)
                if p.role == "novice":
                    if is_today(p.task_given_dates) and not is_today(p.task_done_dates):
                        await bot.send_message(int(uid),
                            "⏰ Напоминание: сегодня до 22:00 (МСК) нужно сдать задание. Если сделал — нажми «✅ Я выполнил задание».",
                            reply_markup=main_kb()
                        )
                # Летники: напоминание о дедлайне теста (24ч c момента выдачи)
                if p.role == "summer" and p.summer_deadline and not p.summer_reminded:
                    try:
                        deadline = datetime.fromisoformat(p.summer_deadline)
                        now = datetime.now(TZ_MOSCOW)
                        # напомним за ~2 часа до дедлайна (условно в 20:00)
                        if now >= deadline - timedelta(hours=2) and now < deadline:
                            await bot.send_message(int(uid),
                                f"⏰ Напоминание: у тебя осталось мало времени, чтобы пройти тест летника.\nСсылка: {SUMMER_TEST_LINK}"
                            )
                            p.summer_reminded = True
                            upsert_user(db, int(uid), p)
                    except Exception:
                        pass
            except Exception:
                continue

# ======================= БОТ =======================

bot = Bot(token=TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()

# ---------- /start ----------

@dp.message(CommandStart())
async def on_start(m: Message, state: FSMContext):
    db = load_users()
    p = get_user(db, m.from_user.id)
    upsert_user(db, m.from_user.id, p)

    await state.clear()
    await state.set_state(RegStates.waiting_role)
    await m.answer(
        "привет! я бот-куратор.\n\nвыбери, кто ты:",
        reply_markup=main_kb()
    )

# ---------- выбор роли ----------

@dp.message(F.text.lower() == "я летник")
async def choose_summer(m: Message, state: FSMContext):
    await state.set_state(RegStates.waiting_summer_code)
    await m.answer("введи код доступа:")

@dp.message(F.text.lower() == "я новичок")
async def choose_novice(m: Message, state: FSMContext):
    await state.set_state(RegStates.waiting_fio)
    await m.answer("окей. введи своё фио (одной строкой):")

@dp.message(RegStates.waiting_summer_code)
async def summer_check_code(m: Message, state: FSMContext):
    if (m.text or "").strip().lower() != SUMMER_CODE.lower():
        await m.answer("❌ неверный код. попробуй ещё раз:")
        return
    await state.update_data(role="summer")
    await state.set_state(RegStates.waiting_fio)
    await m.answer("код верный. введи своё фио (одной строкой):")

@dp.message(RegStates.waiting_fio)
async def reg_fio(m: Message, state: FSMContext):
    fio = (m.text or "").strip()
    if len(fio) < 2:
        await m.answer("введи настоящее фио, пожалуйста:")
        return
    await state.update_data(fio=fio)
    await state.set_state(RegStates.waiting_subject)
    await m.answer("выбери предмет:", reply_markup=subjects_kb())

@dp.message(RegStates.waiting_subject)
async def reg_subject(m: Message, state: FSMContext):
    subject = (m.text or "").strip()
    if subject not in SUBJECTS:
        await m.answer("выбери предмет кнопкой ниже:", reply_markup=subjects_kb())
        return

    data = await state.get_data()
    fio = data.get("fio", "")
    role_flag = data.get("role", "novice")

    db = load_users()
    p = get_user(db, m.from_user.id)
    p.fio = fio
    p.subject = subject
    p.last_update = datetime.now(TZ_MOSCOW).isoformat()

    if role_flag == "summer":
        p.role = "summer"
        # выдаём сразу все гайды + тест и ставим дедлайн 24 часа
        now = datetime.now(TZ_MOSCOW)
        p.summer_assigned_at = now.isoformat()
        p.summer_deadline = (now + timedelta(hours=24)).isoformat()
        upsert_user(db, m.from_user.id, p)

        guides_list = "\n".join([f"• {g['title']}: {g['url']}" for g in GUIDES])
        await state.clear()
        await m.answer(
            f"готово, {p.fio}! ты летник ({p.subject}).\n\n"
            f"вот все гайды сразу:\n{guides_list}\n\n"
            f"тест (24 часа с момента получения):\n{SUMMER_TEST_LINK}",
            reply_markup=main_kb()
        )
    else:
        p.role = "novice"
        upsert_user(db, m.from_user.id, p)
        await state.clear()
        await m.answer(
            f"отлично, {p.fio}! ты новичок ({p.subject}).\n"
            f"первый гайд придёт в {SEND_HOUR:02d}:{SEND_MINUTE:02d} (мск). "
            f"после прочтения жми «📖 я прочитал гайд», тогда пришлю задание.",
            reply_markup=main_kb()
        )

# ---------- подтверждение чтения и выполнение ----------

@dp.message(F.text == "📖 Я прочитал гайд")
async def confirm_read(m: Message):
    db = load_users()
    p = get_user(db, m.from_user.id)

    if p.role != "novice":
        await m.answer("эта кнопка только для новичков. для летников — тест по ссылке.")
        return
    if not p.awaiting_read_confirm:
        await m.answer("сначала дождись гайда и прочитай его. гайды приходят в 08:00 по москве.")
        return

    p.awaiting_read_confirm = False
    p.guide_read_dates.append(today_str())

    # выдаём задание по текущему дню (индекс = p.current_day)
    day_idx = p.current_day
    if day_idx < len(TASKS):
        task = TASKS[day_idx]
        await m.answer(
            f"📝 задание дня:\n<b>{task['title']}</b>\n{task['text']}",
            parse_mode=ParseMode.HTML,
            reply_markup=main_kb()
        )
        p.task_given_dates.append(today_str())
    else:
        await m.answer("все задания уже выданы. 🔥")

    p.last_update = datetime.now(TZ_MOSCOW).isoformat()
    upsert_user(db, m.from_user.id, p)

@dp.message(F.text == "✅ Я выполнил задание")
async def task_done(m: Message):
    db = load_users()
    p = get_user(db, m.from_user.id)

    if p.role != "novice":
        await m.answer("эта кнопка только для новичков.")
        return

    # можно пометить выполненным только если сегодня задание выдавалось
    if not is_today(p.task_given_dates):
        await m.answer("сначала получи задание (после прочтения гайда).")
        return

    if is_today(p.task_done_dates):
        await m.answer("я уже записал, что ты выполнил задание сегодня. 👌")
        return

    p.task_done_dates.append(today_str())
    p.current_day = min(p.current_day + 1, len(GUIDES))
    p.last_update = datetime.now(TZ_MOSCOW).isoformat()
    upsert_user(db, m.from_user.id, p)

    if p.current_day >= len(GUIDES):
        await m.answer("отлично! ты прошёл все гайды и задания. 🎉")
    else:
        await m.answer("записал! жди следующий гайд завтра в 08:00 (мск).")

# ---------- прогресс ----------

@dp.message(F.text == "📊 Мой прогресс")
@dp.message(Command("progress"))
async def my_progress(m: Message):
    db = load_users()
    p = get_user(db, m.from_user.id)
    if not p.role:
        await m.answer("ты ещё не зарегистрирован. нажми «я новичок» или «я летник».")
        return

    if p.role == "summer":
        deadline_txt = p.summer_deadline or "не назначен"
        await m.answer(
            "твой статус: летник\n"
            f"фио: {p.fio}\nпредмет: {p.subject}\n"
            f"ссылка на тест: {SUMMER_TEST_LINK}\n"
            f"дедлайн: {deadline_txt}",
            reply_markup=main_kb()
        )
        return

    total = len(GUIDES)
    done = p.current_day
    guide_today = "да" if is_today(p.guide_sent_dates) else "нет"
    task_today = "да" if is_today(p.task_given_dates) else "нет"
    task_done = "да" if is_today(p.task_done_dates) else "нет"
    await m.answer(
        "твой статус: новичок\n"
        f"фио: {p.fio}\nпредмет: {p.subject}\n"
        f"пройдено дней: {done} из {total}\n"
        f"гайд сегодня отправлен: {guide_today}\n"
        f"задание сегодня выдано: {task_today}\n"
        f"задание сегодня выполнено: {task_done}\n"
        f"последнее обновление: {p.last_update}",
        reply_markup=main_kb()
    )

# ---------- админ-команды ----------

def is_admin(uid: int) -> bool:
    return uid == ADMIN_ID

@dp.message(Command("admin"))
async def admin_panel(m: Message):
    if not is_admin(m.from_user.id):
        return
    db = load_users()
    total = len(db)
    novices = sum(1 for u in db.values() if u.get("role") == "novice")
    summers = sum(1 for u in db.values() if u.get("role") == "summer")

    lines = [
        f"👑 админ-панель",
        f"всего пользователей: {total}",
        f"новички: {novices}, летники: {summers}",
        "",
        "список:"
    ]
    for uid, raw in db.items():
        p = Progress(**raw)
        if p.role == "novice":
            lines.append(
                f"- {uid} | новичок | {p.fio} | {p.subject} | день {p.current_day}/{len(GUIDES)} | "
                f"гайд сегодня: {'+' if is_today(p.guide_sent_dates) else '-'} | "
                f"задание сегодня: {'+' if is_today(p.task_given_dates) else '-'} | "
                f"выполнил: {'+' if is_today(p.task_done_dates) else '-'}"
            )
        elif p.role == "summer":
            lines.append(
                f"- {uid} | летник | {p.fio} | {p.subject} | дедлайн: {p.summer_deadline or '-'}"
            )

    txt = "\n".join(lines)
    # телеграм режет >4к символов; если длинно — шлём частями
    MAX = 3500
    for i in range(0, len(txt), MAX):
        await m.answer(txt[i:i+MAX])

@dp.message(Command("export"))
async def export_csv(m: Message):
    if not is_admin(m.from_user.id):
        return
    db = load_users()
    headers = [
        "user_id", "role", "fio", "subject",
        "current_day", "guide_sent_dates", "guide_read_dates",
        "task_given_dates", "task_done_dates",
        "summer_assigned_at", "summer_deadline", "summer_reminded",
        "last_update"
    ]
    with open(EXPORT_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(headers)
        for uid, raw in db.items():
            p = Progress(**raw)
            w.writerow([
                uid, p.role, p.fio, p.subject,
                p.current_day,
                ",".join(p.guide_sent_dates),
                ",".join(p.guide_read_dates),
                ",".join(p.task_given_dates),
                ",".join(p.task_done_dates),
                p.summer_assigned_at, p.summer_deadline, p.summer_reminded,
                p.last_update
            ])
    await m.answer("csv сохранён в data/export.csv — скачай с сервера при необходимости.")

# ======================= ЗАПУСК =======================

async def main():
    # фоновые задачи: догонялка, ежедневная рассылка, напоминания
    await catchup_after_reboot(bot)
    asyncio.create_task(daily_broadcast(bot))
    asyncio.create_task(daily_reminders(bot))
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
