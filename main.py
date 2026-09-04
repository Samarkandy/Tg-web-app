import os
import hmac
import hashlib
import json
import time
from datetime import datetime
from collections import defaultdict
from typing import Optional
from urllib.parse import parse_qsl

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Header, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import desc, func
from sqlalchemy.orm import Session
import httpx

from database import get_db, User, Task, UserTask, Withdrawal, Base, engine, seed_initial_tasks, UNLOCK_THRESHOLD, MIN_WITHDRAWAL
from logic import get_or_create_user, get_existing_submission, apply_task_reward

app = FastAPI(title="Tapshiriq Bozor API", version="3.0")

# ==========================================
# СЕКРЕТЫ — только из переменных окружения.
# ==========================================
# Старый токен (8898935141:...) был прописан прямо в коде и уже опубликован на GitHub —
# он скомпрометирован. Отзовите его в @BotFather -> /revoke ПЕРЕД деплоем этой версии,
# выпустите новый и положите его в переменные окружения Render (не в код).
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN не задан. Установите переменную окружения BOT_TOKEN в настройках Render "
        "(см. .env.example) — без неё сервер намеренно не запустится."
    )

ADMIN_TELEGRAM_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "0") or "0")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://frontend-tma-2w9i.onrender.com")
DEV_MODE = os.getenv("DEV_MODE", "false").lower() == "true"
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", WEBAPP_URL).split(",") if o.strip()] or ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,  # авторизация через заголовок, не через cookie — credentials не нужны
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

Base.metadata.create_all(bind=engine)


@app.on_event("startup")
def on_startup():
    seed_initial_tasks()


# ==========================================
# 1. АВТОРИЗАЦИЯ — реальная проверка подписи Telegram (HMAC-SHA256)
# ==========================================
# ВАЖНО ЧТО ТУТ БЫЛО ДО ЭТОГО: прежняя verify_telegram_data не проверяла подпись вообще —
# она просто разбирала строку initData, доставала оттуда поле "user" и доверяла ему как есть.
# Это значит, что ЛЮБОЙ человек мог отправить запрос вида
#   Authorization: Bearer user=%7B%22id%22%3A123%2C...%7D
# без реального Telegram и без подписи — и API считало бы его любым пользователем,
# включая накрутку баланса под чужим id. Теперь подпись проверяется по алгоритму Telegram,
# и без неё запрос отклоняется (кроме явного DEV_MODE для локальной разработки).
def verify_telegram_data(authorization: Optional[str] = Header(None)) -> dict:
    init_data = (authorization or "").replace("Bearer ", "").replace("bearer ", "").strip()

    if not init_data or init_data in ("null", "undefined"):
        if DEV_MODE:
            return {"id": 12345678, "first_name": "Тестовый Игрок", "username": "test_user"}
        raise HTTPException(status_code=401, detail="Отсутствуют данные авторизации Telegram")

    try:
        parsed_data = dict(parse_qsl(init_data, strict_parsing=True))
    except ValueError:
        raise HTTPException(status_code=400, detail="Неверный формат данных авторизации")

    if "hash" not in parsed_data:
        raise HTTPException(status_code=401, detail="Отсутствует подпись (hash)")

    received_hash = parsed_data.pop("hash")
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed_data.items()))

    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(calculated_hash, received_hash):
        raise HTTPException(status_code=403, detail="Подпись не совпадает (попытка подделки данных)")

    try:
        auth_date = int(parsed_data.get("auth_date", "0"))
    except ValueError:
        auth_date = 0
    if time.time() - auth_date > 86400:
        raise HTTPException(status_code=401, detail="Данные авторизации устарели, откройте приложение заново")

    try:
        return json.loads(parsed_data.get("user", "{}"))
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Не удалось прочитать данные пользователя")


# ==========================================
# 2. Простой лимитер запросов в памяти процесса (защита от спама по деньгам)
# ==========================================
RATE_LIMIT, RATE_WINDOW = 20, 60
_rate_buckets = defaultdict(list)


def check_rate_limit(user_id: int):
    now = time.time()
    bucket = _rate_buckets[user_id]
    while bucket and bucket[0] < now - RATE_WINDOW:
        bucket.pop(0)
    if len(bucket) >= RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Слишком много запросов подряд. Подождите немного.")
    bucket.append(now)


async def notify_admin(text: str):
    if not ADMIN_TELEGRAM_ID:
        return
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": ADMIN_TELEGRAM_ID, "text": text},
            )
    except Exception:
        pass


@app.get("/")
async def root():
    return {"message": "API is running. WebApp should be accessed via Frontend URL."}


# НИКАКОГО /api/reset_db тут больше нет — раньше это был публичный GET-запрос,
# который стирал ВСЮ базу данных без единой проверки прав. Если нужно почистить базу
# при разработке — делайте это руками через консоль/скрипт, а не через открытый в интернет URL.


