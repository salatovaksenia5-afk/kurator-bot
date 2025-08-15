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

ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or "0")  # твой телеграм ID, чтобы видеть статистику
TIMEZONE = timezone(timedelta(hours=3))  # МСК
REMIND_HOUR = 22  # 22:00 МСК
PORT = int(os.getenv("PORT", "10000"))  # Render отдаёт порт через $PORT

DATA_DIR = "data"
USERS_FILE = os.path.join(DATA_DIR, "users.json")
GUIDES_FILE = os.path.join(DATA_DIR, "guides.json")

os.makedirs(DATA_DIR, exist_ok=True)

# -----------------------
# ПАМЯТЬ (простая JSON "БД")
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
    # нормализуем
    for uid, u in data.items():
        u.setdefault("role", "newbie")           # newbie / letnik
        u.setdefault("subject", None)            # выбранный предмет
        u.setdefault("guide_index", 0)           # какой гайд по очереди (новичку)
        u.setdefault("last_guide_sent_at", None) # ISO строка МСК
        u.setdefault("progress", {})             # {guide_id: {"read": bool, "task_done": bool}}
        u.setdefault("created_at", datetime.now(TIMEZONE).isoformat())
    return data

def save_users(data):
    _write_json(USERS_FILE, data)

def load_guides():
    data = _read_json(GUIDES_FILE, {})
    # структура ожидается вот такая:
    # {
    #   "newbie": [ {"id":"n1","title":"...","url":"..."},
    #               {"id":"n2","title":"...","url":"..."}],
    #   "letnik": [ {"id":"l1","title":"...","url":"..."} ],
    #   "subjects": ["информатика","физика","русский язык","обществознание","биология","химия"]
    # }
    if not data:
        # заглушки
        data = {
            "newbie": [
                {"id": "n1", "title": "Основы и этика (заглушка)", "url": "https://example.com/newbie-1"},
                {"id": "n2", "title": "Техническая часть (заглушка)", "url": "https://example.com/newbie-2"},
                {"id": "n3", "title": "Основные проблемы (заглушка)", "url": "https://example.com/newbie-3"},
            ],
            "letnik": [
                {"id": "l1", "title": "Гайд для летников 1 (заглушка)", "url": "https://example.com/letnik-1"},
                {"id": "l2", "title": "Гайд для летников 2 (заглушка)", "url": "https://example.com/letnik-2"},
                {"id": "l3", "title": "Гайд для летников 3 (заглушка)", "url": "https://example.com/letnik-3"},
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

# клавиатуры
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

# утилиты
def user(u: Message|CallbackQuery):
    uid = (u.from_user.id if isinstance(u, Message) else u.from_user.id)
    if str(uid) not in USERS:
        USERS[str(uid)] = {
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
    await message.answer(
        "привет! я бот-куратор.\n\nВыбери свою роль:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🟢 Я новичок", callback_data="role:newbie")],
            [InlineKeyboardButton(text="🟠 Я летник", callback_data="role:letnik")]
        ])
    )

@dp.callback_query(F.data.startswith("role:"))
async def set_role(cb: CallbackQuery):
    r = cb.data.split(":")[1]
    u = user(cb)
    u["role"] = r
    save_users(USERS)
    if r == "newbie":
        await cb.message.answer(
            "Готово! Ты отмечен как <b>новичок</b>.\n"
            "Гайды будут приходить по одному каждый день после 08:00 МСК.",
            reply_markup=kb_main("newbie")
        )
    else:
        await cb.message.answer(
            "Готово! Ты <b>летник</b>.\n"
            "Могу выслать все гайды сразу. На тест — сутки.",
            reply_markup=kb_main("letnik")
        )
    await cb.answer()

@dp.callback_query(F.data == "guides:menu")
async def guides_menu(cb: CallbackQuery):
    u = user(cb)
    role = u["role"]
    await cb.message.answer(
        "каталог гайдов:",
        reply_markup=kb_guides_list(role)
    )
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
    u = user(cb)
    role = u["role"]
    items = GUIDES["newbie"] if role == "newbie" else GUIDES["letnik"]
    # помечаем последнюю высланную / текущую
    idx = u.get("guide_index", 0)
    guide = None
    if role == "newbie":
        # для новичка текущий — по индексу (не переходим дальше автоматом, пока не 8 утра)
        if idx < len(items):
            guide = items[idx]
    else:
        # для летника просто последняя прочитанная не ведём — отметим любую «активную» как l1
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
    await cb.answer()

