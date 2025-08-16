import os
import json
import asyncio
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

# === Google Sheets ===
import gspread
from google.oauth2.service_account import Credentials

# =========================
# НАСТРОЙКИ / КОНСТАНТЫ
# =========================
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

# === Google Sheets настройки ===
SHEET_ID = os.getenv("SHEET_ID", "17zqwZ0MNNJWjzVfmBluLXyRGt-ogC14QxtXhTfEPsNU/edit?hl=ru&gid=0#gid=0").strip()
SHEET_TAB = os.getenv("SHEET_TAB", "Лист1").strip()
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS", "").strip()

# === HR/онбординг ===
CHAT_LINK_NEWBIE = os.getenv("CHAT_LINK_NEWBIE", "").strip()

# ... остальной код без изменений ...

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


def _write_json(path: str, payload):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_guides():
    """
    Структура:
    {
      "newbie": [ {"id":"n1","title":"...","url":"..."},
                  {"id":"n2","title":"...","url":"..."},
                  {"id":"n3","title":"...","url":"..."} ],
      "letnik": [ {"id":"l1","title":"...","url":"..."}, ... ],
      "subjects": ["информатика", "физика", "русский язык", "обществознание", "биология", "химия", "профильная математика"],
      "tasks_third_by_subject": {
          "<subject>": "Текст задания для 3-го гайда этого предмета"
      }
    }
    """
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
                "информатика",
                "физика",
                "русский язык",
                "обществознание",
                "биология",
                "химия",
                "профильная математика"
            ],
            # Задание для 3-го гайда зависит от предмета (примерные заглушки)
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


# --- модели / утилиты
@dataclass
class Progress:
    role: str = "newbie"              # newbie | letnik
    name: str | None = None           # "Фамилия Имя"
    subject: str | None = None
    allow_letnik: bool = False        # доступ к летникам после кода
    guide_index: int = 0              # индекс гайда для новичков
    last_guide_sent_at: str | None = None  # ISO МСК
    has_read_today: bool = False      # прочитал текущий гайд (новичок, сегодняшний)
    task_done_dates: list = None      # список ISO дат МСК, когда сдано (для новичков)
    created_at: str = None            # ISO МСК создания

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
    # нормализуем
    changed = False
    for uid, raw in list(data.items()):
        # старый формат -> новый
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
    # пост-нормализация
    if p.task_done_dates is None:
        p.task_done_dates = []
    return p


def put_user(uid: int, p: Progress):
    USERS[str(uid)] = p.to_dict()
    save_users(USERS)


# =========================
# КЛАВИАТУРЫ
# =========================
def kb_role() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Я новичок"), KeyboardButton(text="Я летник")]
        ],
        resize_keyboard=True
    )


def kb_main_newbie(p: Progress) -> InlineKeyboardMarkup:
    # кнопки читаем/задание показываем только если выбран предмет
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


# =========================
# FSM: сбор данных
# =========================
class RegStates(StatesGroup):
    waiting_role = State()
    waiting_name = State()
    waiting_subject = State()
    waiting_letnik_code = State()


# =========================
# ИНИЦИАЛИЗАЦИЯ БОТА
# =========================
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
router = Router()
dp.include_router(router)


# =========================
# ХЕЛПЕРЫ ДЛЯ СЦЕНАРИЕВ
# =========================
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
    """
    Выдаём задание (после подтверждения чтения). Если это 3-й гайд — подставляем предметное задание.
    """
    idx = p.guide_index
    # защита
    items = GUIDES["newbie"]
    if idx >= len(items):
        await bot.send_message(uid, "🎉 Новичковые гайды уже все пройдены.")
        return

    g = items[idx]
    if g["id"] == "n3":  # третий гайд (по идентификатору в примере)
        task_text = _third_guide_task_for_subject(p.subject or "")
    else:
        # общее задание-заглушка
        task_text = "Кратко законспектируй основные мысли гайда и выполни предложенное упражнение."

    # Время — можно ли ещё сдавать?
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
    """Можно ли нажимать «Я выполнил» сейчас (до 22:00 по МСК)."""
    return is_time_before_deadline(now_msk())


