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

class User(Base):
    __tablename__ = "users"

    telegram_id = Column(BigInteger, primary_key=True, index=True)
    first_name = Column(String, nullable=False)
    username = Column(String, nullable=True)
    balance = Column(Integer, default=0, nullable=False)
    tasks_completed = Column(Integer, default=0, nullable=False)
    is_pro = Column(Boolean, default=False, nullable=False)
    referrer_id = Column(BigInteger, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    task_completions = relationship("UserTask", back_populates="user", cascade="all, delete-orphan")
    withdrawals = relationship("Withdrawal", back_populates="user", cascade="all, delete-orphan")

class Task(Base):
    __tablename__ = "tasks"

    id = Column(String, primary_key=True, index=True)
    title = Column(String, nullable=False)
    description = Column(String, nullable=False)
    reward = Column(Integer, nullable=False)
    category = Column(String, nullable=False) # 'ai', 'copywriting', 'social', 'survey'
    icon_type = Column(String, default="brain")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class UserTask(Base):
    __tablename__ = "user_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, ForeignKey("users.telegram_id"), nullable=False)
    task_id = Column(String, ForeignKey("tasks.id"), nullable=False)
    completed_at = Column(DateTime, default=datetime.utcnow)

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
    wallet_address = Column(String, nullable=False)
    status = Column(String, default="pending") # pending, approved, rejected
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="withdrawals")

# === АВТОМАТИЧЕСКАЯ ПРОВЕРКА И МИГРАЦИЯ ДЛЯ SQLITE ===
inspector = inspect(engine)
if "users" in inspector.get_table_names():
    columns = [c["name"] for c in inspector.get_columns("users")]
    if "referrer_id" not in columns:
        # Удалится старая таблица со старой структурой
        Base.metadata.drop_all(bind=engine)

Base.metadata.create_all(bind=engine)

def seed_initial_tasks():
    db = SessionLocal()
    try:
        initial_tasks = [
            # --- Категория: ИИ и Разметка (ai) ---
            Task(
                id="ai_training", 
                title="Обучение ИИ", 
                description="Оценка и разметка ответов нейросетей на узбекском и русском языках.", 
                reward=150, 
                category="ai", 
                icon_type="cpu"
            ),
            Task(
                id="voice_transcribe", 
                title="Аудио транскрибация", 
                description="Расшифровка 1-минутной голосовой записи в грамотный текст.", 
                reward=120, 
                category="ai", 
                icon_type="mic"
            ),
            Task(
                id="ai_image_check", 
                title="Модерация картинок", 
                description="Проверка 10 сгенерированных нейросетью изображений на артефакты.", 
                reward=90, 
                category="ai", 
                icon_type="brain"
            ),

            # --- Категория: Тексты и Копирайтинг (copywriting) ---
            Task(
                id="copywriting", 
                title="Копирайтинг поста", 
                description="Написание вовлекающего рекламного поста для Telegram-канала бренда.", 
                reward=200, 
                category="copywriting", 
                icon_type="pen"
            ),
            Task(
                id="proofread_article", 
                title="Вычитка статьи", 
                description="Исправление орфографических и стилистических ошибок в короткой заметке.", 
                reward=160, 
                category="copywriting", 
                icon_type="pen"
            ),
            Task(
                id="seo_description", 
                title="SEO-описание товара", 
                description="Составление карточки товара с ключевыми словами для маркетплейса.", 
                reward=110, 
                category="copywriting", 
                icon_type="pen"
            ),

            # --- Категория: Социальные сети (social) ---
            Task(
                id="telegram_sub", 
                title="Подписка на канал", 
                description="Подписаться на официальный анонс-канал проекта в Telegram.", 
                reward=80, 
                category="social", 
                icon_type="telegram"
            ),
            Task(
                id="youtube_watch", 
                title="Просмотр видео", 
                description="Просмотр короткого 2-минутного видеообзора платформы.", 
                reward=100, 
                category="social", 
                icon_type="telegram"
            ),
            Task(
                id="comment_post", 
                title="Комментарий под постом", 
                description="Оставить осмысленный отзыв из 5+ слов под публикацией.", 
                reward=70, 
                category="social", 
                icon_type="telegram"
            ),

            # --- Категория: Опросы и Исследования (survey) ---
            Task(
                id="market_analysis", 
                title="Анализ цен Uzum", 
                description="Мониторинг и сравнение цен на электронику на маркетплейсе.", 
                reward=100, 
                category="survey", 
                icon_type="chart"
            ),
            Task(
                id="app_feedback", 
                title="Опрос по UX/UI", 
                description="Заполнение короткой анкеты из 4 вопросов об удобстве интерфейса.", 
                reward=130, 
                category="survey", 
                icon_type="chart"
            ),
            Task(
                id="quiz_crypto", 
                title="Тест на знания Web3", 
                description="Быстрый квиз о базовых понятиях блокчейна и смарт-контрактов.", 
                reward=150, 
                category="survey", 
                icon_type="chart"
            ),
        ]

        for task in initial_tasks:
            db.merge(task)  # Добавляет новые задачи и обновляет существующие
            
        db.commit()
    finally:
        db.close()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
