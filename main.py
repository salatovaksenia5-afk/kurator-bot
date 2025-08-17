import os
import asyncio
import json
from datetime import datetime, timedelta, time, timezone
from gsheets import WS_SUMMARY, gs_log_event
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
)
MSK = timezone(timedelta(hours=3))  # Московское время UTC+3

def _now_msk():
    return datetime.now(MSK)


# ============== НАСТРОЙКИ / КОНСТАНТЫ ==============
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("Нет BOT_TOKEN. Добавь переменную окружения BOT_TOKEN на Render.")
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot=bot)

ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or "0")  # твой телеграм ID
TIMEZONE = timezone(timedelta(hours=3))  # МСК
PORT = int(os.getenv("PORT", "10000"))

HR_CHAT_LINK = os.getenv("HR_CHAT_LINK", "")  # ссылка в чат новичков
LETL_CODE = os.getenv("LETL_CODE", "letl2025")  # код для летников

REMIND_HOURS = [14, 22]  # напоминания новичкам
DEADLINE_HOUR = 22       # после 22:00 «Я выполнил задание» закрывается
GUIDE_HOUR = 8           # в 08:00 выдаем следующий гайд новичкам

DATA_DIR = "data"
USERS_FILE = os.path.join(DATA_DIR, "users.json")
GUIDES_FILE = os.path.join(DATA_DIR, "guides.json")
os.makedirs(DATA_DIR, exist_ok=True)

# ============== GOOGLE SHEETS ==============
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from aiogram import types
from datetime import datetime

