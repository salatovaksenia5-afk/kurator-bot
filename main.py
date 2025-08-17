import os
import json
import asyncio
import gspread
from google.oauth2.service_account import Credentials
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, time, timezone
from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton
)

# === Константы ===
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("Нет BOT_TOKEN. Задай переменную окружения BOT_TOKEN на Render.")

ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or "0")
ACCESS_CODE = os.getenv("ACCESS_CODE", "летл2025").strip()
TIMEZONE = timezone(timedelta(hours=3))  # МСК
REMIND_HOUR = 14  # напоминание новичкам в 14:00
DEADLINE_HOUR = 22  # дедлайн сдачи задания в 22:00
PORT = int(os.getenv("PORT", "10000"))
DATA_DIR = "data"
USERS_FILE = os.path.join(DATA_DIR, "users.json")
GUIDES_FILE = os.path.join(DATA_DIR, "guides.json")
os.makedirs(DATA_DIR, exist_ok=True)

# === Google Sheets ===
SHEET_ID = os.getenv("SHEET_ID", "17zqwZ0MNNJWjzVfmBluLXyRGt-ogC14QxtXhTfEPsNU/edit?hl=ru&gid=0#gid=0").strip()
SHEET_TAB = os.getenv("SHEET_TAB", "Лист1").strip()
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS", "").strip()
CHAT_LINK_NEWBIE = os.getenv("CHAT_LINK_NEWBIE", "").strip()

def _read_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def _write_json(path: str, payload):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def load_guides():
    data = _read_json(GUIDES_FILE, {})
    if not data:
        data = {
            "newbie": [
                {"id": "n1", "title": "Гайд 1: Основы и этика", "url": "https://example.com/g1"},
                {"id": "n2", "title": "Гайд 2: Техническая часть", "url": "https://example.com/g2"},
                {"id": "n3", "title": "Гайд 3: Предметы", "url": "https://example.com/g3"},
            ],
            "letnik": [
                {"id": "l1", "title": "Гайд1: Основы и этика", "url": "https://example.com/la"},
                {"id": "l2", "title": "Гайд 2: Техническая часть", "url": "https://example.com/lb"},
                {"id": "l3", "title": "Гайд 3: Предметы", "url": "https://example.com/lc"},
            ],
            "subjects": [
                "информатика", "физика", "русский язык",
                "обществознание", "биология", "химия",
                "профильная математика"
            ],
            "tasks_third_by_subject": {
                "информатика": "Составь мини-конспект алгоритма решения типовой задачи ЕГЭ и запиши 3 примера.",
                "физика": "Разбери задачу на законы Ньютона: условия, формулы, решение и ответ.",
                "русский язык": "Напиши план сочинения по предложенной теме + 3 аргумента.",
                "обществознание": "Дай определения 5-ти ключевых терминов из темы и приведи примеры.",
                "биология": "Набросай схему процесса (фотосинтез/клеточное дыхание) и поясни этапы.",
                "химия": "Составь 3 уравнения реакций по теме и проговори правила уравнивания.",
                "профильная математика": "Реши параметр"
            }
        }
        _write_json(GUIDES_FILE, data)
    return data

GUIDES = load_guides()

@dataclass
class Progress:
    role: str = "newbie"
    name: str = None
    subject: str = None
    allow_letnik: bool = False
    guide_index: int = 0
    last_guide_sent_at: str = None
    has_read_today: bool = False
    task_done_dates: list = None
    created_at: str = None

    def to_dict(self):
        d = asdict(self)
        if d["task_done_dates"] is None:
            d["task_done_dates"] = []
        return d

def now_msk() -> datetime:
    return datetime.now(TIMEZONE)

def today_msk() -> datetime.date:
    return now_msk().date()

def iso(dt: datetime) -> str:
    return dt.astimezone(TIMEZONE).isoformat()

def is_time_before_deadline(dt: datetime) -> bool:
    return dt.time() < time(DEADLINE_HOUR, 0)

def load_users() -> dict[str, dict]:
    data = _read_json(USERS_FILE, {})
    changed = False
    for uid, raw in list(data.items()):
        if "task_done_dates" not in raw:
            raw["task_done_dates"] = []
            changed = True
        if "has_read_today" not in raw:
            raw["has_read_today"] = False
            changed = True
        if "allow_letnik" not in raw:
            raw["allow_letnik"] = False
            changed = True
        if "created_at" not in raw:
            raw["created_at"] = iso(now_msk())
            changed = True
    if changed:
        _write_json(USERS_FILE, data)
    return data