@dp.callback_query(F.data == "newbie:schedule")
async def newbie_schedule(cb: CallbackQuery):
    u = user(cb)
    idx = u.get("guide_index", 0)
    items = GUIDES["newbie"]
    left = max(0, len(items) - idx)
    await cb.message.answer(
        f"🕗 Гайды приходят после 08:00 МСК.\nОсталось гайдов: <b>{left}</b>."
    )
    await cb.answer()

@dp.callback_query(F.data == "letnik:all")
async def letnik_all(cb: CallbackQuery):
    u = user(cb)
    if u["role"] != "letnik":
        await cb.answer("Это только для летников", show_alert=True)
        return
    await send_letnik_all(cb.from_user.id)
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

# Команда для админа — собрать простую статистику
@dp.message(Command("admin"))
async def admin_panel(message: Message):
    if ADMIN_ID and message.from_user.id != ADMIN_ID:
        return
    newbies = [u for u in USERS.values() if u["role"] == "newbie"]
    letniki = [u for u in USERS.values() if u["role"] == "letnik"]
    total = len(USERS)
    lines = [
        f"👥 Всего пользователей: {total}",
        f"🟢 Новичков: {len(newbies)}",
        f"🟠 Летников: {len(letniki)}",
        "",
        "— Топ 10 последних пользователей:"
    ]
    last = sorted(USERS.items(), key=lambda kv: kv[1].get("created_at", ""), reverse=True)[:10]
    for uid, u in last:
        subj = u.get("subject") or "—"
        lines.append(f"{uid}: {u['role']}, предмет: {subj}, индекс гайда: {u.get('guide_index',0)}")
    await message.answer("\n".join(lines))

# -----------------------
# РАСПИСАНИЕ / ЗАДАЧИ
# -----------------------
async def scheduler_loop():
    """
    1) Каждый день после 08:00 МСК — новичкам отправляем следующий гайд (если вчерашний был выслан).
    2) Каждый день в 22:00 МСК — напоминание новичкам о сдаче задания.
    3) Летникам — напоминание про «сутки на тест» (если в этот день заходили/получали гайды).
    4) Если бот «вставал» и пропустил утро — при старте тоже проверим и догоним (если >08:00).
    """
    await asyncio.sleep(3)  # маленькая задержка после запуска
    # «догоняем» утро, если бот рестартанул после 08:00
    now = datetime.now(TIMEZONE)
    if is_after_8_msk(now):
        # выдадим тем новичкам, кому сегодня ещё не высылали
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
            if last_date == now.date():
                continue  # уже сегодня высылали
            try:
                await send_newbie_next_guide(int(uid))
            except Exception:
                pass

    while True:
        try:
            now = datetime.now(TIMEZONE)

            # 08:00 — выдача новичкам нового гайда (по одному в день)
            if now.time().hour == 8 and now.time().minute == 0:
                for uid, u in USERS.items():
                    if u.get("role") != "newbie":
                        continue
                    # если сегодня ещё не отправляли
                    last = u.get("last_guide_sent_at")
                    last_date = None
                    if last:
                        try:
                            last_date = datetime.fromisoformat(last).astimezone(TIMEZONE).date()
                        except Exception:
                            last_date = None
                    if last_date == now.date():
                        continue
                    await send_newbie_next_guide(int(uid))

            # 22:00 — напоминалка новичкам
            if now.time().hour == REMIND_HOUR and now.time().minute == 0:
                for uid, u in USERS.items():
                    if u.get("role") == "newbie":
                        await bot.send_message(int(uid), "⏰ Напоминание: cдать задание до 22:00 по МСК!")
                    elif u.get("role") == "letnik":
                        await bot.send_message(int(uid), "⏰ Напоминание: у тебя сутки на тест. Не затягивай!")

            await asyncio.sleep(60)  # проверяем каждую минуту
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
    # поднимаем фейковый http-сервер (чтобы Render видел открытый порт)
    await start_web_app()

    # запускаем планировщик
    asyncio.create_task(scheduler_loop())

    # запускаем бота (polling)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