def add_user_to_sheets(user: types.User):
    """
    Добавляет нового пользователя в WS_SUMMARY с нужными колонками.
    Если пользователь уже есть, просто обновляет информацию.
    """
    if not WS_SUMMARY:
        print("⚠️ WS_SUMMARY не подключен")
        return

    uid = user.id
    fio = f"{user.first_name} {user.last_name or ''}".strip()
    now = datetime.now(TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
    
    # Пример структуры progress: guide_1, guide_2, guide_3, final_test
    progress = {
        "guide_1": {"read": False, "task_done": False},
        "guide_2": {"read": False, "task_done": False},
        "guide_3": {"read": False, "task_done": False},
        "final_test": {"done": False}
    }

    user_dict = {
        "fio": fio,
        "role": "newbie",
        "subject": "",  # можно заполнить по умолчанию
        "status": "active",
        "guide_index": 0,
        "progress": progress,
        "created_at": now,
        "finished_at": "",
        "last_guide_sent_at": ""
    }

    # Проверяем, есть ли пользователь
    all_values = WS_SUMMARY.get_all_records()
    row_index = None
    for i, row in enumerate(all_values, start=2):
        if str(row.get("TG_ID")) == str(uid):
            row_index = i
            break

    # Формируем значения для записи
    values = [
        str(uid),
        fio,
        user_dict.get("role"),
        user_dict.get("subject"),
        user_dict.get("status"),
        int(user_dict.get("guide_index", 0)),
        int(user_dict["progress"]["guide_1"]["read"]),
        int(user_dict["progress"]["guide_2"]["read"]),
        int(user_dict["progress"]["guide_3"]["read"]),
        int(user_dict["progress"]["guide_1"]["task_done"]),
        int(user_dict["progress"]["guide_2"]["task_done"]),
        int(user_dict["progress"]["guide_3"]["task_done"]),
        int(user_dict["progress"]["final_test"]["done"]),
        user_dict.get("created_at"),
        user_dict.get("finished_at"),
        user_dict.get("last_guide_sent_at")
    ]

    try:
        if row_index:
            for col, val in enumerate(values, start=1):
                WS_SUMMARY.update_cell(row_index, col, val)
        else:
            WS_SUMMARY.append_row(values)
    except Exception as e:
        print("⚠️ Ошибка записи в WS_SUMMARY:", e)
@dp.message(CommandStart())
async def start_command(message: types.Message):
    add_user_to_sheets(message.from_user)
    await message.answer(f"Привет, {message.from_user.first_name}! Ты добавлен в таблицу.")
@dp.message(CommandStart())
async def cmd_start(message: Message):
    uid = message.from_user.id
    fio = f"{message.from_user.first_name} {message.from_user.last_name or ''}".strip()
    
    # Формируем словарь пользователя
    user_data = {
        "fio": fio,
        "role": "newbie",
        "subject": "",
        "created_at": datetime.now(TIMEZONE).strftime("%Y-%m-%d %H:%M"),
        "progress": {
            "guide1": {"read": False, "task_done": False},
            "guide2": {"read": False, "task_done": False},
            "guide3": {"read": False, "task_done": False},
            "final_test": {"done": False}
        }
    }
    
    # Добавляем или обновляем в Google Sheets
    gs_upsert_summary(uid, user_data)
def gs_upsert_summary(user_id, user_data):
    """
    Заглушка для обновления сводки по пользователю.
    Пока можно оставить пустой, чтобы бот не падал.
    """
    pass
     await message.answer(f"Привет, {fio}! Ты добавлен в таблицу.")
# ============== JSON "БД" ==============
def _read_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception:
        return default

def _write_json(path: str, payload):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def load_users():
    data = _read_json(USERS_FILE, {})
    for uid, u in data.items():
        u.setdefault("fio", None)
        u.setdefault("role", None)                  # newbie / letnik
        u.setdefault("subject", None)
        u.setdefault("guide_index", 0)              # индекс текущего гайда для новичка
        u.setdefault("last_guide_sent_at", None)    # ISO
        u.setdefault("progress", {})                # {guide_id: {"read": bool, "task_done": bool, "test_done": bool}}
        u.setdefault("created_at", _now_msk().isoformat())
        u.setdefault("finished_at", "")
        u.setdefault("status", "")
        u.setdefault("awaiting_fio", False)
        u.setdefault("awaiting_subject", False)
        u.setdefault("awaiting_code", False)
    return data

def save_users(data):
    _write_json(USERS_FILE, data)

def load_guides():
    data = _read_json(GUIDES_FILE, {})
    if not data:
        data = {
            # Новички — 4 гайда (пример), 3-й с предметной задачей
            "newbie": [
                {"id": "n1", "num": 1, "title": "Гайд 1", "url": "https://example.com/n1"},
                {"id": "n2", "num": 2, "title": "Гайд 2", "url": "https://example.com/n2"},
                {"id": "n3", "num": 3, "title": "Гайд 3", "url": "https://example.com/n3"},
                {"id": "n4", "num": 4, "title": "Гайд 4", "url": "https://example.com/n4"},
            ],
            # Летники — высылаем всё сразу (пример наполнения)
            "letnik": [
                {"id": "l1", "title": "Летник 1", "url": "https://example.com/l1", "test_url": "https://example.com/lt1test"},
                {"id": "l2", "title": "Летник 2", "url": "https://example.com/l2", "test_url": "https://example.com/lt2test"},
                {"id": "l3", "title": "Летник 3", "url": "https://example.com/l3", "test_url": "https://example.com/lt3test"},
            ],
            "subjects": ["математика", "информатика", "физика", "русский язык", "обществознание", "биология", "химия"]
        }
        _write_json(GUIDES_FILE, data)
    return data

USERS = load_users()
GUIDES = load_guides()

# Предметные задания для 3-го гайда
SUBJECT_TASKS = {
    "математика": "Реши 5 задач на проценты и отправь разбор одной задачи.",
    "информатика": "Сделай короткий скрипт, автоматизирующий рутинную операцию, пришли код.",
    "физика": "Разбери пример по кинематике: составь уравнения, сделай расчёт.",
    "русский язык": "Подготовь 3 примера сложноподчинённых предложений с разбором.",      
    "обществознание": "Сделай конспект по теме «Социальные институты» (10–12 тезисов).",
    "биология": "Составь схему по теме «Клетка» и сделай краткий конспект.",
    "химия": "Реши 3 расчётных задачи и отправь один полный разбор."
}

# ============== БОТ ==============
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

# ============== КЛАВИАТУРЫ ==============
def kb_subjects():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=s.title(), callback_data=f"subject:set:{s}")]
        for s in GUIDES["subjects"]
    ])

