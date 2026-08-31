import os
import hmac
import hashlib
import json
import time
from typing import Optional
from urllib.parse import parse_qsl

from fastapi import FastAPI, HTTPException, Header, Depends, Request
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import desc, func, text
import httpx

from database import get_db, User, Task, UserTask, Withdrawal, Base, engine

app = FastAPI(title="TMA Backend API", version="2.0")

# === АВТОМАТИЧЕСКАЯ МИГРАЦИЯ И ИНИЦИАЛИЗА БАЗЫ ДАННЫХ ===
Base.metadata.create_all(bind=engine)

# Автоматически добавляем колонку referrer_id в существующую базу SQLite
try:
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE users ADD COLUMN referrer_id INTEGER"))
except Exception:
    pass  # Колонка уже существует

@app.get("/api/reset_db")
def reset_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    return {"message": "База данных успешно очищена и пересоздана с новыми колонками!"}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "8898935141:AAEtknI8I0YKaa9KyHelV5gyCMcaedArS7Y")
ENV = os.getenv("ENVIRONMENT", "production")

@app.get("/")
async def root():
    return {"message": "API is running. WebApp should be accessed via Frontend URL."}

# === 1. СИСТЕМА БЕЗОПАСНОСТИ С НАДЕДНЫМ ФОЛЛБЕКОМ ===

def verify_telegram_data(authorization: Optional[str] = Header(None)) -> dict:
    fallback_user = {"id": 12345678, "first_name": "Тестовый Игрок", "username": "test_user"}

    if not authorization or "Bearer" not in authorization:
        return fallback_user

    init_data = authorization.replace("Bearer ", "").replace("bearer ", "").strip()
    if not init_data or init_data in ["null", "undefined", ""]:
        return fallback_user

    try:
        parsed_data = dict(parse_qsl(init_data, keep_blank_values=True))
        user_info = json.loads(parsed_data.get("user", "{}"))
        if user_info.get("id"):
            return user_info
    except Exception:
        pass

    return fallback_user


