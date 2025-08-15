# main.py
# -*- coding: utf-8 -*-

"""
Куратор-бот (Render + Webhook, aiogram v3)
- роли: новичок / летник
- выбор предмета кнопками
- гайды: новички — по расписанию, летники — "открыть все"
- напоминания: новичкам до 22:00 МСК про задание; летникам — про тест (24 часа)
- простая статистика/прогресс (JSON-файл)
- админ-панель (только для ADMIN_ID)
- вебхук сервер (uvicorn); авто-установка вебхука при старте

Переменные окружения:
  BOT_TOKEN        — токен бота
  ADMIN_ID         — твой телеграм-id (строкой или числом)
  PUBLIC_URL       — публичный URL веб-сервиса (Render -> Settings -> Public URL)
  WEBHOOK_SECRET   — любой секрет (например abc123); будет в пути вебхука /tg/<secret>
  TIMEZONE         — часовой пояс в формате IANA, по умолчанию "Europe/Moscow"
  STORAGE_FILE     — путь к файлу с прогрессом, по умолчанию /tmp/kurator_data.json
"""

import threading
from aiohttp import web

async def handle(request):
    return web.Response(text="Bot is running!")

def run_web():
    app = web.Application()
    app.router.add_get("/", handle)
    web.run_app(app, port=10000)

# Запуск фейкового веб-сервера в отдельном потоке
threading.Thread(target=run_web).start()

import asyncio
import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, time
from typing import Dict, List, Optional, Set

import pytz
from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
)

# ------------------------ Конфиг ------------------------

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "secret123")
TIMEZONE = os.getenv("TIMEZONE", "Europe/Moscow")
STORAGE_FILE = os.getenv("STORAGE_FILE", "/tmp/kurator_data.json")

if not BOT_TOKEN:
    raise RuntimeError("ENV BOT_TOKEN is empty")
if not PUBLIC_URL:
    # На Render заполни PUBLIC_URL (Settings -> Environment -> Add var)
    # Пример: https://kurator-bot-xxxxx.onrender.com
    raise RuntimeError("ENV PUBLIC_URL is empty")

TZ = pytz.timezone(TIMEZONE)

# aiogram v3: parse_mode задаём через DefaultBotProperties
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher(storage=MemoryStorage())

router = Router()
dp.include_router(router)

# ------------------------ Данные/модель ------------------------

SUBJECTS = [
    "Информатика", "Физика", "Русский язык",
    "Обществознание", "Биология", "Химия"
]

# Заглушки для гайдов (можешь менять/расширять)
GUIDES_LIBRARY: Dict[str, List[str]] = {
    "Основы и этика": [
        "Гайд 1.1: Этика куратора — заглушка",
        "Гайд 1.2: Коммуникация с учеником — заглушка",
        "Гайд 1.3: Правила и стандарты — заглушка",
    ],
    "Техническая часть": [
        "Гайд 2.1: Настройка рабочих инструментов — заглушка",
        "Гайд 2.2: Как отмечать прогресс — заглушка",
        "Гайд 2.3: Формы и шаблоны — заглушка",
    ],
    "Предмет": [
        "Гайд 3.1: Методика по предмету — заглушка",
        "Гайд 3.2: Примеры уроков — заглушка",
    ],
    "Основные проблемы": [
        "Гайд 4.1: Типичные ошибки — заглушка",
        "Гайд 4.2: Что делать, если... — заглушка",
    ],
}

ALL_GUIDES_FLAT: List[str] = [g for group in GUIDES_LIBRARY.values() for g in group]

@dataclass
class Progress:
    role: str                        # "novice" | "letnik"
    subject: Optional[str] = None
    guides_read: Set[int] = None     # набор индексов из ALL_GUIDES_FLAT
    tasks_done: int = 0
    next_guide_index: int = 0        # какой индекс отправлять новичку в следующий раз
    last_reminder_date: Optional[str] = None  # "YYYY-MM-DD" когда слали напоминание
    last_test_warn_date: Optional[str] = None # для летников: когда слали предупреждение

    def to_json(self):
        return {
            "role": self.role,
            "subject": self.subject,
            "guides_read": list(self.guides_read or set()),
            "tasks_done": self.tasks_done,
            "next_guide_index": self.next_guide_index,
            "last_reminder_date": self.last_reminder_date,
            "last_test_warn_date": self.last_test_warn_date,
        }

    @staticmethod
    def from_json(d):
        return Progress(
            role=d.get("role", "novice"),
            subject=d.get("subject"),
            guides_read=set(d.get("guides_read", [])),
            tasks_done=int(d.get("tasks_done", 0)),
            next_guide_index=int(d.get("next_guide_index", 0)),
            last_reminder_date=d.get("last_reminder_date"),
            last_test_warn_date=d.get("last_test_warn_date"),
        )

