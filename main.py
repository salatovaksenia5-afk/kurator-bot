import os
import asyncio
import json
from datetime import datetime, timedelta, time, timezone

from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
)

# -----------------------
# НАСТРОЙКИ / КОНСТАНТЫ
# -----------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("Нет BOT_TOKEN. Добавь переменную окружения BOT_TOKEN на Render.")

ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or "0")  # ID админа
TIMEZONE = timezone(timedelta(hours=3))  # МСК
REMIND_HOURS = [14, 21]  # напоминания в 14:00 и 21:00
PORT = int(os.getenv("PORT", "10000"))

DATA_DIR = "data"
USERS_FILE = os.path.join(DATA_DIR, "users.json")
GUIDES_FILE = os.path.join(DATA_DIR, "guides.json")

os.makedirs(DATA_DIR, exist_ok=True)

# -----------------------
# ПАМЯТЬ (JSON "БД")
# -----------------------
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
        u.setdefault("fio", None)               # ФИО пользователя
        u.setdefault("role", "newbie")           # newbie / letnik
        u.setdefault("subject", None)            # выбранный предмет
        u.setdefault("guide_index", 0)           # номер текущего гайда
        u.setdefault("last_guide_sent_at", None) # ISO строка
        u.setdefault("progress", {})             # {guide_id: {"read": bool, "task_done": bool}}
        u.setdefault("created_at", datetime.now(TIMEZONE).isoformat())
    return data

def save_users(data):
    _write_json(USERS_FILE, data)

def load_guides():
    data = _read_json(GUIDES_FILE, {})
    if not data:
        data = {
            "newbie": [
                {"id": "n1", "title": "Гайд 1", "url": "https://example.com/newbie-1"},
                {"id": "n2", "title": "Гайд 2", "url": "https://example.com/newbie-2"},
                {"id": "n3", "title": "Гайд 3 (с предметом)", "url": "https://example.com/newbie-3"}
            ],
            "letnik": [
                {"id": "l1", "title": "Гайд летник 1", "url": "https://example.com/letnik-1"},
                {"id": "l2", "title": "Гайд летник 2", "url": "https://example.com/letnik-2"},
                {"id": "l3", "title": "Гайд летник 3", "url": "https://example.com/letnik-3"}
            ],
            "subjects": ["информатика", "физика", "русский язык", "обществознание", "биология", "химия"]
        }
        _write_json(GUIDES_FILE, data)
    return data

USERS = load_users()
GUIDES = load_guides()