def save_users(data: dict):
    _write_json(USERS_FILE, data)

USERS: dict[str, dict] = load_users()

def get_user(uid: int) -> Progress:
    key = str(uid)
    if key not in USERS:
        USERS[key] = Progress(created_at=iso(now_msk())).to_dict()
        save_users(USERS)
    p = Progress(**USERS[key])
    if p.task_done_dates is None:
        p.task_done_dates = []
    return p

def put_user(uid: int, p: Progress):
    USERS[str(uid)] = p.to_dict()
    save_users(USERS)

def kb_role() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Я новичок"), KeyboardButton(text="Я летник")]
        ],
        resize_keyboard=True
    )

def kb_main_newbie(p: Progress) -> InlineKeyboardMarkup:
    rows = []
    rows.append([InlineKeyboardButton(text="📘 Выбрать предмет", callback_data="subject:menu")])
    if p.subject:
        rows.append([InlineKeyboardButton(text="📖 Открыть гайд", callback_data="newbie:open_guide")])
        rows.append([InlineKeyboardButton(text="✅ Я выполнил задание", callback_data="newbie:task_done")])
    rows.append([InlineKeyboardButton(text="📊 Прогресс", callback_data="progress:me")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_main_letnik(p: Progress) -> InlineKeyboardMarkup:
    rows = []
    rows.append([InlineKeyboardButton(text="📘 Выбрать предмет", callback_data="subject:menu")])
    if p.allow_letnik:
        rows.append([InlineKeyboardButton(text="⚡ Все гайды летника", callback_data="letnik:all")])
    else:
        rows.append([InlineKeyboardButton(text="🔒 Ввести код доступа", callback_data="letnik:code")])
    rows.append([InlineKeyboardButton(text="📊 Прогресс", callback_data="progress:me")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_subjects() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=s.title(), callback_data=f"subject:set:{s}")]
            for s in GUIDES["subjects"]
        ]
    )

def kb_read_confirm() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📖 Я прочитал(а) гайд", callback_data="newbie:read_confirm")]
        ]
    )

class RegStates(StatesGroup):
    waiting_role = State()
    waiting_name = State()
    waiting_subject = State()
    waiting_letnik_code = State()

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
router = Router()
dp.include_router(router)

@dp.message(F.text)
async def capture_full_name(message: Message):
    u = get_user(message.from_user.id)
    if not u or not getattr(u, "awaiting_full_name", False):
        return
    full = message.text.strip()
    if len(full.split()) < 2:
        await message.answer("Нужно указать фамилию и имя. Пример: <i>Иванов Иван</i>")
        return
    u.full_name = full
    u.awaiting_full_name = False
    put_user(message.from_user.id, u)

    try:
        gs_set(message.from_user.id, {"ФИ": full})
    except Exception:
        pass

    await message.answer("Отлично! Теперь выбери предмет:", reply_markup=kb_subjects())

@dp.callback_query(F.data.startswith("subject:set:"))
async def subject_set(cb: CallbackQuery):
    s = cb.data.split(":")[2]
    u = get_user(cb.from_user.id)
    u.subject = s
    put_user(cb.from_user.id, u)

    try:
        gs_set(cb.from_user.id, {"Предмет": s})
    except Exception:
        pass

    await cb.message.answer(f"📘 Предмет сохранён: <b>{s}</b>")

    if u.role == "newbie" and CHAT_LINK_NEWBIE and not getattr(u, "hr_chat_link_sent", False):
        u.hr_chat_link_sent = True
        put_user(cb.from_user.id, u)
        try:
            gs_set(cb.from_user.id, {"В чате новичков": "ссылка отправлена"})
        except Exception:
            pass
        await cb.message.answer(
            f"👋 Добро пожаловать! Вступи в чат новичков по ссылке:\n{CHAT_LINK_NEWBIE}\n\n"
            f"После вступления тебе начнут приходить гайды (после 08:00 МСК)."
        )

    await cb.answer()