# ==========================================
# 3. ПРОФИЛЬ
# ==========================================
@app.get("/api/user/profile")
async def get_profile(user_tg: dict = Depends(verify_telegram_data), db: Session = Depends(get_db)):
    user = get_or_create_user(db, user_tg.get("id"), user_tg.get("first_name"), user_tg.get("username"))

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
        "referrals_count": referrals_count,
        "ai_unlocked": user.ai_unlocked,
        "unlock_threshold": UNLOCK_THRESHOLD,
    }


# ==========================================
# 4. ЗАДАНИЯ
# ==========================================
@app.get("/api/tasks")
async def get_tasks(user_tg: dict = Depends(verify_telegram_data), db: Session = Depends(get_db)):
    user = get_or_create_user(db, user_tg.get("id"), user_tg.get("first_name"), user_tg.get("username"))

    my_subs = {ut.task_id: ut.status for ut in db.query(UserTask).filter(UserTask.telegram_id == user.telegram_id).all()}
    all_tasks = db.query(Task).filter(Task.is_active == True).all()  # noqa: E712

    result = []
    for t in all_tasks:
        result.append({
            "id": t.id,
            "title": t.title,
            "description": t.description,
            "reward": t.reward * 2 if user.is_pro else t.reward,
            "category": t.category,
            "icon_type": t.icon_type,
            "requires_proof": t.requires_proof,
            "locked": bool(t.requires_unlock and not user.ai_unlocked),
            "status": my_subs.get(t.id),         # null | "pending" | "approved" | "rejected"
            "is_completed": my_subs.get(t.id) == "approved",
        })
    return result


class CompleteTaskRequest(BaseModel):
    task_id: str
    proof_text: Optional[str] = None


@app.post("/api/tasks/complete")
async def complete_task(req: CompleteTaskRequest, user_tg: dict = Depends(verify_telegram_data), db: Session = Depends(get_db)):
    user = get_or_create_user(db, user_tg.get("id"), user_tg.get("first_name"), user_tg.get("username"))
    check_rate_limit(user.telegram_id)

    task = db.query(Task).filter(Task.id == req.task_id, Task.is_active == True).first()  # noqa: E712
    if not task:
        raise HTTPException(status_code=404, detail="Задание не найдено или неактивно")

    if task.requires_unlock and not user.ai_unlocked:
        raise HTTPException(
            status_code=403,
            detail=f"Сначала выполните {UNLOCK_THRESHOLD} обычных заданий, чтобы открыть эту категорию",
        )

    existing = get_existing_submission(db, user.telegram_id, task.id)
    if existing and existing.status in ("pending", "approved"):
        raise HTTPException(status_code=400, detail="Вы уже получили награду за это задание, либо оно на проверке")

    if task.requires_proof:
        proof = (req.proof_text or "").strip()
        if len(proof) < 5:
            raise HTTPException(status_code=400, detail="Приложите подтверждение выполнения (ссылку, текст, описание)")

        if existing:  # повторная отправка после отклонения — переиспользуем ту же строку (уникальный индекс)
            existing.status = "pending"
            existing.proof_text = proof
            existing.reviewed_at = None
        else:
            db.add(UserTask(telegram_id=user.telegram_id, task_id=task.id, status="pending", proof_text=proof))
        db.commit()

        row = get_existing_submission(db, user.telegram_id, task.id)
        await notify_admin(
            "🆕 Новое задание на проверку\n"
            f"Задание: {task.title} ({task.reward} coins)\n"
            f"Пользователь: {user.first_name} (id {user.telegram_id})\n"
            f"Подтверждение: {proof[:500]}\n"
            f"Команды: /approve_{row.id}  /reject_{row.id}"
        )
        return {"status": "pending", "message": "Отправлено на проверку. Обычно занимает до 24 часов."}

    # Простые задания (подписка/просмотр/лайк) — тяжело фармить массово и легко проверить выборочно,
    # поэтому начисляем сразу.
    db.add(UserTask(telegram_id=user.telegram_id, task_id=task.id, status="approved", reviewed_at=datetime.utcnow()))
    actual_reward = apply_task_reward(db, user, task.reward)
    db.commit()

    return {
        "status": "success",
        "message": f"Задание «{task.title}» выполнено! Начислено +{actual_reward} coins.",
        "new_balance": user.balance,
    }


# ==========================================
# 5. ЛИДЕРБОРД
# ==========================================
@app.get("/api/leaderboard")
async def get_leaderboard(db: Session = Depends(get_db)):
    top_users = db.query(User).order_by(desc(User.balance)).limit(50).all()
    return [
        {
            "rank": idx,
            "first_name": u.first_name,
            "username": u.username,
            "balance": u.balance,
            "tasks_completed": u.tasks_completed,
            "is_pro": u.is_pro,
        }
        for idx, u in enumerate(top_users, 1)
    ]


# ==========================================
# 6. КОШЕЛЁК / ВЫВОД СРЕДСТВ
# ==========================================
class WithdrawRequest(BaseModel):
    amount: int = Field(..., ge=MIN_WITHDRAWAL)
    wallet: str = Field(..., min_length=5)
    method: str = "card"  # card (Payme/Click) | ton | usdt