def kb_role():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟢 Я новичок", callback_data="role:newbie")],
        [InlineKeyboardButton(text="🟠 Я летник", callback_data="role:letnik")]
    ])

def kb_main(role: str):
    rows = [
        [InlineKeyboardButton(text="📊 Мой прогресс", callback_data="progress:me")],
        [InlineKeyboardButton(text="📚 Каталог", callback_data="guides:menu")]
    ]
    if role == "newbie":
        rows.append([InlineKeyboardButton(text="🕗 Мой график гайдов", callback_data="newbie:schedule")])
    if role == "letnik":
        rows.append([InlineKeyboardButton(text="⚡ Все материалы и тесты", callback_data="letnik:all")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_mark_read(guide_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📖 Отметить прочитанным", callback_data=f"newbie:read:{guide_id}")]
    ])

def kb_task_button(guide_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Я выполнил задание", callback_data=f"newbie:task:{guide_id}")]
    ])

def kb_final_test():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Пройти финальный тест", callback_data="newbie:final")]
    ])

# ============== УТИЛИТЫ ==============
def user(obj: Message | CallbackQuery) -> dict:
    uid = obj.from_user.id
    if str(uid) not in USERS:
        USERS[str(uid)] = {
            "fio": None,
            "role": None,
            "subject": None,
            "guide_index": 0,
            "last_guide_sent_at": None,
            "progress": {},
            "created_at": _now_msk().isoformat(),
            "finished_at": "",
            "status": "",
            "awaiting_fio": False,
            "awaiting_subject": False,
            "awaiting_code": False
        }
        save_users(USERS)
    return USERS[str(uid)]

def _today_iso():
    return _now_msk().date().isoformat()

def _is_before_deadline() -> bool:
    cur = _now_msk().time()
    return cur < time(DEADLINE_HOUR, 0)

def _was_sent_today(u: dict) -> bool:
    last = u.get("last_guide_sent_at")
    if not last:
        return False
    try:
        dt = datetime.fromisoformat(last).astimezone(TIMEZONE)
        return dt.date() == _now_msk().date()
    except Exception:
        return False

async def _send_newbie_guide(uid: int):
    u = USERS.get(str(uid))
    if not u or u.get("role") != "newbie":
        return
    idx = u.get("guide_index", 0)
    items = GUIDES["newbie"]
    if idx >= len(items):
        # Все гайды пройдены — финальный тест (однократно)
        await bot.send_message(uid, "🎉 Все гайды для новичков пройдены!")
        await bot.send_message(uid, "Финальный тест доступен ниже:", reply_markup=kb_final_test())
        gs_log_event(uid, u.get("fio",""), u.get("role",""), u.get("subject",""), "Финальный тест выдан")
        return

    g = items[idx]
    # Отправляем сам гайд
    text = (
        f"📘 Сегодняшний гайд #{g['num']}: <b>{g['title']}</b>\n"
        f"Ссылка: {g['url']}\n\n"
        f"После прочтения нажми «Отметить прочитанным».\n"
        f"Задание откроется только после отметки прочтения.\n"
        f"Сдать задание можно до <b>{DEADLINE_HOUR}:00 МСК</b>."
    )
    await bot.send_message(uid, text, reply_markup=kb_mark_read(g["id"]))
    u["last_guide_sent_at"] = _now_msk().isoformat()
    save_users(USERS)
    gs_log_event(uid, u.get("fio",""), u.get("role",""), u.get("subject",""), f"Гайд выдан", f"id={g['id']}, idx={idx+1}")
    gs_upsert_summary(uid, u)