@dp.callback_query(F.data == "task:done")
async def task_done(cb: CallbackQuery):
    u = get_user(cb.from_user.id)
    role = u.role
    items = GUIDES["newbie"] if role == "newbie" else GUIDES["letnik"]

    idx = u.guide_index
    guide = None
    if role == "newbie":
        if idx < len(items):
            guide = items[idx]
    else:
        guide = items[0] if items else None

    if not guide:
        await cb.message.answer("Пока нечего отмечать.")
        await cb.answer()
        return

    prog = u.__dict__.setdefault("progress", {})
    gstat = prog.setdefault(guide["id"], {"read": True, "task_done": False})
    gstat["read"] = True
    gstat["task_done"] = True
    put_user(cb.from_user.id, u)

    await cb.message.answer(f"✅ Задание по «{guide['title']}» отмечено как выполненное!")

    if role == "newbie":
        guide_num = u.guide_index + 1
        try:
            gs_set(cb.from_user.id, {f"Задание {guide_num}": "выполнено"})
        except Exception:
            pass
    else:
        try:
            gs_set(cb.from_user.id, {"Статус": "Тест у летника выполняется"})
        except Exception:
            pass

    await cb.answer()

@dp.callback_query(F.data == "final_test")
async def process_final_test(cb: CallbackQuery):
    u = get_user(cb.from_user.id)
    uid = cb.from_user.id
    role = u.role

    if role != "newbie":
        await cb.answer("Финальный тест — для новичков.", show_alert=True)
        return

    u.final_test_done = True
    u.finished_at = datetime.now(TIMEZONE).isoformat()
    put_user(uid, u)

    try:
        gs_set(uid, {
            "Финальный тест": "✓",
            "Дата окончания": datetime.now(TIMEZONE).strftime("%Y-%m-%d"),
            "Статус": "Завершил обучение"
        })
    except Exception:
        pass

    await cb.message.answer(
        "🎓 <b>Поздравляем!</b>\n"
        "Ты прошёл обучение куратора. Добро пожаловать в команду! 🥳\n\n"
        "Свяжись со старшим куратором для следующих шагов."
    )
    await cb.answer()

@router.callback_query(lambda c: c.data.startswith("test_"))
async def process_test(callback_query: types.CallbackQuery):
    subject = callback_query.data.split("_", 1)[1]
    
    await callback_query.message.answer(
        f"📘 Тест по предмету {subject}.\n"
        f"Скоро здесь будут вопросы!"
    )
    await bot.answer_callback_query(callback_query.id)

@router.callback_query(lambda c: c.data == "final_test")
async def process_final_test(callback_query: types.CallbackQuery):
    await callback_query.message.answer(
        "🎓 Финальный тест!\n"
        "Здесь будут заключительные вопросы для проверки знаний."
    )
    await bot.answer_callback_query(callback_query.id)

async def send_newbie_today_guide(uid: int, p: Progress):
    items = GUIDES["newbie"]
    idx = p.guide_index
    if idx >= len(items):
        await bot.send_message(uid, "🎉 Ты прошёл(ла) все новичковые гайды!")
        return

    g = items[idx]
    p.last_guide_sent_at = iso(now_msk())
    p.has_read_today = False
    put_user(uid, p)

    text = (
        f"📘 Сегодняшний гайд:\n<b>{g['title']}</b>\n{g['url']}\n\n"
        f"После прочтения нажми кнопку ниже. Задание откроется после подтверждения.\n"
        f"Дедлайн сдачи — <b>{DEADLINE_HOUR}:00</b> по МСК."
    )
    await bot.send_message(uid, text, reply_markup=kb_read_confirm())

def _today_iso_date() -> str:
    return today_msk().isoformat()

def _third_guide_task_for_subject(subject: str) -> str:
    tasks = GUIDES.get("tasks_third_by_subject", {})
    return tasks.get(subject or "", "Индивидуальное задание для 3-го гайда по твоему предмету.")