# -----------------------
# БОТ
# -----------------------
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()
# -----------------------
# КЛАВИАТУРЫ
# -----------------------
def kb_main(role: str):
    rows = []
    rows.append([InlineKeyboardButton(text="📚 Все гайды", callback_data="guides:menu")])
    rows.append([
        InlineKeyboardButton(text="🧭 Мой прогресс", callback_data="progress:me"),
        InlineKeyboardButton(text="📨 Отметить задание", callback_data="task:done")
    ])
    if role == "newbie":
        rows.append([InlineKeyboardButton(text="🕗 Мой график гайдов", callback_data="newbie:schedule")])
    if role == "letnik":
        rows.append([InlineKeyboardButton(text="⚡ Открыть все гайды", callback_data="letnik:all")])
    rows.append([InlineKeyboardButton(text="📘 Выбрать предмет", callback_data="subject:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_subjects():
    btns = []
    for s in GUIDES["subjects"]:
        btns.append([InlineKeyboardButton(text=s.title(), callback_data=f"subject:set:{s}")])
    return InlineKeyboardMarkup(inline_keyboard=btns)

def kb_guides_list(role: str):
    items = GUIDES["newbie"] if role == "newbie" else GUIDES["letnik"]
    rows = []
    for g in items:
        rows.append([InlineKeyboardButton(text=g["title"], url=g["url"])])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_yes_no(cb_yes: str, cb_no: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Да", callback_data=cb_yes),
         InlineKeyboardButton(text="Нет", callback_data=cb_no)]
    ])

# -----------------------
# УТИЛИТЫ
# -----------------------
def user(u: Message | CallbackQuery):
    uid = (u.from_user.id if isinstance(u, Message) else u.from_user.id)
    if str(uid) not in USERS:
        USERS[str(uid)] = {
            "fio": None,
            "role": "newbie",
            "subject": None,
            "guide_index": 0,
            "last_guide_sent_at": None,
            "progress": {},
            "created_at": datetime.now(TIMEZONE).isoformat()
        }
        save_users(USERS)
    return USERS[str(uid)]

def is_after_8_msk(dt: datetime):
    return dt.astimezone(TIMEZONE).time() >= time(8, 0)

async def send_newbie_next_guide(uid: int):
    u = USERS.get(str(uid))
    if not u or u.get("role") != "newbie":
        return
    idx = u.get("guide_index", 0)
    items = GUIDES["newbie"]
    if idx >= len(items):
        await bot.send_message(uid, "🎉 Все гайды для новичков пройдены!")
        return
    g = items[idx]
    # особое задание для 3-го гайда
    if g["id"] == "n3" and u.get("subject"):
        task_text = f"Задание по предмету «{u['subject']}»"
    else:
        task_text = "Выполни задание до 22:00"
    await bot.send_message(
        uid,
        f"📘 Сегодняшний гайд: <b>{g['title']}</b>\nСсылка: {g['url']}\n\n{task_text}"
    )
    u["last_guide_sent_at"] = datetime.now(TIMEZONE).isoformat()
    save_users(USERS)

async def send_letnik_all(uid: int):
    rows = []
    for g in GUIDES["letnik"]:
        rows.append(f"• <b>{g['title']}</b> — {g['url']}")
    text = "⚡ Все гайды для летников:\n\n" + "\n".join(rows)
    await bot.send_message(uid, text)
# -----------------------
# ХЕНДЛЕРЫ
# -----------------------
@dp.message(CommandStart())
async def start(message: Message):
    u = user(message)
    if not u["fio"]:
        await message.answer("Привет! Напиши, пожалуйста, свою фамилию и имя.")
        return
    await message.answer(
        "Выбери свою роль:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🟢 Я новичок", callback_data="role:newbie")],
            [InlineKeyboardButton(text="🟠 Я летник", callback_data="role:letnik")]
        ])
    )

@dp.message(F.text & (lambda m: not user(m)["fio"]))
async def set_fio(message: Message):
    u = user(message)
    u["fio"] = message.text.strip()
    save_users(USERS)
    await message.answer(
        "Спасибо! Теперь выбери свою роль:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🟢 Я новичок", callback_data="role:newbie")],
            [InlineKeyboardButton(text="🟠 Я летник", callback_data="role:letnik")]
        ])
    )

@dp.callback_query(F.data.startswith("role:"))
async def set_role(cb: CallbackQuery):
    r = cb.data.split(":")[1]
    u = user(cb)
    if r == "letnik":
        await cb.message.answer("Введите код доступа для летников:")
        u["pending_role"] = "letnik"
        save_users(USERS)
    else:
        u["role"] = "newbie"
        save_users(USERS)
        await cb.message.answer(
            "Готово! Ты отмечен как <b>новичок</b>.\n"
            "Гайды будут приходить по одному каждый день после 08:00 МСК.",
            reply_markup=kb_main("newbie")
        )
    await cb.answer()

@dp.message(F.text & (lambda m: user(m).get("pending_role") == "letnik"))
async def letnik_code(message: Message):
    u = user(message)
    if message.text.strip().lower() == "летл2025":
        u["role"] = "letnik"
        u.pop("pending_role", None)
        save_users(USERS)
        await message.answer(
            "Код верный! Ты теперь <b>летник</b>.",
            reply_markup=kb_main("letnik")
        )
    else:
        await message.answer("❌ Неверный код. Попробуй снова.")

@dp.callback_query(F.data == "guides:menu")
async def guides_menu(cb: CallbackQuery):
    u = user(cb)
    if not u["subject"]:
        await cb.message.answer("Сначала выбери предмет:", reply_markup=kb_subjects())
        await cb.answer()
        return
    await cb.message.answer("Каталог гайдов:", reply_markup=kb_guides_list(u["role"]))
    await cb.answer()

@dp.callback_query(F.data == "progress:me")
async def my_progress(cb: CallbackQuery):
    u = user(cb)
    prog = u.get("progress", {})
    role = u["role"]
    items = GUIDES["newbie"] if role == "newbie" else GUIDES["letnik"]
    lines = []
    for g in items:
        st = prog.get(g["id"], {"read": False, "task_done": False})
        emoji = "✅" if st.get("task_done") else ("📖" if st.get("read") else "⏳")
        lines.append(f"{emoji} {g['title']}")
    if not lines:
        lines = ["пока пусто"]
    subj = u.get("subject") or "не выбран"
    await cb.message.answer("📊 Твой прогресс:\n\n" + "\n".join(lines) + f"\n\nПредмет: <b>{subj}</b>")
    await cb.answer()