# user_id -> Progress
USERS: Dict[int, Progress] = {}

def load_storage():
    if os.path.exists(STORAGE_FILE):
        try:
            with open(STORAGE_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            for uid, data in raw.items():
                USERS[int(uid)] = Progress.from_json(data)
        except Exception as e:
            print("Storage load error:", e)

def save_storage():
    try:
        data = {str(uid): USERS[uid].to_json() for uid in USERS}
        tmp = STORAGE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STORAGE_FILE)
    except Exception as e:
        print("Storage save error:", e)

load_storage()

# ------------------------ Вспомогалки ------------------------

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🆕 Я новичок", callback_data="role:novice"),
         InlineKeyboardButton(text="🛩 Я летник", callback_data="role:letnik")],
        [InlineKeyboardButton(text="📚 Гайды", callback_data="guides:menu")],
        [InlineKeyboardButton(text="📊 Мой прогресс", callback_data="progress:me")],
        [InlineKeyboardButton(text="✅ Я выполнил задание", callback_data="task:done")],
    ])

def kb_subjects() -> InlineKeyboardMarkup:
    rows = []
    row = []
    for i, subj in enumerate(SUBJECTS, 1):
        row.append(InlineKeyboardButton(text=subj, callback_data=f"subject:{subj}"))
        if i % 2 == 0:
            rows.append(row); row = []
    if row: rows.append(row)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_guides_menu(role: str) -> InlineKeyboardMarkup:
    rows = []
    if role == "novice":
        rows.append([InlineKeyboardButton(text="📬 Отправить следующий гайд по расписанию", callback_data="guides:next")])
    else:
        rows.append([InlineKeyboardButton(text="📖 Открыть весь каталог", callback_data="guides:all")])
    rows.append([InlineKeyboardButton(text="📂 Категории", callback_data="guides:cats")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_categories() -> InlineKeyboardMarkup:
    rows = []
    for name in GUIDES_LIBRARY.keys():
        rows.append([InlineKeyboardButton(text=name, callback_data=f"cat:{name}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="guides:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_mark_read(idx: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📗 Отметить как прочитанное", callback_data=f"read:{idx}")],
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="home")]
    ])

def kb_admin() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📈 Общая статистика", callback_data="admin:stats")],
        [InlineKeyboardButton(text="🔔 Принудительно: напомнить новичкам", callback_data="admin:remind_now")],
        [InlineKeyboardButton(text="🔔 Принудительно: предупредить летников", callback_data="admin:test_warn_now")],
        [InlineKeyboardButton(text="🧹 Сброс прогресса (user id)", callback_data="admin:reset_hint")]
    ])

def now_local() -> datetime:
    return datetime.now(TZ)

def today_str() -> str:
    return now_local().strftime("%Y-%m-%d")

# ------------------------ Состояния ------------------------

class AdminReset(StatesGroup):
    waiting_for_user_id = State()

# ------------------------ Хэндлеры ------------------------

@router.message(CommandStart())
async def start_cmd(m: Message, state: FSMContext):
    USERS.setdefault(m.from_user.id, Progress(role="novice", guides_read=set()))
    save_storage()
    await m.answer(
        "привет! я бот-куратор.\n\nвыбери, кто ты:",
        reply_markup=kb_main()
    )