async def send_newbie_task(uid: int, p: Progress):
    idx = p.guide_index
    items = GUIDES["newbie"]
    if idx >= len(items):
        await bot.send_message(uid, "🎉 Новичковые гайды уже все пройдены.")
        return

    g = items[idx]
    if g["id"] == "n3":
        task_text = _third_guide_task_for_subject(p.subject or "")
    else:
        task_text = "Кратко законспектируй основные мысли гайда и выполни предложенное упражнение."

    now = now_msk()
    if not is_time_before_deadline(now):
        deadline_note = "⚠️ Дедлайн уже прошёл. Кнопка сдачи недоступна."
    else:
        deadline_note = f"Сдать можно до <b>{DEADLINE_HOUR}:00</b> МСК."

    await bot.send_message(
        uid,
        f"📝 Задание к «{g['title']}»:\n\n{task_text}\n\n{deadline_note}"
    )

def can_submit_now() -> bool:
    return is_time_before_deadline(now_msk())

@router.message(CommandStart())
async def cmd_start(m: Message, state: FSMContext):
    p = get_user(m.from_user.id)
    await state.clear()
    await state.set_state(RegStates.waiting_role)
    await m.answer(
        "Привет! Я бот-куратор.\n\nВыбери свою роль:",
        reply_markup=kb_role()
    )

