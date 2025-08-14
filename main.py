import asyncio
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiogram.filters import Command

TOKEN = "ТОКЕН_ТВОЕГО_БОТА"

# Храним роли пользователей и задания
user_roles = {}  # {user_id: "новичок" / "летник"}
user_tasks = {}  # {user_id: {"text": "...", "expire": datetime}}

bot = Bot(token=TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()

# --- Меню ---
def main_menu():
    kb = [
        [InlineKeyboardButton(text="Я новичок", callback_data="role_new")],
        [InlineKeyboardButton(text="Я летник", callback_data="role_summer")],
        [InlineKeyboardButton(text="📚 Гайды", callback_data="guides")],
        [InlineKeyboardButton(text="✅ Я выполнил задание", callback_data="done_task")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

# --- Команда /start ---
@dp.message(Command("start"))
async def start_handler(message: types.Message):
    await message.answer("Привет! Я бот-куратор.\nВыбери, кто ты:", reply_markup=main_menu())

# --- Установка роли ---
@dp.callback_query(F.data == "role_new")
async def set_role_new(call: types.CallbackQuery):
    user_roles[call.from_user.id] = "новичок"
    await call.message.answer("Ты отмечен как <b>новичок</b>. Я буду напоминать сдать задания до 22:00 МСК.")
    await call.answer()

@dp.callback_query(F.data == "role_summer")
async def set_role_summer(call: types.CallbackQuery):
    user_roles[call.from_user.id] = "летник"
    await call.message.answer("Ты отмечен как <b>летник</b>. На тест дается сутки!")
    await call.answer()

# --- Гайды ---
@dp.callback_query(F.data == "guides")
async def guides_handler(call: types.CallbackQuery):
    role = user_roles.get(call.from_user.id)
    if role == "летник":
        await call.message.answer("Каталог гайдов:\n1) Основы и этика\n2) Техническая часть\n3) Предмет\n4) Основные проблемы")
    else:
        await call.message.answer("Гайды доступны только летникам.")
    await call.answer()

# --- Выдача задания ---
async def give_task(user_id, text, hours_to_expire):
    expire_time = datetime.now() + timedelta(hours=hours_to_expire)
    user_tasks[user_id] = {"text": text, "expire": expire_time}

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Я выполнил задание", callback_data="done_task")]
        ]
    )
    await bot.send_message(user_id, f"Задание: {text}\nВыполнить до: {expire_time.strftime('%H:%M %d.%m')}", reply_markup=kb)

    # Ждем окончания времени и удаляем задание
    await asyncio.sleep(hours_to_expire * 3600)
    if user_id in user_tasks and datetime.now() >= user_tasks[user_id]["expire"]:
        del user_tasks[user_id]
        await bot.send_message(user_id, "⏰ Срок выполнения задания вышел! Задание удалено.")

# --- Отметка выполнения задания ---
@dp.callback_query(F.data == "done_task")
async def done_task_handler(call: types.CallbackQuery):
    if call.from_user.id in user_tasks:
        del user_tasks[call.from_user.id]
        await call.message.answer("✅ Задание выполнено! Молодец!")
    else:
        await call.message.answer("❌ У тебя нет активных заданий.")
    await call.answer()

# --- Ежедневные напоминания ---
async def daily_reminder():
    while True:
        now = datetime.now()
        if now.hour == 19 and now.minute == 0:  # 22:00 МСК = 19:00 по GMT
            for user_id, role in user_roles.items():
                if role == "новичок":
                    await bot.send_message(user_id, "⏳ Напоминание: сдать задание до 22:00 МСК!")
                elif role == "летник":
                    await bot.send_message(user_id, "⏳ Напоминание: на тест остались сутки!")
        await asyncio.sleep(60)

# --- Запуск ---
async def main():
    asyncio.create_task(daily_reminder())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())