@dp.callback_query(F.data == "task:done")
async def task_done(cb: CallbackQuery):
    now = datetime.now(TIMEZONE).time()
    if now >= time(22, 0):
        await cb.message.answer("❌ Время сдачи заданий прошло. Кнопка недоступна.")
        await cb.answer()
        return
    u = user(cb)
    role = u["role"]
    items = GUIDES["newbie"] if role == "newbie" else GUIDES["letnik"]
    idx = u.get("guide_index", 0)
    guide = items[idx] if idx < len(items) else None
    if not guide:
        await cb.message.answer("Пока нечего отмечать.")
        await cb.answer()
        return
    prog = u.setdefault("progress", {})
    gstat = prog.setdefault(guide["id"], {"read": True, "task_done": False})
    gstat["task_done"] = True
    save_users(USERS)
    await cb.message.answer(f"✅ Задание по «{guide['title']}» отмечено как выполненное!")
    await cb.answer()

@dp.callback_query(F.data == "subject:menu")
async def subject_menu(cb: CallbackQuery):
    await cb.message.answer("Выбери предмет:", reply_markup=kb_subjects())
    await cb.answer()

@dp.callback_query(F.data.startswith("subject:set:"))
async def subject_set(cb: CallbackQuery):
    s = cb.data.split(":")[2]
    u = user(cb)
    u["subject"] = s
    save_users(USERS)
    await cb.message.answer(f"📘 Предмет сохранён: <b>{s}</b>")
    await cb.answer()
# -----------------------
# ПЛАНИРОВЩИК
# -----------------------
async def scheduler_loop():
    """
    08:00 — выдача новичкам нового гайда.
    14:00 — напоминание новичкам, что дедлайн в 22:00.
    21:00 — финальное напоминание новичкам.
    При рестарте после 8:00 — догоняем выдачу.
    """
    await asyncio.sleep(3)  # небольшая пауза после старта

    now = datetime.now(TIMEZONE)
    if is_after_8_msk(now):
        for uid, u in USERS.items():
            if u.get("role") != "newbie":
                continue
            last = u.get("last_guide_sent_at")
            last_date = None
            if last:
                try:
                    last_date = datetime.fromisoformat(last).astimezone(TIMEZONE).date()
                except Exception:
                    last_date = None
            if last_date != now.date():
                try:
                    await send_newbie_next_guide(int(uid))
                except Exception:
                    pass

    while True:
        try:
            now = datetime.now(TIMEZONE)
            hh, mm = now.hour, now.minute

            # 08:00 — новый гайд новичкам
            if hh == 8 and mm == 0:
                for uid, u in USERS.items():
                    if u.get("role") == "newbie":
                        last = u.get("last_guide_sent_at")
                        last_date = None
                        if last:
                            try:
                                last_date = datetime.fromisoformat(last).astimezone(TIMEZONE).date()
                            except Exception:
                                last_date = None
                        if last_date != now.date():
                            await send_newbie_next_guide(int(uid))

            # 14:00 — напоминание про дедлайн
            if hh == 14 and mm == 0:
                for uid, u in USERS.items():
                    if u.get("role") == "newbie":
                        await bot.send_message(int(uid), "⏰ Напоминание: сдать задание до 22:00 по МСК!")

            # 21:00 — финальное напоминание
            if hh == 21 and mm == 0:
                for uid, u in USERS.items():
                    if u.get("role") == "newbie":
                        await bot.send_message(int(uid), "⚠️ Последний шанс сдать задание! До 22:00 кнопка будет доступна.")

            await asyncio.sleep(60)

        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(5)

# -----------------------
# ВЕБ-СЕРВЕР ДЛЯ RENDER
# -----------------------
async def handle_root(request):
    return web.Response(text="kurator-bot ok")

async def handle_health(request):
    return web.json_response({"status": "ok", "ts": datetime.now(TIMEZONE).isoformat()})

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

# -----------------------
# MAIN
# -----------------------
async def main():
    await start_web_app()          # веб-сервер для Render
    asyncio.create_task(scheduler_loop())  # планировщик
    await dp.start_polling(bot)    # запуск бота

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