# =========================
# /start и первичная анкета
# =========================
@router.message(CommandStart())
async def cmd_start(m: Message, state: FSMContext):
    p = get_user(m.from_user.id)
    # Сбрасываем шаги и просим роль
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


@router.message(RegStates.waiting_role)
async def fallback_role(m: Message, state: FSMContext):
    await m.answer("Нажми кнопку: <b>Я новичок</b> или <b>Я летник</b>.", reply_markup=kb_role())


@router.message(RegStates.waiting_name, F.text.len() >= 3)
async def take_name(m: Message, state: FSMContext):
    # примитивная проверка
    text = " ".join(m.text.strip().split())
    p = get_user(m.from_user.id)
    p.name = text
    put_user(m.from_user.id, p)

    await state.set_state(RegStates.waiting_subject)
    await m.answer(
        "Отлично! Теперь выбери предмет:",
        reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Выбрать предмет")]], resize_keyboard=True)
    )
    # отдельной кнопкой выводим меню предметов
    await m.answer("👇 Нажми, чтобы выбрать предмет:", reply_markup=None)
    await m.answer("Выбор предмета:", reply_markup=kb_subjects())


@router.message(RegStates.waiting_name)
async def fallback_name(m: Message, state: FSMContext):
    await m.answer("Отправь фамилию и имя одной строкой. Пример: <i>Иванова Анна</i>.")


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
        # если уже после 08:00 и сегодня ещё не выдавали — выдаём
        # (но гайд выдаёт планировщик; здесь — полезная подсказка)
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


# =========================
# ЛЕТНИКИ: код и гайды
# =========================
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


# =========================
# НОВИЧКИ: гайд/чтение/задание/сдача
# =========================
@router.callback_query(F.data == "newbie:open_guide")
async def newbie_open_guide(cb: CallbackQuery):
    p = get_user(cb.from_user.id)
    if p.role != "newbie":
        await cb.answer("Доступно только новичкам.", show_alert=True)
        return
    if not p.subject:
        await cb.answer("Сначала выбери предмет.", show_alert=True)
        return

    # Показываем ссылку на текущий гайд (если уже присылали сегодня)
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

    # дедлайн
    if not can_submit_now():
        await cb.answer("После 22:00 сдача недоступна 😔", show_alert=True)
        return

    # защита: можно сдавать только если подтверждено чтение
    if not p.has_read_today:
        await cb.answer("Сначала отметь, что прочитал(а) гайд.", show_alert=True)
        return

    # помечаем как сданное сегодня
    tiso = _today_iso_date()
    if tiso not in p.task_done_dates:
        p.task_done_dates.append(tiso)

    # продвигаем на следующий гайд к завтрашнему дню
    p.guide_index += 1
    p.has_read_today = False
    put_user(cb.from_user.id, p)

    await cb.message.answer("✅ Задание принято! Новый гайд придёт после 08:00 по МСК.")
    await cb.answer()


# =========================
# ПРОГРЕСС / АДМИН
# =========================
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
    # Команда для админа — список сдачи тестов
@dp.message(Command("tests"))
async def tests_panel(message: Message):
    if ADMIN_ID and message.from_user.id != ADMIN_ID:
        return
    lines = ["📋 Состояние заданий:"]
    for uid, u in USERS.items():
        name = u.get("full_name") or uid
        role = u.get("role", "—")
        subject = u.get("subject", "—")
        prog = u.get("progress", {})
        
        # Находим текущий гайд для пользователя
        guide_id = None
        if role == "newbie":
            idx = u.get("guide_index", 0)
            if idx < len(GUIDES["newbie"]):
                guide_id = GUIDES["newbie"][idx]["id"]
        elif role == "letnik" and GUIDES["letnik"]:
            guide_id = GUIDES["letnik"][0]["id"]
        
        # Определяем статус
        if guide_id and guide_id in prog:
            task_done = prog[guide_id].get("task_done", False)
            status = "✅ Сдано" if task_done else "❌ Не сдано"
        else:
            status = "⏳ Нет данных"
        
        lines.append(f"{name} ({role}, {subject}) — {status}")
    
    await message.answer("\n".join(lines))



