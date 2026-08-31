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
from sqlalchemy import desc, func
import httpx

from database import get_db, User, Task, UserTask, Withdrawal

app = FastAPI(title="TMA Backend API", version="2.0")

@app.get("/")
async def root():
    return {"message": "API is running. WebApp should be accessed via Frontend URL."}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://frontend-tma-2w9i.onrender.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "8898935141:AAEtknI8I0YKaa9KyHelV5gyCMcaedArS7Y")
ENV = os.getenv("ENVIRONMENT", "production")

@app.get("/")
async def serve_index():
    return FileResponse("index.html")

# === 1. СИСТЕМА БЕЗОПАСНОСТИ (HMAC-SHA256 & ANTI-REPLAY) ===

def verify_telegram_data(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization:
        if ENV == "development":
            return {"id": 12345678, "first_name": "Dev User", "username": "dev_user"}
        raise HTTPException(status_code=401, detail="Авторизационные данные отсутствуют")

    init_data = authorization.replace("Bearer ", "").strip()
    if not init_data:
        raise HTTPException(status_code=401, detail="Пустой токен авторизации")

    try:
        parsed_data = dict(parse_qsl(init_data, strict_parsing=True))
    except Exception:
        raise HTTPException(status_code=400, detail="Неверная структура initData")

    if "hash" not in parsed_data:
        raise HTTPException(status_code=401, detail="Отсутствует криптографическая подпись")

    received_hash = parsed_data.pop("hash")
    
    # Защита от Replay Attack (данные устаревают через 24 часа)
    auth_date = int(parsed_data.get("auth_date", 0))
    if time.time() - auth_date > 86400 and ENV != "development":
        raise HTTPException(status_code=403, detail="Срок действия сессии истек. Перезапустите приложение.")

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed_data.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(calculated_hash, received_hash):
        raise HTTPException(status_code=403, detail="Нарушена целостность данных (попытка взлома)")

    try:
        user_info = json.loads(parsed_data.get("user", "{}"))
        if not user_info.get("id"):
            raise ValueError
        return user_info
    except Exception:
        raise HTTPException(status_code=400, detail="Невалидный объект пользователя в initData")


def get_or_create_user(user_tg: dict, db: Session) -> User:
    tg_id = user_tg.get("id")
    user = db.query(User).filter(User.telegram_id == tg_id).first()
    if not user:
        user = User(
            telegram_id=tg_id,
            first_name=user_tg.get("first_name", "Исполнитель"),
            username=user_tg.get("username"),
            balance=100, # Приветственный бонус
            tasks_completed=0,
            is_pro=False
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    return user

# === 2. ЭНДПОИНТЫ ПОЛЬЗОВАТЕЛЯ И СТАТИСТИКИ ===

@app.get("/api/user/profile")
async def get_profile(user_tg: dict = Depends(verify_telegram_data), db: Session = Depends(get_db)):
    user = get_or_create_user(user_tg, db)
    
    # Вычисление места в рейтинге
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

# === 3. ЗАДАНИЯ С БЕЗОПАСНЫМ ЗАЧИСЛЕНИЕМ ===

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
            "reward": t.reward * 2 if user.is_pro else t.reward, # PRO множитель x2
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
        raise HTTPException(status_code=444, detail="Задание не найдено или неактивно")

    # Проверка на повторное выполнение (Анти-накрутка)
    existing_completion = db.query(UserTask).filter(
        UserTask.telegram_id == user.telegram_id,
        UserTask.task_id == req.task_id
    ).first()

    if existing_completion:
        raise HTTPException(status_code=400, detail="Вы уже получили награду за это задание!")

    reward = task.reward * 2 if user.is_pro else task.reward

    # Атомарная транзакция
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
    amount: int = Field(..., gt=999) # Мин. вывод 1000 монет
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

    # Защита от дублирующих кликов
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
        "description": "Удвоенный доход за задания, доступ к VIP заказывам и приоритетные выплаты.",
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

# === 7. WEBHOOK И РЕФЕРАЛЬНАЯ СИСТЕМА ===

@app.post("/webhook")
async def telegram_webhook(request: Request, db: Session = Depends(get_db)):
    try:
        update = await request.json()
    except Exception:
        return {"ok": False}
    
    if "pre_checkout_query" in update:
        query_id = update["pre_checkout_query"]["id"]
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/answerPreCheckoutQuery", 
                json={"pre_checkout_query_id": query_id, "ok": True}
            )
        return {"ok": True}
        
    if "message" in update:
        msg = update["message"]
        user_id = msg.get("from", {}).get("id")

        if "successful_payment" in msg:
            user = db.query(User).filter(User.telegram_id == user_id).first()
            if user:
                user.is_pro = True
                db.commit()
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                    json={"chat_id": user_id, "text": "💎 PRO Статус активирован! Теперь ваш доход за все задания удвоен (x2)!"}
                )
            return {"ok": True}

        if "text" in msg and msg["text"].startswith("/start"):
            args = msg["text"].split()
            referrer_id = None
            if len(args) > 1 and args[1].startswith("ref_"):
                try:
                    referrer_id = int(args[1].replace("ref_", ""))
                except ValueError:
                    pass

            user = db.query(User).filter(User.telegram_id == user_id).first()
            if not user:
                user = User(
                    telegram_id=user_id,
                    first_name=msg["from"].get("first_name", "User"),
                    username=msg["from"].get("username"),
                    balance=100,
                    referrer_id=referrer_id if referrer_id != user_id else None
                )
                db.add(user)
                if referrer_id and referrer_id != user_id:
                    ref_user = db.query(User).filter(User.telegram_id == referrer_id).first()
                    if ref_user:
                        ref_user.balance += 150 # Бонус рефереру
                db.commit()

            webapp_url = os.getenv("WEBAPP_URL", "https://tma-api-uz.onrender.com")
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                    json={
                        "chat_id": user_id,
                        "text": "⚡ **Добро пожаловать в TMA Earning Hub!**\n\nВыполняйте задания, прокачивайте статус и выводите реальный баланс.",
                        "parse_mode": "Markdown",
                        "reply_markup": {
                            "inline_keyboard": [[{
                                "text": "🚀 Открыть Приложение",
                                "web_app": {"url": webapp_url}
                            }]]
                        }
                    }
                )
            return {"ok": True}

    return {"ok": True}
