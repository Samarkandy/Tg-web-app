import os
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, BigInteger, String, 
    Boolean, DateTime, ForeignKey, UniqueConstraint
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

Base.metadata.create_all(bind=engine)

def seed_initial_tasks():
    db = SessionLocal()
    try:
        existing = db.query(Task).count()
        if existing == 0:
            initial_tasks = [
                Task(id="ai_training", title="Обучение ИИ", description="Оценка и разметка ответов нейросетей на узбекском/русском языках.", reward=150, category="ai", icon_type="cpu"),
                Task(id="copywriting", title="Копирайтинг поста", description="Написание вовлекающего текста для Telegram-канала бренда.", reward=200, category="copywriting", icon_type="pen"),
                Task(id="market_analysis", title="Анализ цен", description="Мониторинг цен товаров на маркетплейсе Uzum.", reward=100, category="survey", icon_type="chart"),
                Task(id="telegram_sub", title="Подписка на канал", description="Подписаться на официальный анонс-канал проекта.", reward=80, category="social", icon_type="telegram"),
                Task(id="voice_transcribe", title="Аудио транскрибация", description="Перевод 1-минутной аудиозаписи в идеальный текст.", reward=120, category="ai", icon_type="mic"),
            ]
            db.add_all(initial_tasks)
            db.commit()
    finally:
        db.close()

seed_initial_tasks()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
