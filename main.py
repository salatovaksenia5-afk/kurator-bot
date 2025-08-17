import os
import json
import asyncio
from datetime import datetime, time
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command, TextFilter, StateFilter 
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ---------------- Конфигурация ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
PORT = int(os.getenv("PORT", 8080))
SHEET_ID = os.getenv("SHEET_ID")
SHEET_TAB = os.getenv("SHEET_TAB", "Users")
ACCESS_CODE = os.getenv("ACCESS_CODE", "1234")
CHAT_LINK_NEWBIE = os.getenv("CHAT_LINK_NEWBIE")
DEADLINE_HOUR = 22
REMIND_HOUR = 14

# ---------------- Google Sheets ----------------
def gs_connect():
    scope = ["https://www.googleapis.com/auth/spreadsheets"]
    creds_json = os.getenv("GOOGLE_CREDENTIALS")
    if not creds_json:
        raise RuntimeError("Нет GOOGLE_CREDENTIALS!")
    creds_dict = json.loads(creds_json)
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    gc = gspread.authorize(creds)
    sheet = gc.open_by_key(SHEET_ID).worksheet(SHEET_TAB)
    return sheet

def gs_set(uid: int, data: dict):
    try:
        sheet = gs_connect()
        all_values = sheet.get_all_values()
        headers = all_values[0]
        user_row = None
        for i, row in enumerate(all_values[1:], start=2):
            if row[0] == str(uid):
                user_row = i
                break
        if not user_row:
            user_row = len(all_values) + 1
            sheet.append_row([str(uid)] + [""]*(len(headers)-1))
        for key, value in data.items():
            if key not in headers:
                sheet.update_cell(1, len(headers)+1, key)
                headers.append(key)
            col = headers.index(key) + 1
            sheet.update_cell(user_row, col, str(value))
    except Exception as e:
        print(f"Ошибка при записи в Google Sheets: {e}")

# ---------------- Пользователи ----------------
USERS = {}

def save_users(users):
    global USERS
    USERS = users

def iso(dt):
    return dt.isoformat()

def now_msk():
    return datetime.now()

class Progress:
    def __init__(self, role=None, name=None, subject=None, guide_index=0, has_read_today=False,
                 task_done_dates=None, last_guide_sent_at=None, allow_letnik=False,
                 final_test_done=False, finished_at=None, hr_chat_link_sent=False, progress=None):
        self.role = role
        self.name = name
        self.subject = subject
        self.guide_index = guide_index
        self.has_read_today = has_read_today
        self.task_done_dates = task_done_dates or []
        self.last_guide_sent_at = last_guide_sent_at
        self.allow_letnik = allow_letnik
        self.final_test_done = final_test_done
        self.finished_at = finished_at
        self.hr_chat_link_sent = hr_chat_link_sent
        self.progress = progress or {}

    def to_dict(self):
        return self.__dict__

def get_user(uid: int) -> Progress:
    key = str(uid)
    if key not in USERS:
        USERS[key] = Progress().to_dict()
        save_users(USERS)
    return Progress(**USERS[key])

def put_user(uid: int, p: Progress):
    USERS[str(uid)] = p.to_dict()
    save_users(USERS)

# ---------------- GUIDES ----------------
GUIDES = {
    "newbie": [
        {"id": "n1", "title": "Гайд 1", "url": "https://example.com/1"},
        {"id": "n2", "title": "Гайд 2", "url": "https://example.com/2"},
        {"id": "n3", "title": "Гайд 3", "url": "https://example.com/3"}
    ],
    "letnik": [
        {"id": "l1", "title": "Гайд летника 1", "url": "https://example.com/l1"},
        {"id": "l2", "title": "Гайд летника 2", "url": "https://example.com/l2"}
    ],
    "tasks_third_by_subject": {
        "математика": "Решить индивидуальное задание по математике",
        "физика": "Решить индивидуальное задание по физике",
        "химия": "Решить индивидуальное задание по химии"
    }
}

# ---------------- Клавиатуры ----------------
def kb_role():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Я новичок"), KeyboardButton(text="Я летник")]], resize_keyboard=True)

def kb_subjects():
    subjects = ["математика", "физика", "химия"]
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=s.title(), callback_data=f"subject:set:{s}")] for s in subjects])

