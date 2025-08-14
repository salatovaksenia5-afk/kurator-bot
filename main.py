# -*- coding: utf-8 -*-
"""
Куратор-бот (aiogram 3.x)
- Роли: Новичок / Летник (по коду)
- Сбор ФИО и предмета
- Новичкам: ежедневные гайды в 08:00 по Москве, по одному в день
  После подтверждения чтения — выдаётся задание за этот день
- Летникам: проверка кода + тест-ссылка
- Прогресс в data/users.json, экспорт /export
- Команда /admin — краткий прогресс (видно только ADMIN_ID)
!!! ВАЖНО: Токен и ADMIN_ID берём из переменных окружения
"""
import asyncio
import csv
import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Dict, Any, List

import pytz
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.client.default import DefaultBotProperties

# ========= НАСТРОЙКИ =========
TOKEN = os.getenv("TOKEN", "").strip()                  # <-- ОБЯЗАТЕЛЬНО задать в Render/локально
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))              # <-- твой Telegram ID
SUMMER_CODE = os.getenv("SUMMER_CODE", "лето2025")      # код для летников
SUMMER_TEST_LINK = os.getenv("SUMMER_TEST_LINK", "https://example.com/test")

# гайды (название -> ссылка). Заполняй как нужно.
GUIDES: List[Dict[str, str]] = [
    {"title": "1) Основы и этика",        "url": "https://example.com/guide1"},
    {"title": "2) Техническая часть",     "url": "https://example.com/guide2"},
    {"title": "3) Предмет (индивидуально)", "url": "https://example.com/guide3"},
    {"title": "4) Основные проблемы",     "url": "https://example.com/guide4"},
]

# задания по умолчанию (когда предмет не нужен)
TASKS_DEFAULT: List[Dict[str, str]] = [
    {"title": "Задание к гайду 1", "text": "Заглушка: задание дня 1."},
    {"title": "Задание к гайду 2", "text": "Заглушка: задание дня 2."},
    # Третий гайд — предметозависимый, заполним ниже
    {"title": "Задание к гайду 3", "text": "Заглушка предметного задания."},
    {"title": "Задание к гайду 4", "text": "Заглушка: задание дня 4."},
]

# для 3-го дня — задания по предметам (сюда подставляешь свои ссылки/тексты)
SUBJECT_TASKS_DAY3: Dict[str, str] = {
    "информатика":    "Заглушка: задание по информатике (день 3).",
    "физика":         "Заглушка: задание по физике (день 3).",
    "русский язык":   "Заглушка: задание по русскому (день 3).",
    "обществознание": "Заглушка: задание по обществознанию (день 3).",
    "биология":       "Заглушка: задание по биологии (день 3).",
    "химия":          "Заглушка: задание по химии (день 3).",
}

SEND_HOUR = 8
SEND_MINUTE = 0
TZ_MOSCOW = pytz.timezone("Europe/Moscow")

DATA_DIR = "data"
USERS_FILE = os.path.join(DATA_DIR, "users.json")
EXPORT_CSV = os.path.join(DATA_DIR, "export.csv")
os.makedirs(DATA_DIR, exist_ok=True)

# ========= МОДЕЛЬ =========
@dataclass
class Progress:
    role: str = ""          # "novice" | "summer"
    fio: str = ""
    subject: str = ""
    current_day: int = 0    # сколько дней уже закрыто (0..len(GUIDES))
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
        if not self.last_update:
            self.last_update = datetime.now(TZ_MOSCOW).isoformat()

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

# ========= FSM =========
class RegStates(StatesGroup):
    waiting_role = State()
    waiting_summer_code = State()
    waiting_fio = State()
    waiting_subject = State()

# ========= Клавиатура =========
def main_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Я новичок"), KeyboardButton(text="Я летник")],
            [KeyboardButton(text="📖 Я прочитал гайд"),
             KeyboardButton(text="✅ Я выполнил задание")],
            [KeyboardButton(text="📊 Мой прогресс")],
        ],
        resize_keyboard=True
    )

# ========= Хелперы =========
def today_str() -> str:
    return datetime.now(TZ_MOSCOW).date().isoformat()

def is_today(dates: List[str]) -> bool:
    return today_str() in (dates or [])

