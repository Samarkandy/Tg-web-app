"""
Общая бизнес-логика для main.py (API) и bot.py (обработка /start и админ-проверка заданий).
Правила начисления денег должны существовать только в одном месте: если продублировать их
в двух файлах, они рано или поздно разойдутся — а это тоже форма дыры в безопасности
(например, лимит починили в одном месте и забыли в другом).
"""
from datetime import datetime

from database import User, UserTask, UNLOCK_THRESHOLD, REFERRAL_BONUS, SIGNUP_BONUS


def get_or_create_user(db, telegram_id: int, first_name: str = None, username: str = None, referrer_id: int = None) -> User:
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    if user:
        return user
    user = User(
        telegram_id=telegram_id,
        first_name=first_name or "Пользователь",
        username=username,
        balance=SIGNUP_BONUS,
        tasks_completed=0,
        is_pro=False,
        ai_unlocked=False,
        referrer_id=referrer_id if referrer_id != telegram_id else None,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_existing_submission(db, telegram_id: int, task_id: str):
    return (
        db.query(UserTask)
        .filter(UserTask.telegram_id == telegram_id, UserTask.task_id == task_id)
        .first()
    )


def apply_task_reward(db, user: User, reward: int):
    """Начисляет деньги ПОСЛЕ того как задание реально одобрено (сразу — для простых,
    вручную админом — для всего, что легко подделать). Также проверяет разблокировку
    категории ai и реферальный бонус."""
    if user.is_pro:
        reward = reward * 2

    user.balance += reward
    user.tasks_completed += 1

    if not user.ai_unlocked and user.tasks_completed >= UNLOCK_THRESHOLD:
        user.ai_unlocked = True

    _maybe_pay_referral_bonus(db, user)
    return reward


def _maybe_pay_referral_bonus(db, user: User):
    """Бонус рефереру и приглашённому платится один раз — и только когда приглашённый
    реально выполнил первое задание, а не просто открыл бота (иначе это чистая накрутка
    на пустых аккаунтах без единого выполненного задания)."""
    if user.referrer_id and not user.referral_bonus_given and user.tasks_completed >= 1:
        referrer = db.query(User).filter(User.telegram_id == user.referrer_id).first()
        if referrer:
            referrer.balance += REFERRAL_BONUS
            user.balance += REFERRAL_BONUS
            user.referral_bonus_given = True
