import os
import asyncio
import json
import logging
from html import escape
from datetime import datetime, timedelta, timezone

from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
)

from gsheets import gs_log_event

logging.basicConfig(level=logging.INFO)

MSK = timezone(timedelta(hours=3))  # Московское время UTC+3


def _now_msk():
    return datetime.now(MSK)


# ============== НАСТРОЙКИ / КОНСТАНТЫ ==============
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("Нет BOT_TOKEN. Добавь переменную окружения BOT_TOKEN на Render.")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or "0")  # твой телеграм ID
PORT = int(os.getenv("PORT", "10000"))
NEWBIE_CODE = os.getenv("NEWBIE_CODE", "newbie2025")  # код для новичков

DATA_DIR = "data"
USERS_FILE = os.path.join(DATA_DIR, "users.json")
GUIDES_FILE = os.path.join(DATA_DIR, "guides.json")
os.makedirs(DATA_DIR, exist_ok=True)

# Данные стираются только если добавить переменную окружения RESET_DATA=1
if os.getenv("RESET_DATA") == "1":
    for f in [USERS_FILE, GUIDES_FILE]:
        if os.path.exists(f):
            os.remove(f)


def gs_upsert_summary(user_id, user_data):
    """Заглушка для обновления сводки в Google Sheets."""
    pass


async def log_event(*args):
    """Запись в Google Sheets в отдельном потоке: бот не «замирает» на время записи,
    а ошибка таблицы не ломает ответ пользователю."""
    try:
        await asyncio.to_thread(gs_log_event, *args)
    except Exception as e:
        print("gsheets log err:", e)


# ============== JSON "БД" ==============
def _read_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path: str, payload):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _new_user_record():
    return {
        "fio": None,
        "role": None,                 # newbie
        "subject": None,
        "guide_index": 0,             # индекс текущего гайда
        "progress": {},
        "created_at": _now_msk().isoformat(),
        "finished_at": "",
        "status": "",
        "awaiting_fio": False,
        "awaiting_subject": False,
        "awaiting_code": False,
    }


def load_users():
    data = _read_json(USERS_FILE, {})
    for uid, u in data.items():
        for k, v in _new_user_record().items():
            u.setdefault(k, v)
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
            "subjects": ["математика", "информатика", "физика", "русский язык", "обществознание", "биология", "химия", "история", "ОГЭ математика"]
        }
        _write_json(GUIDES_FILE, data)
    return data


USERS = load_users()
GUIDES = load_guides()


# ============== КЛАВИАТУРЫ (КНОПКИ) ==============
def kb_subjects():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=s.title(), callback_data=f"subject:set:{s}")]
        for s in GUIDES["subjects"]
    ])


def kb_role():
    # Осталась только кнопка «Я новичок»
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟢 Я новичок", callback_data="role:newbie")]
    ])