def next_run_delay_sec(hour: int, minute: int) -> float:
    now = datetime.now(TZ_MOSCOW)
    run_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if run_at <= now:
        run_at += timedelta(days=1)
    return (run_at - now).total_seconds()

async def send_guide_if_due(bot: Bot, user_id: int, db: Dict[str, Dict[str, Any]]) -> None:
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
        f"После прочтения нажми «📖 Я прочитал гайд», и я пришлю задание.",
    )
    p.guide_sent_dates.append(today_str())
    p.awaiting_read_confirm = True
    upsert_user(db, user_id, p)

async def daily_broadcast(bot: Bot):
    while True:
        await asyncio.sleep(next_run_delay_sec(SEND_HOUR, SEND_MINUTE))
        db = load_users()
        for uid in list(db.keys()):
            try:
                await send_guide_if_due(bot, int(uid), db)
            except Exception:
                continue

# ========= БОТ =========
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

@dp.message(CommandStart())
async def on_start(m: Message, state: FSMContext):
    db = load_users()
    p = get_user(db, m.from_user.id)
    upsert_user(db, m.from_user.id, p)

    await state.clear()
    await state.set_state(RegStates.waiting_role)
    await m.answer(
        "Привет! Я бот-куратор.\n\nВыбери, кто ты:",
        reply_markup=main_kb()
    )

@dp.message(F.text.lower() == "я летник")
async def choose_summer(m: Message, state: FSMContext):
    await state.set_state(RegStates.waiting_summer_code)
    await m.answer("Введи код доступа:")

@dp.message(RegStates.waiting_summer_code)
async def summer_check_code(m: Message, state: FSMContext):
    if (m.text or "").strip().lower() != SUMMER_CODE.lower():
        await m.answer("❌ Неверный код. Попробуй ещё раз:")
        return
    await state.update_data(role="summer")
    await state.set_state(RegStates.waiting_fio)
    await m.answer("Код верный. Введи своё ФИО (одной строкой):")

@dp.message(F.text.lower() == "я новичок")
async def choose_novice(m: Message, state: FSMContext):
    await state.set_state(RegStates.waiting_fio)
    await m.answer("Окей! Введи своё ФИО (одной строкой):")

@dp.message(RegStates.waiting_fio)
async def reg_fio(m: Message, state: FSMContext):
    fio = (m.text or "").strip()
    if len(fio) < 2:
        await m.answer("Введи настоящее ФИО, пожалуйста:")
        return
    await state.update_data(fio=fio)
    await state.set_state(RegStates.waiting_subject)
    await m.answer("Теперь введи предмет (например: «информатика», «физика», «русский язык», «обществознание», «биология», «химия»):")

@dp.message(RegStates.waiting_subject)
async def reg_subject(m: Message, state: FSMContext):
    subject = (m.text or "").strip().lower()
    data = await state.get_data()
    fio = data.get("fio", "")
    role = "summer" if data.get("role") == "summer" else "novice"

    db = load_users()
    p = get_user(db, m.from_user.id)
    p.fio = fio
    p.subject = subject
    p.role = role
    p.last_update = datetime.now(TZ_MOSCOW).isoformat()
    upsert_user(db, m.from_user.id, p)
    await state.clear()

    if role == "summer":
        await m.answer(
            f"Готово, {p.fio}! Вот тест летника:\n{SUMMER_TEST_LINK}\n\n"
            f"Свой прогресс можно смотреть через «📊 Мой прогресс».",
            reply_markup=main_kb()
        )
    else:
        await m.answer(
            f"Отлично, {p.fio}! Первый гайд придёт завтра в {SEND_HOUR:02d}:{SEND_MINUTE:02d} (МСК). "
            f"После чтения жми «📖 Я прочитал гайд», и я вышлю задание.",
            reply_markup=main_kb()
        )