@router.message(RegStates.waiting_role, F.text.lower().in_({"я новичок", "я летник"}))
async def choose_role(m: Message, state: FSMContext):
    p = get_user(m.from_user.id)
    is_newbie = (m.text.lower() == "я новичок")
    p.role = "newbie" if is_newbie else "letnik"
    put_user(m.from_user.id, p)

    await state.set_state(RegStates.waiting_name)
    await m.answer("Отправь, пожалуйста, <b>фамилию и имя</b> одной строкой (например: Иванова Анна).",
                   reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Отмена")]], resize_keyboard=True))

@router.message(RegStates.waiting_name, F.text.len() >= 3)
async def take_name(m: Message, state: FSMContext):
    text = " ".join(m.text.strip().split())
    p = get_user(m.from_user.id)
    p.name = text
    put_user(m.from_user.id, p)

    await state.set_state(RegStates.waiting_subject)
    await m.answer(
        "Отлично! Теперь выбери предмет:",
        reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Выбрать предмет")]], resize_keyboard=True)
    )
    await m.answer("👇 Нажми, чтобы выбрать предмет:", reply_markup=None)
    await m.answer("Выбор предмета:", reply_markup=kb_subjects())

@router.callback_query(F.data.startswith("subject:set:"))
async def subject_set(cb: CallbackQuery, state: FSMContext):
    s = cb.data.split(":", 2)[2]
    p = get_user(cb.from_user.id)
    p.subject = s
    put_user(cb.from_user.id, p)

    await cb.answer("Сохранено")
    if p.role == "newbie":
        await cb.message.answer(
            f"📘 Предмет: <b>{s.title()}</b> сохранён.\n"
            f"Кнопки для чтения гайда и сдачи задания активированы.",
            reply_markup=kb_main_newbie(p)
        )
        
        if CHAT_LINK_NEWBIE and not p.hr_chat_link_sent:
            p.hr_chat_link_sent = True
            put_user(cb.from_user.id, p)
            try:
                gs_set(cb.from_user.id, {"В чате новичков": "ссылка отправлена"})
            except Exception:
                pass
            await cb.message.answer(
                f"👋 Добро пожаловать! Вступи в чат новичков по ссылке:\n{CHAT_LINK_NEWBIE}\n\n"
                f"После вступления тебе начнут приходить гайды (после 08:00 МСК)."
            )

        await cb.message.answer(
            "Гайды для новичков приходят каждый день <b>после 08:00</b> по МСК.\n"
            "Если бот был перезапущен позже — гайд придёт сразу после запуска."
        )
    else:
        await cb.message.answer(
            f"📘 Предмет: <b>{s.title()}</b> сохранён.",
            reply_markup=kb_main_letnik(p)
        )

@router.callback_query(F.data == "subject:menu")
async def subject_menu(cb: CallbackQuery):
    await cb.message.answer("Выбор предмета:", reply_markup=kb_subjects())
    await cb.answer()

@router.callback_query(F.data == "letnik:code")
async def ask_letnik_code(cb: CallbackQuery, state: FSMContext):
    p = get_user(cb.from_user.id)
    if p.allow_letnik:
        await cb.answer("Доступ уже открыт")
        return
    await state.set_state(RegStates.waiting_letnik_code)
    await cb.message.answer("Введи код доступа для летников:")
    await cb.answer()

@router.message(RegStates.waiting_letnik_code)
async def check_letnik_code(m: Message, state: FSMContext):
    p = get_user(m.from_user.id)
    code = (m.text or "").strip()
    if code == ACCESS_CODE:
        p.allow_letnik = True
        put_user(m.from_user.id, p)
        await state.clear()
        await m.answer("✅ Код верный. Доступ к гайдам летника открыт.", reply_markup=None)
        await m.answer("Меню:", reply_markup=kb_main_letnik(p))
    else:
        await m.answer("❌ Неверный код. Попробуй ещё раз или нажми /start для перезапуска.")

@router.callback_query(F.data == "letnik:all")
async def letnik_all(cb: CallbackQuery):
    p = get_user(cb.from_user.id)
    if not p.allow_letnik:
        await cb.answer("Нет доступа. Введи код.", show_alert=True)
        return
    items = GUIDES["letnik"]
    text = "⚡ Все гайды для летников:\n\n" + "\n".join([f"• <b>{g['title']}</b> — {g['url']}" for g in items])
    await cb.message.answer(text)
    await cb.answer()

@router.callback_query(F.data == "newbie:open_guide")
async def newbie_open_guide(cb: CallbackQuery):
    p = get_user(cb.from_user.id)
    if p.role != "newbie":
        await cb.answer("Доступно только новичкам.", show_alert=True)
        return
    if not p.subject:
        await cb.answer("Сначала выбери предмет.", show_alert=True)
        return

    items = GUIDES["newbie"]
    idx = p.guide_index
    if idx >= len(items):
        await cb.message.answer("🎉 Ты уже прошёл(ла) все новичковые гайды!")
        await cb.answer()
        return

    g = items[idx]
    await cb.message.answer(
        f"📘 Текущий гайд:\n<b>{g['title']}</b>\n{g['url']}\n\n"
        f"После прочтения нажми кнопку ниже, чтобы открыть задание.",
        reply_markup=kb_read_confirm()
    )
    await cb.answer()

@router.callback_query(F.data == "newbie:read_confirm")
async def newbie_mark_read(cb: CallbackQuery):
    p = get_user(cb.from_user.id)
    if p.role != "newbie":
        await cb.answer("Только для новичков")
        return
    p.has_read_today = True
    put_user(cb.from_user.id, p)
    await cb.message.answer("Отлично! Гайд помечен как прочитанный. Вот задание:")
    await send_newbie_task(cb.from_user.id, p)
    await cb.answer()

@router.callback_query(F.data == "newbie:task_done")
async def newbie_task_done(cb: CallbackQuery):
    p = get_user(cb.from_user.id)
    if p.role != "newbie":
        await cb.answer("Только для новичков")
        return
    if not p.subject:
        await cb.answer("Сначала выбери предмет.", show_alert=True)
        return

    if not can_submit_now():
        await cb.answer("После 22:00 сдача недоступна 😔", show_alert=True)
        return

    if not p.has_read_today:
        await cb.answer("Сначала отметь, что прочитал(а) гайд.", show_alert=True)
        return

    tiso = _today_iso_date()
    if tiso not in p.task_done_dates:
        p.task_done_dates.append(tiso)

    p.guide_index += 1
    p.has_read_today = False
    put_user(cb.from_user.id, p)

    await cb.message.answer("✅ Задание принято! Новый гайд придёт после 08:00 по МСК.")
    await cb.answer()

@router.callback_query(F.data == "progress:me")
async def progress_me(cb: CallbackQuery):
    p = get_user(cb.from_user.id)
    role = p.role
    subject = p.subject or "не выбран"
    if role == "newbie":
        total = len(GUIDES["newbie"])
        done = min(p.guide_index, total)
        text = (
            "📊 Твой прогресс:\n\n"
            f"Роль: новичок\n"
            f"Предмет: <b>{subject}</b>\n"
            f"Пройдено гайдов: <b>{done}</b> из <b>{total}</b>\n"
            f"Сдано дней: <b>{len(p.task_done_dates)}</b>\n"
        )
    else:
        text = (
            "📊 Твой прогресс:\n\n"
            f"Роль: летник\n"
            f"Предмет: <b>{subject}</b>\n"
            f"Доступ к гайдам летника: {'да' if p.allow_letnik else 'нет'}\n"
        )
    await cb.message.answer(text)
    await cb.answer()

@router.message(Command("admin"))
async def admin_stats(m: Message):
    if ADMIN_ID and m.from_user.id != ADMIN_ID:
        return
    total = len(USERS)
    newbies = sum(1 for u in USERS.values() if u.get("role") == "newbie")
    letniki = sum(1 for u in USERS.values() if u.get("role") == "letnik")
    lines = [
        f"👥 Всего пользователей: {total}",
        f"🟢 Новичков: {newbies}",
        f"🟠 Летников: {letniki}",
        "",
        "Последние 10 регистраций:"
    ]
    last = sorted(USERS.items(), key=lambda kv: kv[1].get("created_at", ""), reverse=True)[:10]
    for uid, u in last:
        lines.append(f"{uid}: {u.get('name') or '—'} | {u.get('role')} | subj:{u.get('subject') or '—'} | idx:{u.get('guide_index', 0)}")
    await m.answer("\n".join(lines))

async def scheduler_loop():
    await asyncio.sleep(2)
    now = now_msk()
    if now.time() >= time(8, 0):
        for uid, raw in list(USERS.items()):
            p = Progress(**raw)
            if p.role != "newbie":
                continue
            last_date = None
            if p.last_guide_sent_at:
                try:
                    last_date = datetime.fromisoformat(p.last_guide_sent_at).astimezone(TIMEZONE).date()
                except Exception:
                    last_date = None
            if last_date != today_msk():
                try:
                    await send_newbie_today_guide(int(uid), p)
                    await asyncio.sleep(0.1)
                except Exception:
                    pass

    while True:
        try:
            now = now_msk()

            if now.time().hour == 8 and now.time().minute == 0:
                for uid, raw in list(USERS.items()):
                    p = Progress(**raw)
                    if p.role != "newbie":
                        continue
                    last_date = None
                    if p.last_guide_sent_at:
                        try:
                            last_date = datetime.fromisoformat(p.last_guide_sent_at).astimezone(TIMEZONE).date()
                        except Exception:
                            last_date = None
                    if last_date == today_msk():
                        continue
                    await send_newbie_today_guide(int(uid), p)
                    await asyncio.sleep(0.1)

            if now.time().hour == REMIND_HOUR and now.time().minute == 0:
                for uid, raw in list(USERS.items()):
                    p = Progress(**raw)
                    if p.role != "newbie":
                        continue
                    await bot.send_message(int(uid), f"⏰ Напоминание: сдать сегодняшнее задание до <b>{DEADLINE_HOUR}:00</b> по МСК.")
                    await asyncio.sleep(0.05)

            await asyncio.sleep(60)
        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(5)

async def handle_root(request):
    return web.Response(text="kurator-bot ok")

async def handle_health(request):
    return web.json_response({"status": "ok", "ts": iso(now_msk())})

async def start_web_app():
    app = web.Application()
    app.add_routes([web.get("/", handle_root), web.get("/health", handle_health)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

async def main():
    await start_web_app()
    asyncio.create_task(scheduler_loop())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
# ... [Предыдущий код без изменений до обработчика subject_set] ...

@router.callback_query(F.data.startswith("subject:set:"))
async def subject_set(cb: CallbackQuery, state: FSMContext):
    s = cb.data.split(":", 2)[2]
    p = get_user(cb.from_user.id)
    p.subject = s
    put_user(cb.from_user.id, p)

    try:
        gs_set(cb.from_user.id, {"Предмет": s})
    except Exception:
        pass

    await cb.answer("Сохранено")
    if p.role == "newbie":
        await cb.message.answer(
            f"📘 Предмет: <b>{s.title()}</b> сохранён.\n"
            f"Кнопки для чтения гайда и сдачи задания активированы.",
            reply_markup=kb_main_newbie(p)
        )
        
        if CHAT_LINK_NEWBIE and not p.hr_chat_link_sent:
            p.hr_chat_link_sent = True
            put_user(cb.from_user.id, p)
            try:
                gs_set(cb.from_user.id, {"В чате новичков": "ссылка отправлена"})
            except Exception:
                pass
            
            await cb.message.answer(
                f"👋 Добро пожаловать! Вступи в чат новичков по ссылке:\n{CHAT_LINK_NEWBIE}\n\n"
                f"После вступления тебе начнут приходить гайды (после 08:00 МСК)."
            )
        
        await cb.message.answer(
            "Гайды для новичков приходят каждый день <b>после 08:00</b> по МСК.\n"
            "Если бот был перезапущен позже — гайд придёт сразу после запуска."
        )
    else:
        await cb.message.answer(
            f"📘 Предмет: <b>{s.title()}</b> сохранён.",
            reply_markup=kb_main_letnik(p)
        )

# [Добавленные обработчики из вашего кода]
async def send_newbie_next_guide(uid: int):
    u = get_user(uid)
    if not u or u.role != "newbie":
        return
    idx = u.guide_index
    items = GUIDES["newbie"]
    if idx >= len(items):
        await bot.send_message(uid, "🎉 Все гайды для новичков пройдены! Можно попросить статус летник у руководителя.")
        return
    
    g = items[idx]
    u.last_guide_sent_at = iso(now_msk())
    put_user(uid, u)

    await bot.send_message(
        uid,
        f"📘 Сегодняшний гайд: <b>{g['title']}</b>\nСсылка: {g['url']}\n\n"
        f"Не забудь выполнить задание к 22:00 по МСК."
    )

    try:
        gs_set(uid, {f"Гайд {idx + 1}": "отправлен"})
    except Exception:
        pass

@router.callback_query(F.data == "task:done")
async def task_done(cb: CallbackQuery):
    p = get_user(cb.from_user.id)
    role = p.role
    items = GUIDES["newbie"] if role == "newbie" else GUIDES["letnik"]

    idx = p.guide_index
    guide = None
    if role == "newbie":
        if idx < len(items):
            guide = items[idx]
    else:
        guide = items[0] if items else None

    if not guide:
        await cb.message.answer("Пока нечего отмечать.")
        await cb.answer()
        return

    if not hasattr(p, 'progress'):
        p.progress = {}
    p.progress[guide["id"]] = {"read": True, "task_done": True}
    put_user(cb.from_user.id, p)

    await cb.message.answer(f"✅ Задание по «{guide['title']}» отмечено как выполненное!")

    try:
        if role == "newbie":
            gs_set(cb.from_user.id, {f"Задание {idx + 1}": "выполнено"})
        else:
            gs_set(cb.from_user.id, {"Статус": "Тест у летника выполняется"})
    except Exception:
        pass

    await cb.answer()

@router.callback_query(F.data == "final_test")
async def process_final_test(cb: CallbackQuery):
    p = get_user(cb.from_user.id)
    if p.role != "newbie":
        await cb.answer("Финальный тест — для новичков.", show_alert=True)
        return

    p.final_test_done = True
    p.finished_at = iso(now_msk())
    put_user(cb.from_user.id, p)

    try:
        gs_set(cb.from_user.id, {
            "Финальный тест": "✓",
            "Дата окончания": today_msk().isoformat(),
            "Статус": "Завершил обучение"
        })
    except Exception:
        pass

    await cb.message.answer(
        "🎓 <b>Поздравляем!</b>\n"
        "Ты прошёл обучение куратора. Добро пожаловать в команду! 🥳\n\n"
        "Свяжись со старшим куратором для следующих шагов."
    )
    await cb.answer()

# [Остальной код без изменений]
async def handle_root(request):
    return web.Response(text="kurator-bot ok")

async def handle_health(request):
    return web.json_response({"status": "ok", "ts": iso(now_msk())})

async def start_web_app():
    app = web.Application()
    app.add_routes([web.get("/", handle_root), web.get("/health", handle_health)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

async def main():
    await start_web_app()
    asyncio.create_task(scheduler_loop())
    await dp.start_polling(bot)
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
from aiohttp import web
import asyncio

async def health(request):
    return web.Response(text="OK")

async def start_web_app():
    app = web.Application()
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()

# Запуск и бота, и health-сервера
async def main():
    # Запускаем бота
    bot_task = asyncio.create_task(dp.start_polling(bot))
    # Запускаем сервер для аптайма
    web_task = asyncio.create_task(start_web_app())
    # Ждём оба таска
    await asyncio.gather(bot_task, web_task)

if __name__ == "__main__":
    asyncio.run(main())
    
