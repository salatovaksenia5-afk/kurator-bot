import os
import asyncio
import json
from aiogram.fsm.context import FSMContext
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
FINAL_TEST_URL = "https://docs.google.com/forms/d/e/1FAIpQLSd3OSHI2tOQINP7jhuQKD3Kbc9A3t2b-nKpoglDGvhIXv9gnw/viewform?usp=header"

HR_CHAT_LINK = os.getenv("HR_CHAT_LINK", "https://t.me/obucheniehub_bot")  # ссылка в чат новичков
LETL_CODE = os.getenv("LETL_CODE", "letl2025")  # код для летников
NEWBIE_CODE = os.getenv("NEWBIE_CODE", "newbie2025")



REMIND_HOURS = [14, 22]  # напоминания новичкам
DEADLINE_HOUR = 22       # после 22:00 «Я выполнил задание» закрывается
GUIDE_HOUR = 8           # в 08:00 выдаем следующий гайд новичкам

import os

DATA_DIR = "data"
USERS_FILE = os.path.join(DATA_DIR, "users.json")
GUIDES_FILE = os.path.join(DATA_DIR, "guides.json")
os.makedirs(DATA_DIR, exist_ok=True)

# ======= ЧИСТЫЙ СТАРТ (только выбранные файлы) =======
for f in [USERS_FILE, GUIDES_FILE]:
    if os.path.exists(f):
        os.remove(f)

user_data = {}  # словарь для хранения данных пользователей



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
            "newbie": [
                {
                    "id": "guide1",
                    "num": 1,
                    "title": "Как построить общение с учеником?",
                    "text": "Читай гайд внимательно!",
                    "url": "https://docs.google.com/document/d/1tEiUuP8wAuwsnxQj2qaqpYH_VYj5a-2mNNZG--iv2I4/edit?usp=sharing",
                    "test_url": "https://docs.google.com/forms/d/e/1FAIpQLSf3wh-yOoLOrGYkCaBZ5a0jfOP1dr_8OdbDJ4nHT5ZU9Ws5Wg/viewform?usp=header"
                },
                {
                    "id": "guide2",
                    "num": 2,
                    "title": "Технические моменты в работе куратора",
                    "text": "Читай гайд внимательно!!",
                    "url": "https://docs.google.com/document/d/1JH0-6m1ygQ4bTjoWTITXMtqFEXGAvUAgieyD8doK21o/edit?usp=sharing",
                    "test_url": "https://docs.google.com/forms/d/e/1FAIpQLSeOe5IXIKFsclxP0mTSeDdPK_cX1qdtTAtUofjlilu9UGHVyA/viewform?usp=header"
                },
                {
                    "id": "guide3",
                    "num": 3,
                    "title": "Как проверять домашние задания + практика",
                    "text": "Переходи по ссылкам, изучай и делай задание! Задание выполняй в файле и потом скидывай своему старшему куратору",
                    "url": "https://docs.google.com/document/d/1gkhcvRV6HydDILnm24jY7ltOKsriM71jdHdzBn2b9VY/edit?usp=sharing",
                    "test_url": "https://docs.google.com/document/d/1NKQRuiJ-almJuSaI3_TLvn-FvIPLv8-EvsGUDL_bMZ8/edit?usp=sharing"
                },
                {
                    "id": "guide4",
                    "num": 4,
                    "title": "Оставшиеся моменты",
                    "text": "После этого гайда сразу идет финальный тест! решай внимательно!)",
                    "url": "https://docs.google.com/document/d/18ZKfsL12_DpttspiO-0sCR83_-xNBgZ8gsxFf-Fe-q4/edit?usp=sharing",
                    "test_url": "https://docs.google.com/forms/d/e/1FAIpQLSd3OSHI2tOQINP7jhuQKD3Kbc9A3t2b-nKpoglDGvhIXv9gnw/viewform?usp=header"
                }
            ],
            "letnik": [
                {"id": "l1", "title": "Летник 1", "url": "https://example.com/l1", "test_url": "https://docs.google.com/forms/d/e/1FAIpQLSf3wh-yOoLOrGYkCaBZ5a0jfOP1dr_8OdbDJ4nHT5ZU9Ws5Wg/viewform?usp=header"},
                {"id": "l2", "title": "Летник 2", "url": "https://example.com/l2", "test_url": "https://docs.google.com/forms/d/e/1FAIpQLSeOe5IXIKFsclxP0mTSeDdPK_cX1qdtTAtUofjlilu9UGHVyA/viewform?usp=header"},
                {"id": "l3", "title": "Летник 3", "url": "https://example.com/l3", "test_url": "https://example.com/lt3test"}
            ],
            "subjects": ["математика", "информатика", "физика", "русский язык", "обществознание", "биология", "химия", "история", "ОГЭ математика"]
        }
        # сохраняем начальные гайды в файл, чтобы не ругались при следующем запуске
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
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def kb_guide_buttons(guide: dict, user_progress: dict):
    """
    Формирует InlineKeyboard для гайда новичка.
    Теперь только две кнопки: пройти тест и я прошёл тест.
    """
    guide_id = guide["id"]
    prog = user_progress.setdefault(guide_id, {"test_done": False})

    buttons = []

    # Кнопка "Пройти тест"
    if guide.get("test_url"):
        buttons.append([InlineKeyboardButton(text="📝 Пройти тест", url=guide["test_url"])])

    # Кнопка "Я прошёл тест"
    if not prog["test_done"]:
        buttons.append([InlineKeyboardButton(text="✅ Я прошёл тест", callback_data=f"testdone:{guide_id}")])

    return InlineKeyboardMarkup(inline_keyboard=buttons)