@dp.message(F.text == "📖 Я прочитал гайд")
async def confirm_read(m: Message):
    db = load_users()
    p = get_user(db, m.from_user.id)
    if p.role != "novice":
        await m.answer("Эта кнопка для новичков. Если ты летник — смотри тест.")
        return
    if not p.awaiting_read_confirm:
        await m.answer("Сначала дождись гайда (в 08:00 по Москве) и прочитай его.")
        return

    p.awaiting_read_confirm = False
    p.guide_read_dates.append(today_str())

    day_index = p.current_day  # 0..N-1
    # выдаём задание
    if day_index == 2:  # третий день (индекс 2) — предметное
        text = SUBJECT_TASKS_DAY3.get(p.subject.lower(), TASKS_DEFAULT[2]["text"])
        title = "Задание к гайду 3 (по твоему предмету)"
        await m.answer(f"📝 <b>{title}</b>\n{text}", reply_markup=main_kb())
    else:
        tasks = TASKS_DEFAULT
        if day_index < len(tasks):
            task = tasks[day_index]
            await m.answer(f"📝 <b>{task['title']}</b>\n{task['text']}", reply_markup=main_kb())
        else:
            await m.answer("Все задания уже выданы. Красота!")

    p.task_given_dates.append(today_str())
    upsert_user(db, m.from_user.id, p)

@dp.message(F.text == "✅ Я выполнил задание")
async def mark_done(m: Message):
    db = load_users()
    p = get_user(db, m.from_user.id)
    if p.role != "novice":
        await m.answer("Эта кнопка для новичков.")
        return
    if not is_today(p.task_given_dates):
        await m.answer("Сначала получи задание (после подтверждения чтения гайда).")
        return
    if is_today(p.task_done_dates):
        await m.answer("Я уже записал выполнение задания сегодня. 👍")
        return

    p.task_done_dates.append(today_str())
    p.current_day = min(p.current_day + 1, len(GUIDES))
    p.last_update = datetime.now(TZ_MOSCOW).isoformat()
    upsert_user(db, m.from_user.id, p)

    if p.current_day >= len(GUIDES):
        await m.answer("🔥 Ты прошёл все гайды и задания. Поздравляю!")
    else:
        await m.answer("Записал! Жди следующий гайд завтра в 08:00 (МСК).")

@dp.message(F.text == "📊 Мой прогресс")
@dp.message(Command("progress"))
async def progress(m: Message):
    db = load_users()
    p = get_user(db, m.from_user.id)
    if not p.role:
        await m.answer("Ты ещё не зарегистрирован. Нажми «Я новичок» или «Я летник».")
        return

    if p.role == "summer":
        await m.answer(
            f"Ты летник.\nФИО: {p.fio}\nПредмет: {p.subject}\nТест: {SUMMER_TEST_LINK}"
        )
        return

    total = len(GUIDES)
    done = p.current_day
    guide_today = "да" if is_today(p.guide_sent_dates) else "нет"
    task_today = "да" if is_today(p.task_given_dates) else "нет"
    task_done = "да" if is_today(p.task_done_dates) else "нет"

    await m.answer(
        "Прогресс (новичок):\n"
        f"ФИО: {p.fio}\nПредмет: {p.subject}\n"
        f"Пройдено дней: {done} из {total}\n"
        f"Гайд сегодня отправлен: {guide_today}\n"
        f"Задание сегодня выдано: {task_today}\n"
        f"Задание сегодня выполнено: {task_done}\n"
        f"Последнее обновление: {p.last_update}",
        reply_markup=main_kb()
    )

@dp.message(Command("export"))
async def export_csv(m: Message):
    if ADMIN_ID and m.from_user.id != ADMIN_ID:
        await m.answer("Экспорт доступен только администратору.")
        return
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
                uid, p.role, p.fio, p.subject, p.current_day,
                ",".join(p.guide_sent_dates),
                ",".join(p.guide_read_dates),
                ",".join(p.task_given_dates),
                ",".join(p.task_done_dates),
                p.last_update
            ])
    await m.answer("Экспорт готов: data/export.csv")

@dp.message(Command("admin"))
async def admin_panel(m: Message):
    if ADMIN_ID and m.from_user.id != ADMIN_ID:
        return
    db = load_users()
    total = len(db)
    novices = sum(1 for v in db.values() if v.get("role") == "novice")
    summers = sum(1 for v in db.values() if v.get("role") == "summer")
    max_day = max((Progress(**v).current_day for v in db.values() if v.get("role") == "novice"), default=0)
    await m.answer(
        f"Админ-статистика:\nВсего пользователей: {total}\n"
        f"Новичков: {novices}\nЛетников: {summers}\n"
        f"Макс. день среди новичков: {max_day}"
    )

async def main():
    # фоновая рассылка
    asyncio.create_task(daily_broadcast(bot))
    await dp.start_polling(bot)

if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Не задан TOKEN (переменная окружения).")
    asyncio.run(main())