# =========================
# ПЛАНИРОВЩИК
# =========================
async def scheduler_loop():
    """
    1) На старте: если сейчас после 08:00 и сегодня ещё не высылали — высылаем новичкам гайд.
    2) Каждый день в 08:00 — выдаём новичкам следующий гайд (по одному).
    3) Каждый день в 14:00 — напоминание новичкам, что дедлайн в 22:00.
    """
    # маленькая пауза после запуска
    await asyncio.sleep(2)

    # Догоним утро, если рестарт после 08:00
    now = now_msk()
    if now.time() >= time(8, 0):
        for uid, raw in list(USERS.items()):
            p = Progress(**raw)
            if p.role != "newbie":
                continue
            # если сегодня ещё не отправляли (сравним дату last_guide_sent_at)
            last_date = None
            if p.last_guide_sent_at:
                try:
                    last_date = datetime.fromisoformat(p.last_guide_sent_at).astimezone(TIMEZONE).date()
                except Exception:
                    last_date = None
            if last_date != today_msk():
                try:
                    await send_newbie_today_guide(int(uid), p)
                    # небольшая задержка между пользователями
                    await asyncio.sleep(0.1)
                except Exception:
                    pass

    # Основной цикл раз в минуту
    while True:
        try:
            now = now_msk()

            # 08:00 — выдача новичкам
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

            # 14:00 — напоминание новичкам про дедлайн
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
            # чтобы цикл не упал навсегда
            await asyncio.sleep(5)
            await cb.message.answer(
        "Готово! Ты отмечен как <b>новичок</b>.\n\n"
        "Напиши, пожалуйста, свою фамилию и имя (например: <i>Иванов Иван</i>)."
    )
    # Запишем стартовую строку в таблицу
    try:
        gs_set(cb.from_user.id, {
            "Telegram ID": str(cb.from_user.id),
            "Роль": "newbie",
            "Дата начала": datetime.now(TIMEZONE).strftime("%Y-%m-%d"),
            "Статус": "В обучении"
        })
    except Exception:
        pass
@dp.message(F.text)
async def capture_full_name(message: Message):
    u = user(message)
    if not u.get("awaiting_full_name"):
        return
    full = message.text.strip()
    if len(full.split()) < 2:
        await message.answer("Нужно указать фамилию и имя. Пример: <i>Иванов Иван</i>")
        return
    u["full_name"] = full
    u["awaiting_full_name"] = False
    save_users(USERS)

    # пишем в таблицу
    try:
        gs_set(message.from_user.id, {"ФИ": full})
    except Exception:
        pass

    # просим выбрать предмет (твоя уже существующая логика)
    await message.answer(
        "Отлично! Теперь выбери предмет:",
        reply_markup=kb_subjects()
    )
@dp.callback_query(F.data.startswith("subject:set:"))
async def subject_set(cb: CallbackQuery):
    s = cb.data.split(":")[2]
    u = user(cb)
    u["subject"] = s
    save_users(USERS)

    # Обновим таблицу
    try:
        gs_set(cb.from_user.id, {"Предмет": s})
    except Exception:
        pass

    await cb.message.answer(f"📘 Предмет сохранён: <b>{s}</b>")

    # HR-шаг: даём ссылку в чат новичков (один раз)
    if u.get("role") == "newbie" and CHAT_LINK_NEWBIE and not u.get("hr_chat_link_sent"):
        u["hr_chat_link_sent"] = True
        save_users(USERS)
        try:
            gs_set(cb.from_user.id, {"В чате новичков": "ссылка отправлена"})
        except Exception:
            pass
        await cb.message.answer(
            f"👋 Добро пожаловать! Вступи в чат новичков по ссылке:\n{CHAT_LINK_NEWBIE}\n\n"
            f"После вступления тебе начнут приходить гайды (после 08:00 МСК)."
        )

    await cb.answer()
