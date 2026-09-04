import os
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, BigInteger, String,
    Boolean, DateTime, ForeignKey, UniqueConstraint, inspect
)
from sqlalchemy.orm import sessionmaker, declarative_base, relationship

SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./tma_app.db")

if SQLALCHEMY_DATABASE_URL.startswith("postgres://"):
    SQLALCHEMY_DATABASE_URL = SQLALCHEMY_DATABASE_URL.replace("postgres://", "postgresql://", 1)

connect_args = {"check_same_thread": False} if "sqlite" in SQLALCHEMY_DATABASE_URL else {}

engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# === НАСТРОЙКИ ЭКОНОМИКИ — единое место, чтобы API и бот не расходились ===
UNLOCK_THRESHOLD = 15     # выполненных заданий, чтобы открыть категорию "ai" (замена рулетке)
SIGNUP_BONUS = 100        # стартовый баланс новому пользователю
REFERRAL_BONUS = 150      # и рефереру, и приглашённому — но только когда приглашённый реально что-то выполнил
MIN_WITHDRAWAL = 10000    # сум (или соответствующий эквивалент, если используете крипто-вывод)


class User(Base):
    __tablename__ = "users"

    telegram_id = Column(BigInteger, primary_key=True, index=True)
    first_name = Column(String, nullable=False)
    username = Column(String, nullable=True)
    balance = Column(Integer, default=0, nullable=False)
    tasks_completed = Column(Integer, default=0, nullable=False)
    is_pro = Column(Boolean, default=False, nullable=False)
    referrer_id = Column(BigInteger, nullable=True)
    ai_unlocked = Column(Boolean, default=False, nullable=False)
    referral_bonus_given = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    task_completions = relationship("UserTask", back_populates="user", cascade="all, delete-orphan")
    withdrawals = relationship("Withdrawal", back_populates="user", cascade="all, delete-orphan")


class Task(Base):
    __tablename__ = "tasks"

    id = Column(String, primary_key=True, index=True)
    title = Column(String, nullable=False)
    description = Column(String, nullable=False)
    reward = Column(Integer, nullable=False)             # фиксировано — никогда не меняется случайно
    category = Column(String, nullable=False)            # ai | copywriting | social | survey
    icon_type = Column(String, default="brain")
    is_active = Column(Boolean, default=True)
    requires_proof = Column(Boolean, default=False)       # True -> нужна ручная проверка перед выплатой
    requires_unlock = Column(Boolean, default=False)      # True -> только после UNLOCK_THRESHOLD заданий
    created_at = Column(DateTime, default=datetime.utcnow)


class UserTask(Base):
    """Одна строка на пару (пользователь, задание) — UniqueConstraint не даёт сдать одно и то же
    задание дважды, это и есть основная защита от накрутки на уровне базы данных."""
    __tablename__ = "user_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, ForeignKey("users.telegram_id"), nullable=False)
    task_id = Column(String, ForeignKey("tasks.id"), nullable=False)
    status = Column(String, default="approved", nullable=False)  # pending | approved | rejected
    proof_text = Column(String, nullable=True)
    completed_at = Column(DateTime, default=datetime.utcnow)
    reviewed_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="task_completions")
    task = relationship("Task")

    __table_args__ = (
        UniqueConstraint('telegram_id', 'task_id', name='_user_task_uc'),
    )


class Withdrawal(Base):
    __tablename__ = "withdrawals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, ForeignKey("users.telegram_id"), nullable=False)
    amount = Column(Integer, nullable=False)
    method = Column(String, default="card")            # card (Payme/Click) | ton | usdt
    wallet_address = Column(String, nullable=False)     # номер карты/телефона ИЛИ адрес кошелька
    status = Column(String, default="pending")          # pending | priority | paid | rejected
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="withdrawals")


# === ИНИЦИАЛИЗАЦИЯ БАЗЫ ===
# ВАЖНО: если вы раньше уже запускали старую версию (без ai_unlocked/requires_proof и т.д.),
# новые колонки сами не появятся в существующей таблице — create_all не меняет уже созданные таблицы.
# Проще всего удалить старые app.db / tma_app.db (это тестовые данные) и дать им пересоздаться заново.
# Ниже — безопасная авто-миграция на случай, если удалять базу не хочется.
Base.metadata.create_all(bind=engine)

_inspector = inspect(engine)
if "users" in _inspector.get_table_names():
    _existing_cols = {c["name"] for c in _inspector.get_columns("users")}
    _needed = {
        "ai_unlocked": "BOOLEAN DEFAULT 0",
        "referral_bonus_given": "BOOLEAN DEFAULT 0",
    }
    with engine.begin() as conn:
        for col, ddl in _needed.items():
            if col not in _existing_cols:
                try:
                    conn.exec_driver_sql(f"ALTER TABLE users ADD COLUMN {col} {ddl}")
                except Exception:
                    pass  # уже есть, либо не SQLite — тогда используйте нормальный migration-инструмент (alembic)