async def _send_subject_task(uid: int, u: dict, guide: dict):
    """
    Вызывается после отметки «прочитано».
    Если это 3-й гайд — выдаём предметное задание.
    Иначе — базовая заглушка для задания.
    """
    idx_num = guide.get("num")
    if idx_num == 3:
        subj = (u.get("subject") or "").lower()
        task = SUBJECT_TASKS.get(subj, "Сделай предметное задание по третьему гайду и отправь результат.")
        msg = f"🧩 Предметное задание к гайду #3 ({u.get('subject','—')}):\n\n{task}\n\nСдай до {DEADLINE_HOUR}:00."
    else:
        msg = "🧩 Задание к гайду: выполни практику и отметь выполнение до дедлайна."
    kb = kb_task_button(guide["id"]) if _is_before_deadline() else None
    await bot.send_message(uid, msg, reply_markup=kb)
    gs_log_event(uid, u.get("fio",""), u.get("role",""), u.get("subject",""), f"Задание выдано", f"guide_id={guide['id']}")

# ============== ХЕНДЛЕРЫ: РЕГИСТРАЦИЯ / ДАННЫЕ ==============
@dp.message(CommandStart())
async def start(message: Message):
    u = user(message)
    u["awaiting_fio"] = True
    save_users(USERS)
    await message.answer("👋 Привет! Я бот-куратор.\nНапиши, пожалуйста, свою <b>фамилию и имя</b> (ФИО).")

@dp.message(F.text)
async def handle_text(message: Message):
    u = user(message)
    uid = message.from_user.id
    text = (message.text or "").strip()

    # Ввод ФИО
    if u.get("awaiting_fio"):
        u["fio"] = text
        u["awaiting_fio"] = False
        u["awaiting_subject"] = True
        u.setdefault("status", "Старт обучения")
        save_users(USERS)
        gs_log_event(uid, u["fio"], u.get("role",""), u.get("subject",""), "ФИО введено")
        gs_upsert_summary(uid, u)
        if u.get("awaiting_fio"):
    # сохраняем ФИО
           fio = message.text.strip()
           u["fio"] = fio
           u["awaiting_fio"] = False
           save_users(USERS)
    gs_upsert_summary(uid, u)  # обновление таблицы

    # Отправляем ответ пользователю
    await message.answer(f"✅ ФИО сохранено: {fio}\nТеперь бот будет отправлять задания.")
    
    # Если используешь FSM
    if 'state' in locals():
        await state.clear()
        return  # чтобы дальше не шли остальные проверки
        await message.answer("✅ ФИО сохранено.\nТеперь выбери предмет:", reply_markup=kb_subjects())
        return

    # Код для летника
    if u.get("awaiting_code"):
        if text == LETL_CODE:
            u["awaiting_code"] = False
            u["role"] = "letnik"
            u["status"] = "Летник (код подтвержден)"
            save_users(USERS)
            gs_log_event(uid, u.get("fio",""), "letnik", u.get("subject",""), "Код подтвержден")
            gs_upsert_summary(uid, u)
            await message.answer("🔓 Код верный. Доступ открыт.", reply_markup=kb_main("letnik"))
        else:
            await message.answer("❌ Неверный код. Попробуй ещё раз.")
        return

@dp.callback_query(F.data.startswith("subject:set:"))
async def subject_set(cb: CallbackQuery):
    u = user(cb)
    subj = cb.data.split(":")[2]
    u["subject"] = subj
    u["awaiting_subject"] = False
    save_users(USERS)
    gs_log_event(cb.from_user.id, u.get("fio",""), u.get("role",""), subj, "Предмет выбран")
    gs_upsert_summary(cb.from_user.id, u)

    await cb.message.answer(
        f"📘 Предмет сохранён: <b>{subj}</b>\nТеперь выбери свою роль:",
        reply_markup=kb_role()
    )
    await cb.answer()