@router.message(Command("admin"))
async def admin_cmd(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    await m.answer("Админ-панель:", reply_markup=kb_admin())

@router.callback_query(F.data == "home")
async def home_cb(c: CallbackQuery, state: FSMContext):
    await c.message.edit_text("Главное меню:", reply_markup=kb_main())
    await c.answer()

@router.callback_query(F.data.startswith("role:"))
async def set_role(c: CallbackQuery, state: FSMContext):
    role = c.data.split(":")[1]
    p = USERS.setdefault(c.from_user.id, Progress(role="novice", guides_read=set()))
    p.role = role
    save_storage()
    if role == "novice":
        await c.message.edit_text(
            "Окей, ты <b>новичок</b>.\nВыбери предмет:",
            reply_markup=kb_subjects()
        )
    else:
        await c.message.edit_text(
            "Окей, ты <b>летник</b>.\nВыбери предмет:",
            reply_markup=kb_subjects()
        )
    await c.answer("Роль сохранена")

@router.callback_query(F.data.startswith("subject:"))
async def set_subject(c: CallbackQuery, state: FSMContext):
    subj = c.data.split(":", 1)[1]
    p = USERS.setdefault(c.from_user.id, Progress(role="novice", guides_read=set()))
    p.subject = subj
    save_storage()
    await c.message.edit_text(
        f"Предмет: <b>{subj}</b> сохранён.\nЧто дальше?",
        reply_markup=kb_main()
    )
    await c.answer("Предмет выбран")

@router.callback_query(F.data == "guides:menu")
async def guides_menu(c: CallbackQuery, state: FSMContext):
    p = USERS.setdefault(c.from_user.id, Progress(role="novice", guides_read=set()))
    role = p.role
    await c.message.edit_text(
        "Меню гайдов:",
        reply_markup=kb_guides_menu(role)
    )
    await c.answer()

@router.callback_query(F.data == "guides:cats")
async def guides_cats(c: CallbackQuery, state: FSMContext):
    await c.message.edit_text("Категории гайдов:", reply_markup=kb_categories())
    await c.answer()

@router.callback_query(F.data.startswith("cat:"))
async def show_category(c: CallbackQuery, state: FSMContext):
    name = c.data.split(":",1)[1]
    items = GUIDES_LIBRARY.get(name, [])
    if not items:
        await c.answer("Пока пусто", show_alert=True); return
    text = [f"<b>{name}</b>:"]
    base_index = 0
    # найдём смещение этой категории в плоском списке
    offset = 0
    for k, v in GUIDES_LIBRARY.items():
        if k == name:
            base_index = offset
            break
        offset += len(v)
    for i, g in enumerate(items):
        idx = base_index + i
        mark = "✅" if idx in USERS.setdefault(c.from_user.id, Progress(role="novice", guides_read=set())).guides_read else "⬜"
        text.append(f"{mark} {g}")
    await c.message.edit_text("\n".join(text), reply_markup=kb_categories())
    await c.answer()

@router.callback_query(F.data == "guides:all")
async def letnik_all_guides(c: CallbackQuery, state: FSMContext):
    p = USERS.setdefault(c.from_user.id, Progress(role="letnik", guides_read=set()))
    if p.role != "letnik":
        await c.answer("Эта кнопка для летников", show_alert=True); return
    chunks = []
    chunk = []
    for i, g in enumerate(ALL_GUIDES_FLAT):
        chunk.append((i, g))
        if len(chunk) == 4:
            chunks.append(chunk); chunk = []
    if chunk: chunks.append(chunk)
    await c.message.edit_text("Каталог (листай дальше):")
    for chunk in chunks:
        for idx, g in chunk:
            try:
                await c.message.answer(f"• {g}", reply_markup=kb_mark_read(idx))
            except TelegramBadRequest:
                await asyncio.sleep(0.4)
    await c.answer("Отправил все гайды")

@router.callback_query(F.data == "guides:next")
async def novice_next_guide(c: CallbackQuery, state: FSMContext):
    p = USERS.setdefault(c.from_user.id, Progress(role="novice", guides_read=set()))
    if p.role != "novice":
        await c.answer("Эта кнопка для новичков", show_alert=True); return
    idx = p.next_guide_index
    if idx >= len(ALL_GUIDES_FLAT):
        await c.answer("Все гайды уже выданы!", show_alert=True); return
    guide = ALL_GUIDES_FLAT[idx]
    await c.message.answer(f"📬 Твой следующий гайд:\n\n{guide}", reply_markup=kb_mark_read(idx))
    # следующий по расписанию (но вручную тоже можно попросить)
    await c.answer("Отправил следующий гайд")

@router.callback_query(F.data.startswith("read:"))
async def mark_read(c: CallbackQuery, state: FSMContext):
    idx = int(c.data.split(":")[1])
    p = USERS.setdefault(c.from_user.id, Progress(role="novice", guides_read=set()))
    p.guides_read.add(idx)
    # Если это был текущий для новичка — сдвинем указатель
    if p.role == "novice" and idx == p.next_guide_index:
        p.next_guide_index += 1
    save_storage()
    await c.answer("Отмечено ✅")
    try:
        await c.message.edit_reply_markup(reply_markup=None)
    except:
        pass

@router.callback_query(F.data == "progress:me")
async def my_progress(c: CallbackQuery, state: FSMContext):
    p = USERS.setdefault(c.from_user.id, Progress(role="novice", guides_read=set()))
    total = len(ALL_GUIDES_FLAT)
    read = len(p.guides_read)
    txt = [
        f"📊 <b>Твой прогресс</b>",
        f"Роль: <b>{'Новичок' if p.role=='novice' else 'Летник'}</b>",
        f"Предмет: <b>{p.subject or 'не выбран'}</b>",
        f"Гайды: <b>{read}/{total}</b>",
        f"Заданий выполнено: <b>{p.tasks_done}</b>",
    ]
    await c.message.edit_text("\n".join(txt), reply_markup=kb_main())
    await c.answer()

@router.callback_query(F.data == "task:done")
async def task_done(c: CallbackQuery, state: FSMContext):
    p = USERS.setdefault(c.from_user.id, Progress(role="novice", guides_read=set()))
    p.tasks_done += 1
    save_storage()
    await c.answer("Отлично! Я отметил ✅")
    await c.message.edit_text("Задание принято. Молодец!", reply_markup=kb_main())

# ------------------------ Админ ------------------------

@router.callback_query(F.data == "admin:stats")
async def admin_stats(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return
    total = len(USERS)
    novices = sum(1 for p in USERS.values() if p.role == "novice")
    letniki = total - novices
    txt = [f"👤 Пользователей: {total}",
           f"— Новички: {novices}",
           f"— Летники: {letniki}",
           "-------------------------"]
    # короткая выдача по 10
    for uid, p in list(USERS.items())[:10]:
        txt.append(f"{uid}: {p.role}, subj={p.subject}, guides={len(p.guides_read)}/{len(ALL_GUIDES_FLAT)}, tasks={p.tasks_done}")
    await c.message.edit_text("\n".join(txt), reply_markup=kb_admin())
    await c.answer()

@router.callback_query(F.data == "admin:remind_now")
async def admin_remind_now(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id): return
    await send_novice_reminders(force=True)
    await c.answer("Напоминания отправлены")

@router.callback_query(F.data == "admin:test_warn_now")
async def admin_warn_now(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id): return
    await send_letnik_test_warnings(force=True)
    await c.answer("Предупреждения отправлены")

@router.callback_query(F.data == "admin:reset_hint")
async def admin_reset_hint(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id): return
    await c.message.edit_text("Введи user_id для сброса. /cancel — отмена.")
    await state.set_state(AdminReset.waiting_for_user_id)
    await c.answer()

@router.message(StateFilter(AdminReset.waiting_for_user_id))
async def admin_reset_do(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    if m.text.strip().lower() == "/cancel":
        await state.clear()
        await m.answer("Отмена.", reply_markup=kb_admin())
        return
    try:
        uid = int(m.text.strip())
        if uid in USERS:
            USERS.pop(uid)
            save_storage()
            await m.answer(f"Сброшено для {uid}", reply_markup=kb_admin())
        else:
            await m.answer("Не найден такой user_id", reply_markup=kb_admin())
    except:
        await m.answer("Нужно число user_id", reply_markup=kb_admin())
    await state.clear()

# ------------------------ Планировщики ------------------------

async def send_guide_to_novices_if_morning():
    """
    Каждый день около 08:00 МСК — отправляем следующий гайд новичкам,
    которые ещё не получили все.
    Если бот перезапускался и час позже — всё равно отправим один раз при старте, если > 08:00.
    """
    local_now = now_local()
    target = local_now.replace(hour=8, minute=0, second=0, microsecond=0)
    if local_now > target:
        # уже позже 8 — сработаем один раз прямо сейчас
        await _dispatch_next_guides_to_all()
        return
    # иначе — подождём до 8:00
    await asyncio.sleep((target - local_now).total_seconds())
    await _dispatch_next_guides_to_all()

async def _dispatch_next_guides_to_all():
    for uid, p in USERS.items():
        if p.role != "novice":
            continue
        if p.next_guide_index >= len(ALL_GUIDES_FLAT):
            continue
        try:
            guide = ALL_GUIDES_FLAT[p.next_guide_index]
            await bot.send_message(uid, f"🌅 Доброе утро!\nТвой гайд на сегодня:\n\n{guide}", reply_markup=kb_mark_read(p.next_guide_index))
        except:
            continue
    save_storage()

async def send_novice_reminders(force=False):
    """
    Ежедневно ~21:30 МСК напоминаем новичкам про задание до 22:00.
    Если force=True — шлём вне расписания.
    """
    today = today_str()
    for uid, p in USERS.items():
        if p.role != "novice":
            continue
        if not force and p.last_reminder_date == today:
            continue
        try:
            await bot.send_message(uid, "⏰ Напоминание: не забудь сдать задание до <b>22:00 (МСК)</b>!")
            p.last_reminder_date = today
        except:
            pass
    save_storage()

async def send_letnik_test_warnings(force=False):
    """
    Летникам — напоминание: до теста 24 часа (условная заглушка).
    Шлём раз в день ~12:00 МСК.
    """
    today = today_str()
    for uid, p in USERS.items():
        if p.role != "letnik":
            continue
        if not force and p.last_test_warn_date == today:
            continue
        try:
            await bot.send_message(uid, "🧪 Напоминание для летника: на тест осталось ~24 часа. Успей пройти!")
            p.last_test_warn_date = today
        except:
            pass
    save_storage()

async def scheduler_loop():
    """
    Бесконечный цикл расписаний:
    - утром отправка гайдов новичкам
    - в 21:30 напоминание новичкам
    - в 12:00 предупреждение летникам
    На бесплатном Render, если вебсервис «уснёт», задачи остановятся.
    Но при «пробуждении» цикл продолжит работу.
    """
    # сразу один «утренний» прогон, если бот пришёл в строй после 8:00
    asyncio.create_task(send_guide_to_novices_if_morning())

    while True:
        now = now_local().time()
        # 12:00 — летники
        if time(12, 0) <= now <= time(12, 2):
            await send_letnik_test_warnings()
        # 21:30 — новички
        if time(21, 30) <= now <= time(21, 32):
            await send_novice_reminders()
        await asyncio.sleep(60)  # раз в минуту проверяем

# ------------------------ AIOHTTP сервер (webhook) ------------------------

# Путь вебхука будет вида /tg/<WEBHOOK_SECRET>
WEBHOOK_PATH = f"/tg/{WEBHOOK_SECRET}"
WEBHOOK_URL = f"{PUBLIC_URL}{WEBHOOK_PATH}"

async def handle_webhook(request: web.Request):
    data = await request.json()
    update = dp.feed_webhook_update(bot, data)
    return web.Response()

async def on_startup(app: web.Application):
    # ставим вебхук
    try:
        await bot.set_webhook(WEBHOOK_URL, drop_pending_updates=True)
        print("Webhook set:", WEBHOOK_URL)
    except Exception as e:
        print("Webhook set error:", e)
    # стартуем планировщик
    app['scheduler'] = asyncio.create_task(scheduler_loop())

async def on_shutdown(app: web.Application):
    try:
        await bot.delete_webhook(drop_pending_updates=False)
    except:
        pass
    if task := app.get('scheduler'):
        task.cancel()

def create_app() -> web.Application:
    app = web.Application()
    app.router.add_post(WEBHOOK_PATH, handle_webhook)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    return app

app = create_app()

# Локальный запуск (не для Render). На Render будет uvicorn main:app
if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")))