# ====== Утилиты ======
def user(obj):
    """Возвращает словарь пользователя"""
    uid = str(obj.from_user.id)
    if uid not in USERS:
        USERS[uid] = {
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
    return USERS[uid]



@dp.callback_query(F.data.startswith("read:"))
async def newbie_mark_read(cb: CallbackQuery):
    u = user(cb)
    guide_id = cb.data.split(":")[1]
    prog = u.setdefault("progress", {}).setdefault(guide_id, {"read": False, "task_done": False, "test_done": False})
    prog["read"] = True
    save_users(USERS)
    await cb.answer("Прочитано ✅")
    await send_guide(cb.from_user.id)

@dp.callback_query(F.data.startswith("task:"))
async def newbie_mark_task(cb: CallbackQuery):
    u = user(cb)
    guide_id = cb.data.split(":")[1]
    prog = u.setdefault("progress", {}).setdefault(guide_id, {"read": True, "task_done": False, "test_done": False})
    prog["task_done"] = True
    save_users(USERS)
    await cb.answer("Задание отмечено ✅")
    await send_guide(cb.from_user.id)


@dp.callback_query(F.data.startswith("testdone:"))
async def newbie_test_done(cb: CallbackQuery):
    u = user(cb)
    guide_id = cb.data.split(":")[1]

    # Отмечаем тест как пройденный
    u["progress"].setdefault(guide_id, {})["test_done"] = True
    save_users(USERS)

    await cb.answer("🎉 Тест отмечен как пройденный!")

    # Переходим к следующему гайду
    u["guide_index"] = u.get("guide_index", 0) + 1
    save_users(USERS)

    items = GUIDES["newbie"]
    if u["guide_index"] >= len(items):
        await bot.send_message(cb.from_user.id, "🎉 Все гайды пройдены! Доступен финальный тест.", reply_markup=kb_final_test())
    else:
        guide = items[u["guide_index"]]
        kb = kb_guide_buttons(guide, u["progress"])
        await bot.send_message(
            cb.from_user.id,
            f"📘 Гайд {guide['num']}: {guide['title']}\n\n{guide['text']}\n🔗 {guide.get('url', '')}",
            reply_markup=kb
        )


@dp.callback_query(F.data == "newbie:final")
async def newbie_final_test(cb: CallbackQuery):
    u = user(cb)
    u["guide_index"] = len(GUIDES["newbie"])
    save_users(USERS)
    await cb.answer("🎉 Поздравляем! Вы прошли все гайды и финальный тест!")
    await bot.send_message(cb.from_user.id, "🏆 Курс завершён! Теперь вы полностью прошли обучение.")

# ============== ХЕНДЛЕРЫ: РЕГИСТРАЦИЯ / ДАННЫЕ ==============

@dp.message(CommandStart())
async def start(message: Message):
    u = user(message)
    u["awaiting_fio"] = True
    save_users(USERS)
    await message.answer(
        "👋 Привет! Я бот-куратор.\nНапиши, пожалуйста, свою 🎉фамилию и имя (ФИО)."
    )

# ===== Выбор предмета =====
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
        f"📘 Предмет сохранён: {subj}\nТеперь выбери свою роль:",
        reply_markup=kb_role()
    )
    await cb.answer()