@dp.callback_query(F.data.startswith("role:"))
async def role_set(cb: CallbackQuery):
    u = user(cb)
    role = cb.data.split(":")[1]
    if role == "letnik":
        u["awaiting_code"] = True
        u["role"] = None  # до ввода кода
        save_users(USERS)
        await cb.message.answer("🔑 Введи код доступа для летников:")
        await cb.answer()
        return

    # Новичок
    u["role"] = "newbie"
    u["status"] = "Новичок (старт обучения)"
    save_users(USERS)
    gs_log_event(cb.from_user.id, u.get("fio",""), "newbie", u.get("subject",""), "Выбрана роль: новичок")
    gs_upsert_summary(cb.from_user.id, u)

    # HR онбординг
    if HR_CHAT_LINK:
        await cb.message.answer(
            "👥 Вступи, пожалуйста, в чат новичков и возвращайся сюда:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Вступить в чат новичков", url=HR_CHAT_LINK)]
            ])
        )
        gs_log_event(cb.from_user.id, u.get("fio",""), "newbie", u.
     get("subject",""), "Выдана HR-ссылка")

    await cb.message.answer(
        "Гайды будут приходить по одному каждый день после 08:00 МСК.\n"
        "После прочтения открывается задание. Сдать его можно до 22:00 МСК.",
        reply_markup=kb_main("newbie")
    )
    await cb.answer()

# ============== ХЕНДЛЕРЫ: ПРОГРЕСС / КАТАЛОГ ==============
@dp.callback_query(F.data == "progress:me")
async def progress_me(cb: CallbackQuery):
    u = user(cb)
    role = u.get("role") or "—"
    subj = u.get("subject") or "—"
    idx = u.get("guide_index", 0)
    items = GUIDES["newbie"] if role == "newbie" else GUIDES["letnik"]
    total = len(items)
    done_tasks = sum(1 for v in u.get("progress", {}).values() if v.get("task_done"))
    done_tests = sum(1 for v in u.get("progress", {}).values() if v.get("test_done"))
    text = (
        f"📊 Твой прогресс\n\n"
        f"ФИО: <b>{u.get('fio','—')}</b>\n"
        f"Роль: <b>{role}</b>\n"
        f"Предмет: <b>{subj}</b>\n"
        f"Текущий гайд (новичок): <b>{idx}/{len(GUIDES['newbie'])}</b>\n"
        f"Выполнено заданий: <b>{done_tasks}</b>\n"
        f"Пройдено тестов: <b>{done_tests}</b>\n"
    )
    await cb.message.answer(text)
    await cb.answer()

@dp.callback_query(F.data == "guides:menu")
async def guides_menu(cb: CallbackQuery):
    u = user(cb)
    if u.get("role") == "letnik":
        # краткий список
        lines = []
        for g in GUIDES["letnik"]:
            lines.append(f"• <b>{g['title']}</b> — {g['url']} (тест: {g.get('test_url','—')})")
        await cb.message.answer("⚡ Материалы для летников:\n\n" + "\n".join(lines))
    else:
        # текущий/следующий гайд
        idx = u.get("guide_index", 0)
        items = GUIDES["newbie"]
        if idx >= len(items):
            await cb.message.answer("🎉 Все гайды пройдены. Доступен финальный тест.", reply_markup=kb_final_test())
        else:
            g = items[idx]
            await cb.message.answer(
                f"Следующий гайд #{g['num']}: <b>{g['title']}</b>\n{g['url']}",
                reply_markup=kb_mark_read(g["id"])
            )
    await cb.answer()

@dp.callback_query(F.data == "newbie:schedule")
async def newbie_schedule(cb: CallbackQuery):
    u = user(cb)
    idx = u.get("guide_index", 0)
    total = len(GUIDES["newbie"])
    left = max(0, total - idx)
    await cb.message.answer(
        f"🕗 Гайды приходят после 08:00 МСК.\n"
        f"Осталось гайдов: <b>{left}</b>."
    )
    await cb.answer()