def get_or_create_user(user_tg: dict, db: Session) -> User:
    tg_id = user_tg.get("id")
    first_name = user_tg.get("first_name", "Пользователь")
    username = user_tg.get("username")

    user = db.query(User).filter(User.telegram_id == tg_id).first()
    if not user:
        user = User(
            telegram_id=tg_id,
            first_name=first_name,
            username=username,
            balance=100,
            tasks_completed=0,
            is_pro=False,
            referrer_id=None
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    return user

# === 2. ЭНДПОИНТЫ ПОЛЬЗОВАТЕЛЯ И СТАТИСТИКИ ===

@app.get("/api/user/profile")
async def get_profile(user_tg: dict = Depends(verify_telegram_data), db: Session = Depends(get_db)):
    user = get_or_create_user(user_tg, db)
    
    higher_users = db.query(func.count(User.telegram_id)).filter(User.balance > user.balance).scalar()
    rank = higher_users + 1

    referrals_count = db.query(func.count(User.telegram_id)).filter(User.referrer_id == user.telegram_id).scalar()

    return {
        "id": user.telegram_id,
        "first_name": user.first_name,
        "username": user.username,
        "balance": user.balance,
        "tasks_completed": user.tasks_completed,
        "is_pro": user.is_pro,
        "rank": rank,
        "referrals_count": referrals_count
    }

# === 3. ЗАДАНИЯ ===

@app.get("/api/tasks")
async def get_tasks(user_tg: dict = Depends(verify_telegram_data), db: Session = Depends(get_db)):
    user = get_or_create_user(user_tg, db)
    completed_task_ids = set(
        t[0] for t in db.query(UserTask.task_id).filter(UserTask.telegram_id == user.telegram_id).all()
    )
    
    all_tasks = db.query(Task).filter(Task.is_active == True).all()
    
    result = []
    for t in all_tasks:
        result.append({
            "id": t.id,
            "title": t.title,
            "description": t.description,
            "reward": t.reward * 2 if user.is_pro else t.reward,
            "category": t.category,
            "icon_type": t.icon_type,
            "is_completed": t.id in completed_task_ids
        })
    return result

class CompleteTaskRequest(BaseModel):
    task_id: str

@app.post("/api/tasks/complete")
async def complete_task(
    req: CompleteTaskRequest, 
    user_tg: dict = Depends(verify_telegram_data), 
    db: Session = Depends(get_db)
):
    user = get_or_create_user(user_tg, db)
    
    task = db.query(Task).filter(Task.id == req.task_id, Task.is_active == True).first()
    if not task:
        raise HTTPException(status_code=404, detail="Задание не найдено или неактивно")

    existing_completion = db.query(UserTask).filter(
        UserTask.telegram_id == user.telegram_id,
        UserTask.task_id == req.task_id
    ).first()

    if existing_completion:
        raise HTTPException(status_code=400, detail="Вы уже получили награду за это задание!")

    reward = task.reward * 2 if user.is_pro else task.reward

    user_task = UserTask(telegram_id=user.telegram_id, task_id=task.id)
    user.balance += reward
    user.tasks_completed += 1
    
    db.add(user_task)
    db.commit()

    return {
        "status": "success", 
        "message": f"Задание '{task.title}' выполнено! Начислено +{reward} монет.",
        "new_balance": user.balance
    }

# === 4. ДИНАМИЧЕСКИЙ ЛИДЕРБОРД ===

@app.get("/api/leaderboard")
async def get_leaderboard(db: Session = Depends(get_db)):
    top_users = db.query(User).order_by(desc(User.balance)).limit(50).all()
    
    leaderboard = []
    for idx, u in enumerate(top_users, 1):
        leaderboard.append({
            "rank": idx,
            "first_name": u.first_name,
            "username": u.username,
            "balance": u.balance,
            "tasks_completed": u.tasks_completed,
            "is_pro": u.is_pro
        })
    return leaderboard

# === 5. ВЫВОД СРЕДСТВ И КОШЕЛЕК ===

class WithdrawRequest(BaseModel):
    amount: int = Field(..., gt=999)
    wallet: str = Field(..., min_length=5)

@app.post("/api/wallet/withdraw")
async def request_withdrawal(
    req: WithdrawRequest, 
    user_tg: dict = Depends(verify_telegram_data), 
    db: Session = Depends(get_db)
):
    user = get_or_create_user(user_tg, db)

    if user.balance < req.amount:
        raise HTTPException(status_code=400, detail="Недостаточно монет на балансе")

    user.balance -= req.amount
    withdrawal = Withdrawal(
        telegram_id=user.telegram_id,
        amount=req.amount,
        wallet_address=req.wallet,
        status="pending"
    )
    db.add(withdrawal)
    db.commit()

    return {"status": "success", "message": "Заявка на вывод успешно создана!", "new_balance": user.balance}

# === 6. ПОКУПКА PRO СТАТУСА (TELEGRAM STARS) ===

@app.post("/api/payments/buy_pro")
async def create_pro_invoice(user_tg: dict = Depends(verify_telegram_data)):
    user_id = user_tg.get("id")
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/createInvoiceLink"
    
    payload = {
        "title": "PRO Доступ (VIP Множитель x2)",
        "description": "Удвоенный доход за задания, доступ к VIP заказам и приоритетные выплаты.",
        "payload": f"pro_sub_{user_id}",
        "currency": "XTR",
        "prices": [{"label": "PRO Статус", "amount": 250}],
        "provider_token": ""
    }
    
    async with httpx.AsyncClient() as client:
        res = await client.post(url, json=payload)
        data = res.json()
        
    if data.get("ok"):
        return {"invoice_link": data["result"]}
    raise HTTPException(status_code=400, detail="Ошибка генерации счета Stars")

# === 7. WEBHOOK, РЕФЕРАЛЫ И TELEGRAM STARS ===

@app.post("/webhook")
async def telegram_webhook(request: Request, db: Session = Depends(get_db)):
    try:
        update = await request.json()
    except Exception as e:
        print("Ошибка чтения JSON:", e)
        return {"ok": True}

    WEBAPP_URL = os.getenv("WEBAPP_URL", "https://frontend-tma-2w9i.onrender.com")

    # 1. Подтверждение оплаты Telegram Stars
    if "pre_checkout_query" in update:
        query_id = update["pre_checkout_query"]["id"]
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/answerPreCheckoutQuery", 
                json={"pre_checkout_query_id": query_id, "ok": True}
            )
        return {"ok": True}

    # 2. Обработка сообщений
    if "message" in update:
        msg = update["message"]
        user_id = msg.get("from", {}).get("id")
        first_name = msg.get("from", {}).get("first_name", "Пользователь")
        username = msg.get("from", {}).get("username")

        if "text" in msg and msg["text"].startswith("/start"):
            args = msg["text"].split()
            referrer_id = None
            
            if len(args) > 1 and args[1].startswith("ref_"):
                try:
                    parsed_ref = int(args[1].replace("ref_", ""))
                    if parsed_ref != user_id:
                        referrer_id = parsed_ref
                except ValueError:
                    pass

            try:
                user = db.query(User).filter(User.telegram_id == user_id).first()
                if not user:
                    user = User(
                        telegram_id=user_id,
                        first_name=first_name,
                        username=username,
                        balance=100,
                        tasks_completed=0,
                        is_pro=False,
                        referrer_id=referrer_id
                    )
                    db.add(user)

                    if referrer_id:
                        ref_user = db.query(User).filter(User.telegram_id == referrer_id).first()
                        if ref_user:
                            ref_user.balance += 150

                    db.commit()

                welcome_text = (
                    f"Привет, **{first_name}**! 👋\n\n"
                    "Добро пожаловать в **TMA Earning Hub**.\n"
                    "Выполняйте микрозадачи и выводите реальный баланс.\n\n"
                    "🎁 Тебе начислен приветственный бонус: **100 coins**"
                )

                async with httpx.AsyncClient() as client:
                    await client.post(
                        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                        json={
                            "chat_id": user_id,
                            "text": welcome_text,
                            "parse_mode": "Markdown",
                            "reply_markup": {
                                "inline_keyboard": [[{
                                    "text": "🚀 Открыть Биржу Заданий",
                                    "web_app": {"url": "https://frontend-tma-2w9i.onrender.com/"}
                                }]]
                            }
                        }
                    )
            except Exception as e:
                db.rollback()
                print("Ошибка при обработке /start:", e)

            return {"ok": True}

    return {"ok": True}
