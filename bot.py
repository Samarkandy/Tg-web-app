import os
import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from database import SessionLocal, User

BOT_TOKEN = os.getenv("BOT_TOKEN", "8898935141:AAEtknI8I0YKaa9KyHelV5gyCMcaedArS7Y")
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://tma-api-uz.onrender.com")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

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
        user = db.query(User).filter(User.telegram_id == user_id).first()
        if not user:
            user = User(
                telegram_id=user_id,
                first_name=message.from_user.first_name,
                username=message.from_user.username,
                balance=100,
                referrer_id=referrer_id if referrer_id != user_id else None
            )
            db.add(user)
            if referrer_id and referrer_id != user_id:
                ref_user = db.query(User).filter(User.telegram_id == referrer_id).first()
                if ref_user:
                    ref_user.balance += 150
            db.commit()
    finally:
        db.close()

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚡ Запустить Приложение",
                    web_app=WebAppInfo(url=WEBAPP_URL)
                )
            ]
        ]
    )
    await message.answer(
        "👋 **Добро пожаловать в биржу микрозаданий!**\n\n"
        "Выполняйте задания, копите монеты и заказывайте выплаты.",
        reply_markup=kb,
        parse_mode="Markdown"
    )

@dp.pre_checkout_query()
async def process_pre_checkout_query(pre_checkout_query: types.PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def process_successful_payment(message: types.Message):
    user_id = message.from_user.id
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.telegram_id == user_id).first()
        if user:
            user.is_pro = True
            db.commit()
    finally:
        db.close()
    await message.answer("🎉 **PRO Статус успешно активирован!** Доход x2 включен.")

async def main():
    print("Бот запущен в режиме Long Polling!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())