def kb_read_confirm():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📖 Я прочитал(а) гайд", callback_data="newbie:read_confirm")]])

def kb_main_newbie(p):
    rows = [[InlineKeyboardButton(text="📘 Выбрать предмет", callback_data="subject:menu")]]
    if p.subject:
        rows.append([InlineKeyboardButton(text="📖 Открыть гайд", callback_data="newbie:open_guide")])
        rows.append([InlineKeyboardButton(text="✅ Я выполнил задание", callback_data="newbie:task_done")])
        rows.append([InlineKeyboardButton(text="🎓 Финальный тест", callback_data="final_test")])
    rows.append([InlineKeyboardButton(text="📊 Прогресс", callback_data="progress:me")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_main_letnik(p):
    rows = [[InlineKeyboardButton(text="📘 Выбрать предмет", callback_data="subject:menu")]]
    if p.allow_letnik:
        rows.append([InlineKeyboardButton(text="⚡ Все гайды летника", callback_data="letnik:all")])
    else:
        rows.append([InlineKeyboardButton(text="🔒 Ввести код доступа", callback_data="letnik:code")])
    rows.append([InlineKeyboardButton(text="📊 Прогресс", callback_data="progress:me")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

# ---------------- FSM ----------------
class RegStates(StatesGroup):
    waiting_role = State()
    waiting_name = State()
    waiting_subject = State()
    waiting_letnik_code = State()

# ---------------- Бот ----------------
bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher()

# ---------------- Обработчики ----------------
@dp.message(Command("start"))
async def cmd_start(m: types.Message, state: FSMContext):
    await state.set_state(RegStates.waiting_role)
    await m.answer("Привет! Выбери свою роль:", reply_markup=kb_role())

@dp.message(RegStates.waiting_role, Text(equals=["Я новичок","Я летник"], ignore_case=True))
async def choose_role(m: types.Message, state: FSMContext):
    p = get_user(m.from_user.id)
    p.role = "newbie" if m.text.lower() == "я новичок" else "letnik"
    put_user(m.from_user.id, p)
    await state.set_state(RegStates.waiting_name)
    await m.answer("Отправь фамилию и имя одной строкой:")

@dp.message(RegStates.waiting_name, F.text.len() >= 3)
async def take_name(m: types.Message, state: FSMContext):
    p = get_user(m.from_user.id)
    p.name = " ".join(m.text.strip().split())
    put_user(m.from_user.id, p)
    await state.set_state(RegStates.waiting_subject)
    await m.answer("Выбери предмет:", reply_markup=kb_subjects())

@dp.callback_query(Text(startswith="subject:set:"))
async def subject_set(cb: types.CallbackQuery):
    s = cb.data.split(":",2)[2]
    p = get_user(cb.from_user.id)
    p.subject = s
    put_user(cb.from_user.id, p)
    try: gs_set(cb.from_user.id, {"Предмет": s})
    except: pass
    await cb.answer("Сохранено")
    if p.role == "newbie":
        await cb.message.answer(f"📘 Предмет <b>{s}</b> сохранён", reply_markup=kb_main_newbie(p))
        if CHAT_LINK_NEWBIE and not p.hr_chat_link_sent:
            p.hr_chat_link_sent = True
            put_user(cb.from_user.id, p)
            try: gs_set(cb.from_user.id, {"В чате новичков": "ссылка отправлена"})
            except: pass
            await cb.message.answer(f"👋 Ссылка в чат новичков: {CHAT_LINK_NEWBIE}")
    else:
        await cb.message.answer(f"📘 Предмет <b>{s}</b> сохранён", reply_markup=kb_main_letnik(p))

# ---------------- Новичковые функции ----------------
def can_submit_now():
    return now_msk().hour < DEADLINE_HOUR

def _today_iso_date():
    return now_msk().date().isoformat()
def _third_guide_task_for_subject(subject: str) -> str:
    return GUIDES["tasks_third_by_subject"].get(subject or "", "Индивидуальное задание для 3-го гайда")

async def send_newbie_task(uid: int, p: Progress):
    idx = p.guide_index
    items = GUIDES["newbie"]
    if idx >= len(items):
        await bot.send_message(uid, "🎉 Все гайды пройдены.")
        return
    g = items[idx]
    task_text = _third_guide_task_for_subject(p.subject) if g["id"]=="n3" else "Сделай задание по гайду"
    deadline_note = f"Сдать до {DEADLINE_HOUR}:00" if can_submit_now() else "Дедлайн прошёл"
    await bot.send_message(uid, f"📝 Задание к «{g['title']}»:\n{task_text}\n{deadline_note}")

# ---------------- Отметка выполнения ----------------
@dp.callback_query(Text("newbie:read_confirm"))
async def newbie_mark_read(cb: types.CallbackQuery):
    p = get_user(cb.from_user.id)
    p.has_read_today = True
    put_user(cb.from_user.id, p)
    await cb.message.answer("Гайд помечен как прочитанный. Вот задание:")
    await send_newbie_task(cb.from_user.id, p)
    await cb.answer()

@dp.callback_query(Text("newbie:task_done"))
async def newbie_task_done(cb: types.CallbackQuery):
    p = get_user(cb.from_user.id)
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
    try: gs_set(cb.from_user.id, {f"Задание {p.guide_index}": "выполнено"})
    except: pass
    await cb.message.answer("✅ Задание принято! Новый гайд придёт после 08:00 по МСК.")
    await cb.answer()

# ---------------- Финальный тест ----------------
@dp.callback_query(Text("final_test"))
async def final_test(cb: types.CallbackQuery):
    p = get_user(cb.from_user.id)
    if p.role != "newbie":
        await cb.answer("Финальный тест — для новичков.", show_alert=True)
        return
    p.final_test_done = True
    p.finished_at = iso(now_msk())
    put_user(cb.from_user.id, p)
    try:
        gs_set(cb.from_user.id, {"Финальный тест":"✓","Дата окончания":_today_iso_date(),"Статус":"Завершил обучение"})
    except: pass
    await cb.message.answer("🎓 Поздравляем! Ты завершил обучение. Свяжись со старшим куратором.")
    await cb.answer()

# ---------------- Прогресс ----------------
@dp.callback_query(Text("progress:me"))
async def progress_me(cb: types.CallbackQuery):
    p = get_user(cb.from_user.id)
    if p.role=="newbie":
        total = len(GUIDES["newbie"])
        done = min(p.guide_index,total)
        await cb.message.answer(f"📊 Прогресс новичка:\nПредмет: {p.subject}\nГайды пройдены: {done}/{total}\nЗаданий сдано: {len(p.task_done_dates)}")
    else:
        await cb.message.answer(f"📊 Прогресс летника:\nПредмет: {p.subject}\nДоступ к гайдам: {'да' if p.allow_letnik else 'нет'}")
    await cb.answer()

# ---------------- Планировщик ----------------
async def scheduler_loop():
    while True:
        now = now_msk()
        for uid, raw in list(USERS.items()):
            p = Progress(**raw)
            if p.role != "newbie": continue
            today = now.date()
            if not p.last_guide_sent_at or datetime.fromisoformat(p.last_guide_sent_at).date()!=today:
                if now.hour>=8:
                    idx = p.guide_index
                    items = GUIDES["newbie"]
                    if idx<len(items):
                        g = items[idx]
                        await bot.send_message(int(uid), f"📘 Гайд {g['title']}: {g['url']}")
                        p.last_guide_sent_at = iso(now)
                        put_user(uid, p)
            if now.hour==REMIND_HOUR and now.minute==0:
                await bot.send_message(int(uid), f"⏰ Напоминание: сдать задание до {DEADLINE_HOUR}:00")
        await asyncio.sleep(60)

# ---------------- Веб-сервер ----------------
async def handle_root(request): return web.Response(text="kurator-bot ok")
async def handle_health(request): return web.json_response({"status":"ok","ts":iso(now_msk())})
async def start_web_app():
    app = web.Application()
    app.add_routes([web.get("/", handle_root), web.get("/health", handle_health)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner,"0.0.0.0",PORT)
    await site.start()

# ---------------- Main ----------------
async def main():
    await bot.delete_webhook()
    await asyncio.gather(start_web_app(), dp.start_polling(bot, skip_updates=True), scheduler_loop())

if __name__=="__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped by user")


    