# ============== ХЕНДЛЕРЫ: НОВИЧКИ (прочитал / задание / финал) ==============
@dp.callback_query(F.data.startswith("newbie:read:"))
async def newbie_mark_read(cb: CallbackQuery):
    u = user(cb)
    if u.get("role") != "newbie":
        await cb.answer("Только для новичков", show_alert=True)
        return
    guide_id = cb.data.split(":")[2]
    # найдём объект гайда по guide_index
    idx = u.get("guide_index", 0)
    items = GUIDES["newbie"]
    if idx >= len(items):
        await cb.answer("Все гайды уже пройдены.")
        return
    guide = items[idx]
    if guide["id"] != guide_id:
        await cb.answer("Это не текущий гайд.")
        return

    # помечаем «read»
    pr = u.setdefault("progress", {})
    st = pr.setdefault(guide_id, {"read": False, "task_done": False, "test_done": False})
    st["read"] = True
    save_users(USERS)
    gs_log_event(cb.from_user.id, u.get("fio",""), "newbie", u.get("subject",""), "Отмечен прочитанным", f"guide={guide_id}")
    gs_upsert_summary(cb.from_user.id, u)

    await cb.message.answer("📖 Отмечено как прочитано. Выдаю задание…")
    await _send_subject_task(cb.from_user.id, u, guide)
    await cb.answer()

@dp.callback_query(F.data.startswith("newbie:task:"))
async def newbie_task_done(cb: CallbackQuery):
    u = user(cb)
    if u.get("role") != "newbie":
        await cb.answer("Только для новичков", show_alert=True)
        return
    if not _is_before_deadline():
        await cb.answer("Дедлайн истёк. Задание можно было сдать до 22:00 МСК.", show_alert=True)
        return

    guide_id = cb.data.split(":")[2]
    idx = u.get("guide_index", 0)
    items = GUIDES["newbie"]
    if idx >= len(items):
        await cb.answer("Все гайды уже пройдены.")
        return
    guide = items[idx]
    if guide["id"] != guide_id:
        await cb.answer("Это не текущий гайд.")
        return

    # отмечаем задание выполненным
    pr = u.setdefault("progress", {})
    st = pr.setdefault(guide_id, {"read": False, "task_done": False, "test_done": False})
    if not st.get("read"):
        await cb.answer("Сначала отметь, что прочитал гайд.", show_alert=True)
        return
    st["task_done"] = True
    save_users(USERS)
    gs_log_event(cb.from_user.id, u.get("fio",""), "newbie", u.get("subject",""), "Задание выполнено", f"guide={guide_id}")
    gs_upsert_summary(cb.from_user.id, u)

    # после сдачи — фиксируем завершение текущего гайда (переход на следующий в следующий день в 8:00)
    u["guide_index"] = min(u.get("guide_index", 0) + 1, len(GUIDES["newbie"]))
    save_users(USERS)
    gs_upsert_summary(cb.from_user.id, u)

    await cb.message.answer("✅ Задание принято! Следующий гайд придёт после 08:00 МСК завтра.")
    await cb.answer()

@dp.callback_query(F.data == "newbie:final")
async def newbie_final_test(cb: CallbackQuery):
    u = user(cb)
    if u.get("role") != "newbie":
        await cb.answer("Только для новичков", show_alert=True)
        return
    # Ссылка на финальный тест (замени)
    await cb.message.answer("📝 Финальный тест: https://example.com/final-test")
    # Отметка прохождения теста (кнопка)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Я прошёл финальный тест", callback_data="newbie:final:done")]
    ])
    await cb.message.answer("Когда пройдёшь — нажми кнопку ниже.", reply_markup=kb)
    await cb.answer()