def kb_guide_buttons(guide: dict, show_done: bool = True):
    """Кнопки под гайдом: «Пройти тест» (ссылка) и «Я прошёл тест»."""
    buttons = []
    if guide.get("test_url"):
        buttons.append([InlineKeyboardButton(text="📝 Пройти тест", url=guide["test_url"])])
    if show_done:
        buttons.append([InlineKeyboardButton(text="✅ Я прошёл тест", callback_data=f"testdone:{guide['id']}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def guide_text(g: dict, prefix: str = "📘 Гайд"):
    return f"{prefix} {g['num']}: {g['title']}\n\n{g['text']}\n🔗 {g.get('url', '')}"


# ====== Утилиты ======
def user(obj):
    """Возвращает словарь пользователя (создаёт, если нет)."""
    uid = str(obj.from_user.id)
    if uid not in USERS:
        USERS[uid] = _new_user_record()
        save_users(USERS)
    return USERS[uid]


# ============== РЕГИСТРАЦИЯ ==============
@dp.message(CommandStart())
async def start(message: Message):
    u = user(message)
    u["awaiting_fio"] = True
    save_users(USERS)
    await message.answer(
        "👋 Привет! Я бот-куратор.\nНапиши, пожалуйста, свою 🎉фамилию и имя (ФИО)."
    )


@dp.callback_query(F.data.startswith("subject:set:"))
async def subject_set(cb: CallbackQuery):
    await cb.answer()
    u = user(cb)
    subj = cb.data.split(":")[2]
    u["subject"] = subj
    u["awaiting_subject"] = False
    save_users(USERS)

    await cb.message.answer(
        f"📘 Предмет сохранён: {escape(subj)}\nТеперь выбери свою роль:",
        reply_markup=kb_role()
    )
    await log_event(cb.from_user.id, u.get("fio", ""), u.get("role", ""), subj, "Предмет выбран")
    gs_upsert_summary(cb.from_user.id, u)


@dp.callback_query(F.data == "role:newbie")
async def role_set(cb: CallbackQuery):
    await cb.answer()
    u = user(cb)
    u["awaiting_code"] = True
    u["role"] = None  # роль появится только после ввода кода
    save_users(USERS)
    await cb.message.answer("🔑 Введи код доступа для новичков:")


# ============== ВЫДАЧА ТЕКУЩЕГО ГАЙДА ==============
async def send_current_guide(chat_id: int, u: dict):
    idx = u.get("guide_index", 0)
    items = GUIDES["newbie"]

    if idx >= len(items):
        await bot.send_message(chat_id, "🏆 Все гайды и финальный тест пройдены. Обучение завершено!")
        return

    g = items[idx]
    await bot.send_message(chat_id, guide_text(g), reply_markup=kb_guide_buttons(g))


# ============== ТЕСТ ПРОЙДЕН ==============
@dp.callback_query(F.data.startswith("testdone:"))
async def newbie_test_done(cb: CallbackQuery):
    u = user(cb)
    guide_id = cb.data.split(":")[1]
    items = GUIDES["newbie"]
    idx = u.get("guide_index", 0)

    # Защита от повторных нажатий на старое сообщение
    if idx >= len(items) or items[idx]["id"] != guide_id:
        await cb.answer("Этот гайд уже отмечен ✅")
        try:
            await cb.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    await cb.answer("🎉 Тест отмечен как пройденный!")

    u.setdefault("progress", {}).setdefault(guide_id, {})["test_done"] = True
    u["guide_index"] = idx + 1
    save_users(USERS)

    # Убираем кнопку «Я прошёл тест» из старого сообщения, ссылка на тест остаётся
    try:
        await cb.message.edit_reply_markup(reply_markup=kb_guide_buttons(items[idx], show_done=False))
    except Exception:
        pass

    if u["guide_index"] >= len(items):
        # Последний гайд (с финальным тестом) пройден — обучение завершено
        u["status"] = "Обучение завершено"
        u["finished_at"] = _now_msk().isoformat()
        save_users(USERS)
        await bot.send_message(
            cb.from_user.id,
            "🏆 Поздравляем! Ты прошёл все гайды и финальный тест. Обучение завершено!"
        )
        await log_event(cb.from_user.id, u.get("fio", ""), "newbie", u.get("subject", ""), "Финальный тест пройден")
        gs_upsert_summary(cb.from_user.id, u)
    else:
        g = items[u["guide_index"]]
        await bot.send_message(cb.from_user.id, guide_text(g), reply_markup=kb_guide_buttons(g))


# ============== КОМАНДЫ АДМИНА ==============
def _is_admin(message: Message) -> bool:
    return ADMIN_ID != 0 and message.from_user.id == ADMIN_ID


@dp.message(Command("admin"))
async def admin_panel(message: Message):
    if not _is_admin(message):
        return

    newbies = [u for u in USERS.values() if u.get("role") == "newbie"]
    lines = [
        "🔧 <b>Админ-панель</b>",
        f"👥 Всего пользователей: <b>{len(USERS)}</b>",
        f"🟢 Новичков: <b>{len(newbies)}</b>",
        ""
    ]

    last = sorted(USERS.items(), key=lambda kv: kv[1].get("created_at", ""), reverse=True)[:10]
    lines.append("🕒 Последние регистрации:")
    for uid, u in last:
        lines.append(f"{uid}: {escape(str(u.get('fio') or '—'))} | {u.get('role') or '—'} | {escape(str(u.get('subject') or '—'))} | idx={u.get('guide_index', 0)}")

    await message.answer("\n".join(lines))


@dp.message(Command("tests"))
async def admin_tests(message: Message):
    if not _is_admin(message):
        return

    lines = ["📑 <b>Сводка по тестам</b>", ""]
    last = sorted(USERS.items(), key=lambda kv: kv[1].get("created_at", ""), reverse=True)[:20]
    for uid, u in last:
        done_tests = sum(1 for v in u.get("progress", {}).values() if v.get("test_done"))
        lines.append(f"{uid}: {escape(str(u.get('fio') or '—'))} | {escape(str(u.get('subject') or '—'))} | "
                     f"тестов={done_tests}, idx={u.get('guide_index', 0)}")

    await message.answer("\n".join(lines))


# ============== ОБЫЧНЫЙ ТЕКСТ ==============
# Должен стоять ПОСЛЕ всех команд, иначе «съедает» /admin и /tests.
@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text(message: Message):
    u = user(message)
    uid = message.from_user.id
    text = (message.text or "").strip()

    # ===== Ввод ФИО =====
    if u.get("awaiting_fio"):
        u["fio"] = text
        u["awaiting_fio"] = False
        u["awaiting_subject"] = True
        u["status"] = u.get("status") or "Старт обучения"
        save_users(USERS)

        await message.answer(
            f"✅ ФИО сохранено: {escape(text)}\nТеперь выбери предмет:",
            reply_markup=kb_subjects()
        )
        await log_event(uid, u["fio"], u.get("role", ""), u.get("subject", ""), "ФИО введено")
        gs_upsert_summary(uid, u)
        return

    # ===== Ввод кода =====
    if u.get("awaiting_code"):
        if text.lower() == NEWBIE_CODE.lower():
            u["awaiting_code"] = False
            u["role"] = "newbie"
            u["status"] = "Новичок (код подтвержден)"
            save_users(USERS)
            await message.answer("🔓 Код верный. Добро пожаловать, новичок!")
            await send_current_guide(uid, u)
            await log_event(uid, u.get("fio", ""), "newbie", u.get("subject", ""), "Код подтвержден")
            gs_upsert_summary(uid, u)
        else:
            await message.answer("❌ Неверный код. Попробуй ещё раз.")
        return

    # ===== Бот ничего не ждёт — подсказываем, что делать =====
    if u.get("awaiting_subject"):
        await message.answer("Выбери предмет кнопкой:", reply_markup=kb_subjects())
    elif u.get("role") == "newbie":
        await message.answer("Я отвечаю только на кнопки 🙂 Вот твой текущий гайд:")
        await send_current_guide(uid, u)
    else:
        await message.answer("Чтобы начать, нажми /start")


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
    web_task = asyncio.create_task(start_web_app())  # чтобы Render видел открытый порт
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        import traceback
        print("❌ Ошибка при запуске:")
        traceback.print_exc()































































































































































































































