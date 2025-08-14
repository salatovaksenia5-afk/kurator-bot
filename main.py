# -*- coding: utf-8 -*-
"""
Бот-куратор (aiogram v3)

Фичи:
- Роли: Новичок / Летник (по коду)
- Сбор ФИО и предмета
- Новичок: авто-рассылка гайдов в 08:00 (МСК) по одному в день
  -> "📖 Я прочитал гайд" -> выдача задания -> "✅ Я выполнил задание"
- Напоминания о дедлайне: 21:30 и 21:55 МСК, если задание за сегодня не сдано
- Летник: выдача теста + каталог гайдов ("📚 Гайды", "📥 Все гайды")
- Предметное задание на 3-й день (информатика, физика, русский, обществознание, биология, химия)
- Прогресс в data/users.json
- Админ-команды: /stats, /export (CSV), /find <текст>, /send_today (форс выдачу гайда)
"""

import asyncio
import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List

import pytz
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton, FSInputFile
)

# ======================= НАСТРОЙКИ =======================

# Токен бота
TOKEN = os.getenv("TOKEN", "8222461922:AAEi2IxJfevX_LpL2bQ1s_dc_Uym7-rb2fk")

# ID админа (только ему доступны /stats, /export, /find, /send_today)
ADMIN_IDS = {int(os.getenv("ADMIN_ID", "1026494049"))}

# Код доступа летников
SUMMER_CODE = os.getenv("SUMMER_CODE", "лето2025")

# Ссылка на тест летников
SUMMER_TEST_LINK = os.getenv(
    "SUMMER_TEST_LINK",
    "https://docs.google.com/forms/d/e/1FAIpQLSdR-iR1mhQBwlNMPKNa_ugjYMAnIYnPDRAdrAbcwRjhBVqoPA/viewform?usp=header"
)

# Гайды (заполни своими ссылками)
GUIDES: List[Dict[str, str]] = [
    {"title": "1) Основы и этика",        "url": "https://example.com/guide1"},
    {"title": "2) Техническая часть",     "url": "https://example.com/guide2"},
    {"title": "3) Предмет",               "url": "https://example.com/guide3"},
    {"title": "4) Основные проблемы",     "url": "https://example.com/guide4"},
]

# Задания по дням (после подтверждения чтения гайда)
TASKS_BASE: List[Dict[str, str]] = [
    {"title": "Задание к гайду 1", "text": "Задание дня 1. (заглушка, вставь свой текст/ссылку)"},
    {"title": "Задание к гайду 2", "text": "Задание дня 2. (заглушка, вставь свой текст/ссылку)"},
    # Для дня 3 будет предметное задание (динамически)
    {"title": "Задание к гайду 4", "text": "Задание дня 4. (заглушка, вставь свой текст/ссылку)"},
]

# Карта предметных заданий для дня 3 (новичок)
SUBJECT_TASKS_DAY3 = {
    "информатика": "Задание по информатике (заглушка-ссылка).",
    "физика": "Задание по физике (заглушка-ссылка).",
    "русский язык": "Задание по русскому языку (заглушка-ссылка).",
    "обществознание": "Задание по обществознанию (заглушка-ссылка).",
    "биология": "Задание по биологии (заглушка-ссылка).",
    "химия": "Задание по химии (заглушка-ссылка).",
}

# Часы рассылок (МСК)
TZ_MOSCOW = pytz.timezone("Europe/Moscow")
GUIDE_HOUR = int(os.getenv("GUIDE_HOUR", "8"))      # 08:00 — гайд
GUIDE_MIN = int(os.getenv("GUIDE_MIN", "0"))
REMIND1_HOUR, REMIND1_MIN = 21, 30                   # 21:30 — мягкое напоминание
REMIND2_HOUR, REMIND2_MIN = 21, 55                   # 21:55 — жёсткое напоминание до 22:00

# Хранилище
DATA_DIR = "data"
USERS_FILE = os.path.join(DATA_DIR, "users.json")
EXPORT_CSV = os.path.join(DATA_DIR, "export.csv")
os.makedirs(DATA_DIR, exist_ok=True)

# ======================= МОДЕЛИ =======================

@dataclass
class Progress:
    role: str = ""  # "novice" | "summer"
    fio: str = ""
    subject: str = ""
    current_day: int = 0  # сколько гайдов пройдено (0..len(GUIDES))
    guide_sent_dates: List[str] = None
    guide_read_dates: List[str] = None
    task_given_dates: List[str] = None
    task_done_dates: List[str] = None
    awaiting_read_confirm: bool = False
    last_update: str = ""

    def __post_init__(self):
        self.guide_sent_dates = self.guide_sent_dates or []
        self.guide_read_dates = self.guide_read_dates or []
        self.task_given_dates = self.task_given_dates or []
        self.task_done_dates = self.task_done_dates or []
        self.last_update = self.last_update or datetime.now(TZ_MOSCOW).isoformat()