@dp.callback_query(F.data == "newbie:final:done")
async def newbie_final_done(cb: CallbackQuery):
    u = user(cb)
    # помечаем итог
    u["status"] = "Обучение завершено (новичок)"
    u["finished_at"] = _now_msk().isoformat()
    save_users(USERS)
    gs_log_event(cb.from_user.id, u.get("fio",""), "newbie", u.get("subject",""), "Финальный тест пройден")
    gs_upsert_summary(cb.from_user.id, u)
    await cb.message.answer("🎉 Поздравляем! Ты прошёл обучение. Добро пожаловать в команду!")
    await cb.answer()

# ============== ХЕНДЛЕРЫ: ЛЕТНИКИ ==============
@dp.callback_query(F.data == "letnik:all")
async def letnik_all(cb: CallbackQuery):
    u = user(cb)
    if u.get("role") != "letnik":
        await cb.answer("Доступно только летникам", show_alert=True)
        return

    # отправляем материалы с кнопками: открыть материал, открыть тест, отметить тест пройденным
    lines = ["⚡ Все материалы для летников:"]
    for g in GUIDES["letnik"]:
        lines.append(f"• <b>{g['title']}</b> — {g['url']}")
    await cb.message.answer("\n".join(lines))

    for g in GUIDES["letnik"]:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📖 Открыть материал", url=g["url"])],
            [InlineKeyboardButton(text="📝 Открыть тест", url=g.get("test_url","https://example.com/test"))],
            [InlineKeyboardButton(text="✅ Отметить тест пройденным", callback_data=f"letnik:testdone:{g['id']}")]
        ])
        await cb.message.answer(f"<b>{g['title']}</b>", reply_markup=kb)

    gs_log_event(cb.from_user.id, u.get("fio",""), "letnik", u.get("subject",""), "Выданы материалы летнику")
    await cb.answer()


@dp.callback_query(F.data.startswith("letnik:testdone:"))
async def letnik_test_done(cb: CallbackQuery):
    u = user(cb)
    if u.get("role") != "letnik":
        await cb.answer("Только для летников", show_alert=True)
        return

    guide_id = cb.data.split(":")[2]
    pr = u.setdefault("progress", {})
    st = pr.setdefault(guide_id, {"read": True, "task_done": True, "test_done": False})
    st["test_done"] = True
    save_users(USERS)
    gs_log_event(cb.from_user.id, u.get("fio",""), "letnik", u.get("subject",""), "Тест пройден (летник)", f"guide={guide_id}")
    gs_upsert_summary(cb.from_user.id, u)

    await cb.message.answer("✅ Тест отмечен как пройденный.")
    await cb.answer()

# ============== КОМАНДЫ АДМИНА ==============
@dp.message(Command("admin"))
async def admin_panel(message: Message):
    if ADMIN_ID and message.from_user.id != ADMIN_ID:
        return

    total = len(USERS)
    newbies = [u for u in USERS.values() if u.get("role") == "newbie"]
    letniki = [u for u in USERS.values() if u.get("role") == "letnik"]

    lines = [
        "🔧 <b>Админ-панель</b>",
        f"👥 Всего пользователей: <b>{total}</b>",
        f"🟢 Новичков: <b>{len(newbies)}</b>",
        f"🟠 Летников: <b>{len(letniki)}</b>",
        ""
    ]

    # Топ последних 10 регистраций
    last = sorted(USERS.items(), key=lambda kv: kv[1].get("created_at",""), reverse=True)[:10]
    lines.append("🕒 Последние регистрации:")
    for uid, u in last:
        lines.append(f"{uid}: {u.get('fio','—')} | {u.get('role','—')} | {u.get('subject','—')} | idx={u.get('guide_index',0)}")

    await message.answer("\n".join(lines))