if "user_tasks" in _inspector.get_table_names():
    _existing_cols = {c["name"] for c in _inspector.get_columns("user_tasks")}
    _needed = {
        "status": "VARCHAR DEFAULT 'approved'",
        "proof_text": "VARCHAR",
        "reviewed_at": "DATETIME",
    }
    with engine.begin() as conn:
        for col, ddl in _needed.items():
            if col not in _existing_cols:
                try:
                    conn.exec_driver_sql(f"ALTER TABLE user_tasks ADD COLUMN {col} {ddl}")
                except Exception:
                    pass

if "tasks" in _inspector.get_table_names():
    _existing_cols = {c["name"] for c in _inspector.get_columns("tasks")}
    _needed = {"requires_proof": "BOOLEAN DEFAULT 0", "requires_unlock": "BOOLEAN DEFAULT 0"}
    with engine.begin() as conn:
        for col, ddl in _needed.items():
            if col not in _existing_cols:
                try:
                    conn.exec_driver_sql(f"ALTER TABLE tasks ADD COLUMN {col} {ddl}")
                except Exception:
                    pass

if "withdrawals" in _inspector.get_table_names():
    _existing_cols = {c["name"] for c in _inspector.get_columns("withdrawals")}
    if "method" not in _existing_cols:
        with engine.begin() as conn:
            try:
                conn.exec_driver_sql("ALTER TABLE withdrawals ADD COLUMN method VARCHAR DEFAULT 'card'")
            except Exception:
                pass


# === СТАРТОВЫЙ КАТАЛОГ ЗАДАНИЙ ===
# requires_proof=True  -> нужно подтверждение, начисление после ручной проверки (не по нажатию кнопки)
# requires_unlock=True -> доступно только после UNLOCK_THRESHOLD выполненных заданий — замена рулетке
INITIAL_TASKS = [
    dict(id="ai_training", title="Обучение ИИ",
         description="Оценка и разметка ответов нейросетей на узбекском и русском языках.",
         reward=150, category="ai", icon_type="cpu", requires_proof=True, requires_unlock=True),
    dict(id="voice_transcribe", title="Аудио транскрибация",
         description="Расшифровка 1-минутной голосовой записи в грамотный текст.",
         reward=120, category="ai", icon_type="mic", requires_proof=True, requires_unlock=True),
    dict(id="ai_image_check", title="Модерация картинок",
         description="Проверка 10 сгенерированных нейросетью изображений на артефакты.",
         reward=90, category="ai", icon_type="scan", requires_proof=True, requires_unlock=True),

    dict(id="copywriting", title="Копирайтинг поста",
         description="Написание вовлекающего рекламного поста для Telegram-канала бренда.",
         reward=200, category="copywriting", icon_type="pen", requires_proof=True, requires_unlock=False),
    dict(id="proofread_article", title="Вычитка статьи",
         description="Исправление орфографических и стилистических ошибок в короткой заметке.",
         reward=160, category="copywriting", icon_type="pen", requires_proof=True, requires_unlock=False),
    dict(id="seo_description", title="SEO-описание товара",
         description="Составление карточки товара с ключевыми словами для маркетплейса.",
         reward=110, category="copywriting", icon_type="pen", requires_proof=True, requires_unlock=False),

    dict(id="telegram_sub", title="Подписка на канал",
         description="Подписаться на официальный анонс-канал проекта в Telegram.",
         reward=80, category="social", icon_type="bell", requires_proof=False, requires_unlock=False),
    dict(id="youtube_watch", title="Просмотр видео",
         description="Просмотр короткого 2-минутного видеообзора платформы.",
         reward=100, category="social", icon_type="play", requires_proof=False, requires_unlock=False),
    dict(id="comment_post", title="Комментарий под постом",
         description="Оставить осмысленный отзыв из 5+ слов под публикацией.",
         reward=70, category="social", icon_type="chat", requires_proof=False, requires_unlock=False),

    dict(id="market_analysis", title="Анализ цен Uzum",
         description="Мониторинг и сравнение цен на электронику на маркетплейсе.",
         reward=100, category="survey", icon_type="chart", requires_proof=True, requires_unlock=False),
    dict(id="app_feedback", title="Опрос по UX/UI",
         description="Заполнение короткой анкеты из 4 вопросов об удобстве интерфейса.",
         reward=130, category="survey", icon_type="clipboard", requires_proof=True, requires_unlock=False),
    dict(id="quiz_crypto", title="Тест на знания Web3",
         description="Быстрый квиз о базовых понятиях блокчейна и смарт-контрактов.",
         reward=150, category="survey", icon_type="clipboard", requires_proof=True, requires_unlock=False),
]


def seed_initial_tasks():
    db = SessionLocal()
    try:
        for t in INITIAL_TASKS:
            db.merge(Task(**t))  # добавляет новые и обновляет существующие по id
        db.commit()
    finally:
        db.close()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