# ======================= БАЗА =======================

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

def today_str() -> str:
    return datetime.now(TZ_MOSCOW).date().isoformat()

def is_today(dates: List[str]) -> bool:
    return today_str() in (dates or [])

def next_delay_to(hour: int, minute: int) -> float:
    now = datetime.now(TZ_MOSCOW)
    run_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if run_at <= now:
        run_at += timedelta(days=1)
    return (run_at - now).total_seconds()

# ======================= FSM =======================

class RegStates(StatesGroup):
    waiting_role = State()
    waiting_summer_code = State()
    waiting_fio = State()
    waiting_subject = State()

# ======================= UI =======================

def main_kb(role: Optional[str] = None) -> ReplyKeyboardMarkup:
    # Для летника показываем каталог гайдов
    rows = []
    rows.append([KeyboardButton(text="Я новичок"), KeyboardButton(text="Я летник")])
    rows.append([KeyboardButton(text="📖 Я прочитал гайд"), KeyboardButton(text="✅ Я выполнил задание")])
    rows.append([KeyboardButton(text="📊 Мой прогресс")])
    if role == "summer":
        rows.append([KeyboardButton(text="📚 Гайды"), KeyboardButton(text="📥 Все гайды")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)

# ======================= БОТ =======================

bot = Bot(TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()

# ---------- Вспомогательное: сформировать текст задания дня ----------
def make_task_text_for_day(p: Progress) -> Optional[str]:
    """
    Возвращает текст задания по индексу текущего дня (p.current_day),
    который соответствует только что прочитанному гайду.
    """
    day_index = p.current_day  # 0..N-1
    # День 3 — предметный (индекс 2)
    if day_index == 2:
        subj = (p.subject or "").strip().lower()
        text = SUBJECT_TASKS_DAY3.get(subj)
        if not text:
            # Если предмет не распознан — дайте заглушку и попросите написать обучатору
            return "Предметное задание (заглушка). Не распознал предмет — напиши обучатору."
        return text

    # для остальных дней — из TASKS_BASE
    # TASKS_BASE имеет 3 элемента: для дней 1,2 и 4 (индексы 0,1,3)
    if day_index == 0:
        return TASKS_BASE[0]["text"]
    if day_index == 1:
        return TASKS_BASE[1]["text"]
    if day_index == 3:
        return TASKS_BASE[2]["text"]

    return None

# ---------- Планировщики ----------

async def send_guide_if_due(user_id: int, db: Dict[str, Dict[str, Any]]) -> None:
    p = get_user(db, user_id)
    if p.role != "novice":
        return
    if p.current_day >= len(GUIDES):
        return
    if is_today(p.guide_sent_dates):
        return

    guide = GUIDES[p.current_day]
    await bot.send_message(
        user_id,
        f"📖 Твой гайд на сегодня:\n<b>{guide['title']}</b>\n{guide['url']}\n\n"
        f"прочитай и нажми «📖 Я прочитал гайд», чтобы получить задание.",
    )
    p.guide_sent_dates.append(today_str())
    p.awaiting_read_confirm = True
    p.last_update = datetime.now(TZ_MOSCOW).isoformat()
    upsert_user(db, user_id, p)

async def daily_guides_loop():
    while True:
        await asyncio.sleep(next_delay_to(GUIDE_HOUR, GUIDE_MIN))
        db = load_users()
        for uid in list(db.keys()):
            try:
                await send_guide_if_due(int(uid), db)
            except Exception:
                continue

async def reminders_loop(hour: int, minute: int, kind: str):
    """kind: 'soft' или 'hard' (для текста)"""
    while True:
        await asyncio.sleep(next_delay_to(hour, minute))
        db = load_users()
        for uid, raw in list(db.items()):
            p = Progress(**raw)
            if p.role != "novice":
                continue
            # если сегодня дали задание, но не сдали — напомнить
            if is_today(p.task_given_dates) and not is_today(p.task_done_dates):
                if kind == "soft":
                    txt = "⏰ Напоминание: сегодня есть задание. Успей сдать до 22:00 по МСК."
                else:
                    txt = "⏰ Супер-напоминание: сдача задания закрывается в 22:00 по МСК. Нажми «✅ Я выполнил задание», если всё готово."
                try:
                    await bot.send_message(int(uid), txt, reply_markup=main_kb(role=p.role))
                except Exception:
                    continue

# ======================= ХЕНДЛЕРЫ =======================

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

@dp.message(CommandStart())
async def start(m: Message, state: FSMContext):
    db = load_users()
    p = get_user(db, m.from_user.id)
    upsert_user(db, m.from_user.id, p)

    await state.clear()
    await state.set_state(RegStates.waiting_role)
    role_hint = " (летники видят каталог гайдов)" if p.role == "summer" else ""
    await m.answer(
        "привет! я бот-куратор.\n\nвыбери, кто ты:",
        reply_markup=main_kb(role=p.role or None)
    )

@dp.message(F.text.lower() == "я летник")
async def choose_summer(m: Message, state: FSMContext):
    await state.set_state(RegStates.waiting_summer_code)
    await m.answer("введи код доступа:")

@dp.message(RegStates.waiting_summer_code)
async def summer_code(m: Message, state: FSMContext):
    if (m.text or "").strip().lower() != SUMMER_CODE.lower():
        await m.answer("❌ неверный код. попробуй ещё раз:")
        return
    await state.update_data(role="summer")
    await state.set_state(RegStates.waiting_fio)
    await m.answer("ок. введи своё фио (одной строкой):")

@dp.message(F.text.lower() == "я новичок")
async def choose_novice(m: Message, state: FSMContext):
    await state.set_state(RegStates.waiting_fio)
    await m.answer("отлично. введи своё фио (одной строкой):")

@dp.message(RegStates.waiting_fio)
async def reg_fio(m: Message, state: FSMContext):
    fio = (m.text or "").strip()
    if len(fio) < 2:
        await m.answer("введи настоящее фио, пожалуйста:")
        return
    await state.update_data(fio=fio)
    await state.set_state(RegStates.waiting_subject)
    await m.answer("теперь введи предмет (например: «информатика», «физика», «русский язык», «обществознание», «биология», «химия»):")

@dp.message(RegStates.waiting_subject)
async def reg_subject(m: Message, state: FSMContext):
    subject = (m.text or "").strip().lower()
    data = await state.get_data()
    fio = data.get("fio", "")
    role_flag = data.get("role")

    db = load_users()
    p = get_user(db, m.from_user.id)
    p.fio = fio
    p.subject = subject
    if role_flag == "summer":
        p.role = "summer"
    else:
        p.role = "novice"
    p.last_update = datetime.now(TZ_MOSCOW).isoformat()
    upsert_user(db, m.from_user.id, p)

    await state.clear()

    if p.role == "summer":
        await m.answer(
            f"готово, {p.fio}.\nвот твой тест летника:\n{SUMMER_TEST_LINK}\n\n"
            f"также можешь открыть каталог: «📚 гайды» или получить всё: «📥 все гайды».",
            reply_markup=main_kb(role=p.role)
        )
    else:
        await m.answer(
            f"ок, {p.fio}. первый гайд придёт в {GUIDE_HOUR:02d}:{GUIDE_MIN:02d} по москве. "
            f"после чтения жми «📖 я прочитал гайд», и я пришлю задание.",
            reply_markup=main_kb(role=p.role)
        )

@dp.message(F.text == "📚 Гайды")
async def summer_guides(m: Message):
    db = load_users()
    p = get_user(db, m.from_user.id)
    if p.role != "summer":
        await m.answer("эта кнопка для летника. если ты новичок — жди ежедневные гайды.")
        return
    lines = [f"• <a href='{g['url']}'>{g['title']}</a>" for g in GUIDES]
    await m.answer("каталог гайдов:\n" + "\n".join(lines))

@dp.message(F.text == "📥 Все гайды")
async def summer_all_guides(m: Message):
    db = load_users()
    p = get_user(db, m.from_user.id)
    if p.role != "summer":
        await m.answer("эта кнопка для летника.")
        return
    txt = "\n\n".join([f"<b>{g['title']}</b>\n{g['url']}" for g in GUIDES])
    await m.answer(txt)

@dp.message(F.text == "📖 Я прочитал гайд")
async def confirm_read(m: Message):
    db = load_users()
    p = get_user(db, m.from_user.id)
    if p.role != "novice":
        await m.answer("эта кнопка для новичка. если ты летник — открывай «📚 гайды».")
        return
    if not p.awaiting_read_confirm:
        await m.answer("сначала дождись гайда и прочитай его. гайды приходят в 08:00 по москве.")
        return

    # подтверждаем
    p.awaiting_read_confirm = False
    p.guide_read_dates.append(today_str())
    # выдаём задание этого же дня
    task_text = make_task_text_for_day(p)
    if task_text:
        await m.answer(f"📝 задание дня:\n{task_text}")
        p.task_given_dates.append(today_str())
    else:
        await m.answer("на этот день задание не настроено. напиши обучатору.")
    upsert_user(db, m.from_user.id, p)

@dp.message(F.text == "✅ Я выполнил задание")
async def task_done(m: Message):
    db = load_users()
    p = get_user(db, m.from_user.id)
    if p.role != "novice":
        await m.answer("эта кнопка только для новичка.")
        return
    if not is_today(p.task_given_dates):
        await m.answer("сначала получи задание (после «📖 я прочитал гайд»).")
        return
    if is_today(p.task_done_dates):
        await m.answer("я уже записал, что ты выполнил задание сегодня. nice!")
        return

    p.task_done_dates.append(today_str())
    p.current_day = min(p.current_day + 1, len(GUIDES))  # переходим к след. дню
    p.last_update = datetime.now(TZ_MOSCOW).isoformat()
    upsert_user(db, m.from_user.id, p)

    if p.current_day >= len(GUIDES):
        await m.answer("отлично! ты прошёл все гайды и задания. 🎉")
    else:
        await m.answer("записал. жди следующий гайд завтра в 08:00 (мск).")

@dp.message(F.text == "📊 Мой прогресс")
@dp.message(Command("progress"))
async def show_progress(m: Message):
    db = load_users()
    p = get_user(db, m.from_user.id)
    if not p.role:
        await m.answer("ты ещё не зарегистрирован. нажми «я новичок» или «я летник».")
        return
    if p.role == "summer":
        await m.answer(
            f"роль: летник\nфио: {p.fio}\nпредмет: {p.subject}\nтест: {SUMMER_TEST_LINK}",
            reply_markup=main_kb(role=p.role)
        )
        return
    total = len(GUIDES)
    done = p.current_day
    guide_today = "да" if is_today(p.guide_sent_dates) else "нет"
    task_today = "да" if is_today(p.task_given_dates) else "нет"
    task_done = "да" if is_today(p.task_done_dates) else "нет"
    await m.answer(
        "твой прогресс (новичок):\n"
        f"фио: {p.fio}\nпредмет: {p.subject}\n"
        f"пройдено дней: {done} из {total}\n"
        f"гайд сегодня отправлен: {guide_today}\n"
        f"задание сегодня выдано: {task_today}\n"
        f"задание сегодня выполнено: {task_done}\n"
        f"последнее обновление: {p.last_update}",
        reply_markup=main_kb(role=p.role)
    )

# ---------- Админки ----------

@dp.message(Command("stats"))
async def stats(m: Message):
    if not is_admin(m.from_user.id):
        return
    db = load_users()
    total = len(db)
    novice = sum(1 for _, r in db.items() if r.get("role") == "novice")
    summer = sum(1 for _, r in db.items() if r.get("role") == "summer")
    today_tasks = sum(1 for _, r in db.items()
                      if r.get("role") == "novice"
                      and today_str() in (r.get("task_done_dates") or []))
    await m.answer(
        f"статистика:\nвсего пользователей: {total}\nновичков: {novice}\nлетников: {summer}\n"
        f"сдали задание сегодня: {today_tasks}"
    )

@dp.message(Command("export"))
async def export_csv(m: Message):
    if not is_admin(m.from_user.id):
        return
    import csv
    db = load_users()
    headers = [
        "user_id", "role", "fio", "subject",
        "current_day", "guide_sent_dates", "guide_read_dates",
        "task_given_dates", "task_done_dates", "last_update"
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
                p.last_update
            ])
    await m.answer_document(FSInputFile(EXPORT_CSV), caption="экспорт готов (CSV).")

@dp.message(Command("find"))
async def find_user(m: Message):
    if not is_admin(m.from_user.id):
        return
    q = (m.text or "").split(maxsplit=1)
    if len(q) < 2:
        await m.answer("использование: /find <фрагмент фио или id>")
        return
    needle = q[1].strip().lower()
    db = load_users()
    hits = []
    for uid, raw in db.items():
        fio = (raw.get("fio") or "").lower()
        if needle in fio or needle in uid:
            hits.append((uid, raw))
    if not hits:
        await m.answer("ничего не нашёл.")
        return
    lines = []
    for uid, r in hits[:30]:
        lines.append(f"{uid} — {r.get('fio','?')} / {r.get('role','?')} / день {r.get('current_day',0)}")
    await m.answer("найдено:\n" + "\n".join(lines))

@dp.message(Command("send_today"))
async def force_send_today(m: Message):
    """Форс отдать сегодняшние гайды всем новичкам (на случай проверки/демо)."""
    if not is_admin(m.from_user.id):
        return
    db = load_users()
    cnt = 0
    for uid in list(db.keys()):
        try:
            await send_guide_if_due(int(uid), db)
            cnt += 1
        except Exception:
            continue
    await m.answer(f"ок. попытался разослать гайды. пользователей в базе: {len(db)}, обработал: {cnt}.")

# ======================= ЗАПУСК =======================

async def main():
    # фоновые задачи: утренний гайд + напоминалки
    asyncio.create_task(daily_guides_loop())
    asyncio.create_task(reminders_loop(REMIND1_HOUR, REMIND1_MIN, "soft"))
    asyncio.create_task(reminders_loop(REMIND2_HOUR, REMIND2_MIN, "hard"))

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