@dp.message(Command("tests"))
async def admin_tests(message: Message):
    if ADMIN_ID and message.from_user.id != ADMIN_ID:
        return

    # сводка по выполнению заданий/тестов
    def stats_for(u: dict):
        prog = u.get("progress", {})
        done_tasks = sum(1 for v in prog.values() if v.get("task_done"))
        done_tests = sum(1 for v in prog.values() if v.get("test_done"))
        read_cnt   = sum(1 for v in prog.values() if v.get("read"))
        return read_cnt, done_tasks, done_tests

    lines = ["📑 <b>Сводка по заданиям/тестам</b>", ""]
    # последние 20 активных
    active = sorted(USERS.items(), key=lambda kv: kv[1].get("last_guide_sent_at","") or kv[1].get("created_at",""), reverse=True)[:20]
    for uid, u in active:
        rc, tc, xc = stats_for(u)
        lines.append(f"{uid}: {u.get('fio','—')} | {u.get('role','—')} | {u.get('subject','—')} | "
                     f"прочитано={rc}, заданий={tc}, тестов={xc}, idx={u.get('guide_index',0)}")

    await message.answer("\n".join(lines))

# ============== РАСПИСАНИЕ / ЗАДАЧИ ==============
async def scheduler_loop():
    """
    1) Утром (08:00 МСК) выдаём новичкам следующий гайд (по одному в день).
    2) Если бот рестартовал после 08:00 — «догоняем» и выдаем пропущенное.
    3) В 14:00 и 22:00 — напоминаем новичкам про дедлайн.
    """
    await asyncio.sleep(3)  # пауза после запуска

    # Догоним утро, если рестартнули после 08:00 и ещё не слали сегодня
    now = _now_msk()
    if now.time() >= time(GUIDE_HOUR, 0):
        for uid, u in USERS.items():
            if u.get("role") != "newbie":
                continue
            if _was_sent_today(u):
                continue
            try:
                await _send_newbie_guide(int(uid))
            except Exception as e:
                print("scheduler catch-up err:", e)

    # Основной цикл
    while True:
        try:
            now = _now_msk()

            # 08:00 — выдача гайда новичкам
            if now.time().hour == GUIDE_HOUR and now.time().minute == 0:
                for uid, u in USERS.items():
                    if u.get("role") != "newbie":
                        continue
                    if _was_sent_today(u):
                        continue
                    await _send_newbie_guide(int(uid))

            # 14:00 — напоминание новичкам о дедлайне
            if now.time().hour == 14 and now.time().minute == 0:
                for uid, u in USERS.items():
                    if u.get("role") == "newbie":
                        await bot.send_message(int(uid), "⏰ Напоминание: сдать задание сегодня до 22:00 МСК!")

            # 22:00 — финальное напоминание (и закрытие кнопок мы контролируем проверкой времени)
            if now.time().hour == 22 and now.time().minute == 0:
                for uid, u in USERS.items():
                    if u.get("role") == "newbie":
                        await bot.send_message(int(uid), "⏰ Дедлайн наступил. Новые задания — завтра после 08:00 МСК.")

            await asyncio.sleep(60)  # проверяем раз в минуту
        except asyncio.CancelledError:
            break
        except Exception as e:
            print("scheduler loop err:", e)
            await asyncio.sleep(5)

# ============== ВЕБ-СЕРВЕР ДЛЯ RENDER ==============
async def handle_root(request):
    return web.Response(text="kurator-bot ok")

async def handle_health(request):
    return web.json_response({"status": "ok", "ts": _now_msk().isoformat()})

async def start_web_app():
    app = web.Application()
    app.add_routes([
        web.get("/", handle_root),
        web.get("/health", handle_health),
    ])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

# ============== MAIN ==============
async def main():
    print("Бот запускается...")

    # поднимаем лёгкий веб-сервис (чтобы Render видел открытый порт)
    asyncio.create_task(start_web_app())

    # запускаем планировщик
    asyncio.create_task(scheduler_loop())

    # запускаем бота (главный цикл)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        import traceback
        print("❌ Ошибка при запуске:")
        traceback.print_exc()





