# ===== Выбор роли =====
@dp.callback_query(F.data.startswith("role:"))
async def role_set(cb: CallbackQuery):
    u = user(cb)
    role = cb.data.split(":")[1]

    # Летник
    if role == "letnik":
        u["awaiting_code"] = True
        u["role"] = None  # роль до ввода кода
        save_users(USERS)
        await cb.message.answer("🔑 Введи код доступа для летников:")
        await cb.answer()
        return

    # Новичок
    elif role == "newbie":
        u["awaiting_code"] = True
        u["role"] = None  # роль до ввода кода
        save_users(USERS)
        await cb.message.answer("🔑 Введи код доступа для новичков:")
        await cb.answer()
        return
@dp.message(F.text)
async def handle_text(message: Message):
    u = user(message)
    uid = message.from_user.id
    text = (message.text or "").strip()

    # ===== Ввод ФИО =====
    if u.get("awaiting_fio"):
        u["fio"] = text
        u["awaiting_fio"] = False
        u["awaiting_subject"] = True
        u.setdefault("status", "Старт обучения")
        save_users(USERS)
        gs_log_event(uid, u["fio"], u.get("role",""), u.get("subject",""), "ФИО введено")
        gs_upsert_summary(uid, u)

        await message.answer(
            f"✅ ФИО сохранено: {text}\nТеперь выбери предмет:",
            reply_markup=kb_subjects()
        )
        return  # стоп, дальше код ФИО не идёт

    # ===== Ввод кода для летника или новичка =====
    if u.get("awaiting_code"):
        if text.lower() == NEWBIE_CODE.lower():
            u["awaiting_code"] = False
            u["role"] = "newbie"
            u["status"] = "Новичок (код подтвержден)"
            save_users(USERS)
            gs_log_event(uid, u.get("fio",""), "newbie", u.get("subject",""), "Код подтвержден")
            gs_upsert_summary(uid, u)
            await message.answer(
                "🔓 Код верный. Добро пожаловать, новичок!",
                reply_markup=kb_main("newbie")
            )
            return
        elif text == LETL_CODE:
            u["awaiting_code"] = False
            u["role"] = "letnik"
            u["status"] = "Летник (код подтвержден)"
            save_users(USERS)
            gs_log_event(uid, u.get("fio",""), "letnik", u.get("subject",""), "Код подтвержден")
            gs_upsert_summary(uid, u)
            await message.answer(
                "🔓 Код верный. Доступ открыт.",
                reply_markup=kb_main("letnik")
            )
            return
        else:
            await message.answer("❌ Неверный код. Попробуй ещё раз.")
            return


@dp.message(F.text)
async def handle_text(message: Message):
    u = user(message)
    uid = message.from_user.id
    text = (message.text or "").strip()

    # Ввод кода для летника или новичка
    if u.get("awaiting_code"):
        if text.lower() == NEWBIE_CODE.lower():
            u["awaiting_code"] = False
            u["role"] = "newbie"
            u["status"] = "Новичок (код подтвержден)"
            save_users(USERS)
            gs_log_event(uid, u.get("fio",""), "newbie", u.get("subject",""), "Код подтвержден")
            gs_upsert_summary(uid, u)
            await message.answer("🔓 Код верный. Добро пожаловать, новичок!", reply_markup=kb_main("newbie"))
            return
        elif text == LETL_CODE:
            u["awaiting_code"] = False
            u["role"] = "letnik"
            u["status"] = "Летник (код подтвержден)"
            save_users(USERS)
            gs_log_event(uid, u.get("fio",""), "letnik", u.get("subject",""), "Код подтвержден")
            gs_upsert_summary(uid, u)
            await message.answer("🔓 Код верный. Доступ открыт.", reply_markup=kb_main("letnik"))
            return
        else:
            await message.answer("❌ Неверный код. Попробуй ещё раз.")
            return
   



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

    # ✅ ВОТ ТАК должно быть
    text = (
        f"📊 Твой прогресс\n\n"
        f"ФИО: {u.get('fio','—')}\n"
        f"Роль: {role}\n"
        f"Предмет: {subj}\n"
        f"Текущий гайд (новичок): {idx}/{len(GUIDES['newbie'])}\n"
        f"Выполнено заданий: {done_tasks}\n"
        f"Пройдено тестов: {done_tests}\n"
    )
    await cb.message.answer(text)
    await cb.answer()