async def send_newbie_next_guide(uid: int):
    u = USERS.get(str(uid))
    if not u or u.get("role") != "newbie":
        return
    idx = u.get("guide_index", 0)
    items = GUIDES["newbie"]
    if idx >= len(items):
        await bot.send_message(uid, "🎉 Все гайды для новичков пройдены! Можно попросить статус летник у руководителя.")
        return
    g = items[idx]
    await bot.send_message(
        uid,
        f"📘 Сегодняшний гайд: <b>{g['title']}</b>\nСсылка: {g['url']}\n\n"
        f"Не забудь выполнить задание к 22:00 по МСК."
    )
    u["last_guide_sent_at"] = datetime.now(TIMEZONE).isoformat()
    save_users(USERS)

    # === запись в таблицу: "Гайд X"
    guide_num = idx + 1
    try:
        gs_set(uid, {f"Гайд {guide_num}": "отправлен"})
    except Exception:
        pass
@dp.callback_query(F.data == "task:done")
async def task_done(cb: CallbackQuery):
    u = user(cb)
    role = u["role"]
    items = GUIDES["newbie"] if role == "newbie" else GUIDES["letnik"]

    idx = u.get("guide_index", 0)
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

    prog = u.setdefault("progress", {})
    gstat = prog.setdefault(guide["id"], {"read": True, "task_done": False})
    gstat["read"] = True
    gstat["task_done"] = True
    save_users(USERS)

    await cb.message.answer(f"✅ Задание по «{guide['title']}» отмечено как выполненное!")

    # === запись в таблицу: "Задание X"
    if role == "newbie":
        guide_num = u.get("guide_index", 0) + 1
        try:
            gs_set(cb.from_user.id, {f"Задание {guide_num}": "выполнено"})
        except Exception:
            pass
    else:
        # для летников можно фиксить иначе (по твоей логике)
        try:
            gs_set(cb.from_user.id, {"Статус": "Тест у летника выполняется"})
        except Exception:
            pass

    await cb.answer()
@dp.callback_query(F.data == "final_test")
async def process_final_test(cb: CallbackQuery):
    u = user(cb)
    uid = cb.from_user.id
    role = u.get("role")

    if role != "newbie":
        await cb.answer("Финальный тест — для новичков.", show_alert=True)
        return

    u["final_test_done"] = True
    u["finished_at"] = datetime.now(TIMEZONE).isoformat()
    save_users(USERS)

    # запись в таблицу
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



# =========================
# ВЕБ-СЕРВИС ДЛЯ RENDER (ОБХОД)
# =========================
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


# =========================
# MAIN
# =========================
async def main():
    # веб-сервис для Render (чтобы держать порт открыт)
    await start_web_app()

    # запускаем планировщик
    asyncio.create_task(scheduler_loop())

    # пулинг
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
# === ОБРАБОТЧИКИ ТЕСТОВ ===

@dp.callback_query_handler(lambda c: c.data.startswith("test_"))
async def process_test(callback_query: types.CallbackQuery):
    subject = callback_query.data.split("_", 1)[1]
    
    await callback_query.message.answer(
        f"📘 Тест по предмету {subject}.\n"
        f"Скоро здесь будут вопросы!"
    )
    await bot.answer_callback_query(callback_query.id)


@dp.callback_query_handler(lambda c: c.data == "final_test")
async def process_final_test(callback_query: types.CallbackQuery):
    await callback_query.message.answer(
        "🎓 Финальный тест!\n"
        "Здесь будут заключительные вопросы для проверки знаний."
    )
    await bot.answer_callback_query(callback_query.id)
guide_kb = InlineKeyboardMarkup()

guide_button = InlineKeyboardButton("Читать гайд", url=link)
guide_kb.add(guide_button)

# Кнопка теста для летников
if role == "letnik":
    test_button = InlineKeyboardButton("Пройти тест", callback_data=f"test_{subject}")
    guide_kb.add(test_button)

# Кнопка финального теста для новичков (если это последний гайд)
if role == "newbie" and guide_number == 3:  # замени 3 на число последнего гайда
    final_test_button = InlineKeyboardButton("Финальный тест", callback_data="final_test")
    guide_kb.add(final_test_button)
if r == "newbie":
    u["awaiting_full_name"] = True
    save_users(USERS)

   












