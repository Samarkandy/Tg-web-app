import os
import asyncio
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

from database import SessionLocal, User, Task, UserTask
from logic import get_or_create_user, apply_task_reward

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN не задан. Установите переменную окружения BOT_TOKEN (см. .env.example). "
        "Старый токен уже был опубликован в этом репозитории на GitHub — считайте его "
        "скомпрометированным: зайдите в @BotFather -> /revoke и выпустите новый, прежде чем продолжать."
    )

# ИСПРАВЛЕНО: раньше здесь по умолчанию стоял URL БЭКЕНДА (tma-api-uz.onrender.com),
# из-за чего кнопка в боте открывала бы API вместо самого приложения.
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://frontend-tma-2w9i.onrender.com")
ADMIN_TELEGRAM_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "0") or "0")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def _is_admin(message: types.Message) -> bool:
    return ADMIN_TELEGRAM_ID != 0 and message.from_user.id == ADMIN_TELEGRAM_ID


@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    args = message.text.split()
    referrer_id = None
    if len(args) > 1 and args[1].startswith("ref_"):
        try:
            referrer_id = int(args[1].replace("ref_", ""))
        except ValueError:
            pass

    db = SessionLocal()
    try:
        # ИСПРАВЛЕНО: раньше рефереру начислялся бонус сразу здесь, в момент /start —
        # то есть просто за то, что кто-то открыл бота по ссылке, без единого выполненного
        # задания. Это можно было фармить, создавая новые аккаунты и просто нажимая /start.
        # Теперь бонус начисляется в apply_task_reward() — только когда приглашённый
        # реально выполнит первое задание.
        get_or_create_user(db, user_id, message.from_user.first_name, message.from_user.username, referrer_id=referrer_id)
    finally:
        db.close()

    kb = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="⚡ Запустить Приложение", web_app=WebAppInfo(url=WEBAPP_URL))
        ]]
    )
    await message.answer(
        "👋 Добро пожаловать в биржу микрозаданий!\n\n"
        "Выполняйте задания, копите монеты и заказывайте выплаты. "
        "Честная фиксированная оплата — без рулетки и без шансов.",
        reply_markup=kb,
    )


@dp.pre_checkout_query()
async def process_pre_checkout_query(pre_checkout_query: types.PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)


@dp.message(F.successful_payment)
async def process_successful_payment(message: types.Message):
    db = SessionLocal()
    try:
        user = get_or_create_user(db, message.from_user.id, message.from_user.first_name, message.from_user.username)
        user.is_pro = True
        db.commit()
    finally:
        db.close()
    await message.answer("🎉 PRO Статус успешно активирован! Доход x2 включён.")


# ==========================================
# АДМИН: проверка заданий, которые нельзя одобрить автоматически
# (ai, копирайтинг, опросы — то, что легко подделать одной кнопкой).
# Работает, только пока bot.py запущен через polling (см. заметку в конце файла).
# ==========================================
@dp.message(F.text == "/pending")
async def cmd_pending(message: types.Message):
    if not _is_admin(message):
        return
    db = SessionLocal()
    try:
        subs = db.query(UserTask).filter(UserTask.status == "pending").order_by(UserTask.completed_at).limit(10).all()
        if not subs:
            await message.answer("Нет заданий на проверке.")
            return
        for s in subs:
            task = db.query(Task).filter(Task.id == s.task_id).first()
            title = task.title if task else s.task_id
            reward = task.reward if task else "?"
            await message.answer(
                f"#{s.id} · {title} · {reward} coins\n"
                f"Пользователь id: {s.telegram_id}\n"
                f"Подтверждение: {s.proof_text}\n\n"
                f"/approve_{s.id}  /reject_{s.id}"
            )
    finally:
        db.close()


@dp.message(F.text.regexp(r"^/approve_(\d+)$"))
async def cmd_approve(message: types.Message):
    if not _is_admin(message):
        return
    sub_id = int(message.text.split("_", 1)[1])
    db = SessionLocal()
    try:
        sub = db.query(UserTask).filter(UserTask.id == sub_id, UserTask.status == "pending").first()
        if not sub:
            await message.answer(f"Заявка #{sub_id} не найдена или уже обработана.")
            return
        task = db.query(Task).filter(Task.id == sub.task_id).first()
        user = db.query(User).filter(User.telegram_id == sub.telegram_id).first()
        if not task or not user:
            await message.answer("Ошибка: задание или пользователь не найдены в базе.")
            return

        sub.status = "approved"
        sub.reviewed_at = datetime.utcnow()
        actual_reward = apply_task_reward(db, user, task.reward)
        db.commit()

        await message.answer(f"✅ Одобрено #{sub_id}. Начислено {actual_reward} coins пользователю {user.first_name}.")
        try:
            await bot.send_message(user.telegram_id, f"✅ Задание «{task.title}» одобрено! Начислено {actual_reward} coins.")
        except Exception:
            pass
    finally:
        db.close()


@dp.message(F.text.regexp(r"^/reject_(\d+)$"))
async def cmd_reject(message: types.Message):
    if not _is_admin(message):
        return
    sub_id = int(message.text.split("_", 1)[1])
    db = SessionLocal()
    try:
        sub = db.query(UserTask).filter(UserTask.id == sub_id, UserTask.status == "pending").first()
        if not sub:
            await message.answer(f"Заявка #{sub_id} не найдена или уже обработана.")
            return
        sub.status = "rejected"
        sub.reviewed_at = datetime.utcnow()
        db.commit()

        await message.answer(f"❌ Отклонено #{sub_id}.")
        try:
            await bot.send_message(sub.telegram_id, "❌ Задание не прошло проверку. Проверьте условия и попробуйте снова.")
        except Exception:
            pass
    finally:
        db.close()


async def main():
    print("Бот запущен в режиме Long Polling!")
    if ADMIN_TELEGRAM_ID == 0:
        print("⚠️  ADMIN_TELEGRAM_ID не задан в .env — команды /pending, /approve_*, /reject_* никому не доступны.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    # ЗАМЕТКА ПО ДЕПЛОЮ: main.py (веб-сервис) и bot.py (polling) — два разных процесса.
    # На Render main.py разумно держать как Web Service (принимает вебхуки Telegram),
    # а bot.py — как отдельный Background Worker, если вам нужны админ-команды
    # /pending /approve_/reject_. Если bot.py не запущен — проверка заданий с proof
    # будет копиться в статусе "pending" без возможности их одобрить.
    asyncio.run(main())