# ============== КАТАЛОГ (новички) ==============
@dp.callback_query(F.data == "guides:menu")
async def guides_menu(cb: CallbackQuery):
    u = user(cb)
    if u.get("role") == "letnik":
        # Для летников оставляем старый вариант
        lines = []
        for g in GUIDES["letnik"]:
            lines.append(f"• {g['title']} — {g['url']} (тест: {g.get('test_url','—')})")
        await cb.message.answer("⚡ Материалы для летников:\n\n" + "\n".join(lines))
        await cb.answer()
        return

    # Новичок
    idx = u.get("guide_index", 0)
    items = GUIDES["newbie"]

    # Все гайды пройдены
    if idx >= len(items):
        await cb.message.answer("🎉 Все гайды пройдены. Доступен финальный тест.", reply_markup=kb_final_test())
        await cb.answer()
        return

    # Показываем текущий гайд с кнопками через kb_guide_buttons
    g = items[idx]
    kb = kb_guide_buttons(g, u["progress"])
    await cb.message.answer(
        f"📘 Текущий гайд #{g['num']}: {g['title']}\n\n{g['text']}\n🔗 {g['url']}",
        reply_markup=kb
    )
    await cb.answer()




async def scheduler_loop():
    """
    Планировщик:
    1) В 14:00 и 22:00 напоминаем новичкам про дедлайн.
    """
    await asyncio.sleep(3)  # пауза после запуска

    while True:
        try:
            now = _now_msk()

            # 14:00 — напоминание новичкам о дедлайне
            if now.time().hour == 14 and now.time().minute == 0:
                for uid, u in USERS.items():
                    if u.get("role") == "newbie":
                        await bot.send_message(int(uid), "⏰ Напоминание: сдать задание сегодня до 22:00 МСК!")

            # 22:00 — финальное напоминание (после дедлайна кнопка всё равно блокируется в _is_before_deadline)
            if now.time().hour == 22 and now.time().minute == 0:
                for uid, u in USERS.items():
                    if u.get("role") == "newbie":
                        await bot.send_message(int(uid), "⏰ Дедлайн наступил. Постарайся сдавать вовремя)!")

            await asyncio.sleep(60)  # проверяем раз в минуту
        except asyncio.CancelledError:
            break
        except Exception as e:
            print("scheduler loop err:", e)
            await asyncio.sleep(5)


# ============== ХЕНДЛЕРЫ: НОВИЧКИ (прочитал / задание / финал) ==============


# ============== ХЕНДЛЕРЫ: ЛЕТНИКИ ==============
@dp.callback_query(F.data == "letnik:all")
async def letnik_all(cb: CallbackQuery):
    u = user(cb)
    if u.get("role") != "letnik":
        await cb.answer("Доступно только летникам", show_alert=True)
        return

    # один список материалов без кнопок
    lines = ["⚡ Все материалы для летников:"]
    for g in GUIDES["letnik"]:
        lines.append(f"• <b>{g['title']}</b> — {g['url']}")
    await cb.message.answer("\n".join(lines))

    # добавляем только финальный тест
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Пройти финальный тест", callback_data="letnik:final")]
    ])
    await cb.message.answer("Когда изучишь материалы — пройди финальный тест:", reply_markup=kb)

    gs_log_event(cb.from_user.id, u.get("fio",""), "letnik", u.get("subject",""), "Выданы материалы летнику")
    await cb.answer()


@dp.callback_query(F.data == "letnik:final")
async def letnik_final(cb: CallbackQuery):
    u = user(cb)
    if u.get("role") != "letnik":
        await cb.answer("Только для летников", show_alert=True)
        return

    # выдаём ссылку на финальный тест
    await cb.message.answer("📝 Финальный тест для летников: https://docs.google.com/forms/d/e/1FAIpQLSd3OSHI2tOQINP7jhuQKD3Kbc9A3t2b-nKpoglDGvhIXv9gnw/viewform?usp=header")

    # кнопка «Я прошёл финальный тест»
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Я прошёл финальный тест", callback_data="letnik:final:done")]
    ])
    await cb.message.answer("Когда пройдёшь — нажми кнопку ниже.", reply_markup=kb)
    await cb.answer()


@dp.callback_query(F.data == "letnik:final:done")
async def letnik_final_done(cb: CallbackQuery):
    u = user(cb)
    u["status"] = "Обучение завершено (летник)"
    u["finished_at"] = _now_msk().isoformat()
    save_users(USERS)
    gs_log_event(cb.from_user.id, u.get("fio",""), "letnik", u.get("subject",""), "Финальный тест пройден (летник)")
    gs_upsert_summary(cb.from_user.id, u)

    await cb.message.answer("🎉 Поздравляем! Ты прошёл обучение как летник. Добро пожаловать в команду!")
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
                        await bot.send_message(int(uid), "⏰ Дедлайн наступил! Постарайся сдавать до 22:00, чтобы быть в ритме обучения 😉.")

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























































































































































































































