@app.post("/api/wallet/withdraw")
async def request_withdrawal(req: WithdrawRequest, user_tg: dict = Depends(verify_telegram_data), db: Session = Depends(get_db)):
    user = get_or_create_user(db, user_tg.get("id"), user_tg.get("first_name"), user_tg.get("username"))
    check_rate_limit(user.telegram_id)

    if req.method not in ("card", "ton", "usdt"):
        raise HTTPException(status_code=400, detail="Неверный способ вывода")
    if user.balance < req.amount:
        raise HTTPException(status_code=400, detail="Недостаточно монет на балансе")

    user.balance -= req.amount
    withdrawal = Withdrawal(
        telegram_id=user.telegram_id, amount=req.amount, method=req.method,
        wallet_address=req.wallet.strip(), status="priority" if user.is_pro else "pending",
    )
    db.add(withdrawal)
    db.commit()

    await notify_admin(
        f"💸 Заявка на вывод {'(PRO — приоритет) ' if user.is_pro else ''}\n"
        f"Пользователь: {user.first_name} (id {user.telegram_id})\n"
        f"Сумма: {req.amount} coins, способ: {req.method}\n"
        f"Реквизиты: {req.wallet.strip()}"
    )
    return {"status": "success", "message": "Заявка на вывод успешно создана!", "new_balance": user.balance}


# ==========================================
# 7. PRO ЧЕРЕЗ TELEGRAM STARS
# ==========================================
@app.post("/api/payments/buy_pro")
async def create_pro_invoice(user_tg: dict = Depends(verify_telegram_data), db: Session = Depends(get_db)):
    user = get_or_create_user(db, user_tg.get("id"), user_tg.get("first_name"), user_tg.get("username"))
    if user.is_pro:
        raise HTTPException(status_code=400, detail="У вас уже есть PRO статус")

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/createInvoiceLink"
    payload = {
        "title": "PRO Доступ (VIP Множитель x2)",
        "description": "Удвоенный доход за задания, приоритетная проверка и приоритетный вывод.",
        "payload": f"pro_sub_{user.telegram_id}",
        "currency": "XTR",
        "prices": [{"label": "PRO Статус", "amount": 250}],
        "provider_token": "",
    }
    async with httpx.AsyncClient() as client:
        res = await client.post(url, json=payload)
        data = res.json()

    if data.get("ok"):
        return {"invoice_link": data["result"]}
    raise HTTPException(status_code=400, detail="Ошибка генерации счета Stars")


# ==========================================
# 8. WEBHOOK — платежи Stars, /start и рефералы (продакшен-режим на Render)
# ==========================================
@app.post("/webhook")
async def telegram_webhook(
    request: Request,
    db: Session = Depends(get_db),
    x_telegram_bot_api_secret_token: Optional[str] = Header(None),
):
    # Без проверки секрета кто угодно мог бы прислать поддельный "successful_payment"
    # и бесплатно получить PRO. Установите WEBHOOK_SECRET и передайте его в setWebhook
    # (параметр secret_token) при регистрации вебхука.
    if WEBHOOK_SECRET and x_telegram_bot_api_secret_token != WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Invalid secret token")

    try:
        update = await request.json()
    except Exception:
        return {"ok": True}

    if "pre_checkout_query" in update:
        query_id = update["pre_checkout_query"]["id"]
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/answerPreCheckoutQuery",
                json={"pre_checkout_query_id": query_id, "ok": True},
            )
        return {"ok": True}

    if "message" not in update:
        return {"ok": True}

    msg = update["message"]
    user_id = msg.get("from", {}).get("id")
    first_name = msg.get("from", {}).get("first_name", "Пользователь")
    username = msg.get("from", {}).get("username")

    if "successful_payment" in msg:
        user = get_or_create_user(db, user_id, first_name, username)
        user.is_pro = True
        db.commit()
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": user_id, "text": "🎉 PRO Статус успешно активирован! Доход x2 включён."},
            )
        return {"ok": True}

    if str(msg.get("text", "")).startswith("/start"):
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
            user = get_or_create_user(db, user_id, first_name, username, referrer_id=referrer_id)
            welcome_text = (
                f"Привет, {first_name}! 👋\n\n"
                "Добро пожаловать в Tapshiriq Bozor — простые задания, честная фиксированная оплата.\n"
                f"🎁 Тебе начислен приветственный бонус: {user.balance} coins"
            )
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                    json={
                        "chat_id": user_id,
                        "text": welcome_text,
                        "reply_markup": {"inline_keyboard": [[{
                            "text": "🚀 Открыть Биржу Заданий",
                            "web_app": {"url": WEBAPP_URL},
                        }]]},
                    },
                )
        except Exception as e:
            db.rollback()
            print("Ошибка при обработке /start:", e)

        return {"ok": True}

    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
