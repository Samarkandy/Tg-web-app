from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

router = APIRouter(
    prefix="/api/tasks",
    tags=["tasks"]
)

class TaskSubmit(BaseModel):
    task_id: str
    report_url: str = None

@router.get("/")
async def get_tasks():
    # Позже здесь будет запрос к БД для получения списка активных задач
    return [
        {"id": "ai_training", "title": "Обучение ИИ", "reward": 150},
        {"id": "copywriting", "title": "Копирайтинг поста", "reward": 200},
    ]

@router.post("/submit")
async def submit_task(payload: TaskSubmit):
    # Логика приема задания на проверку
    return {"status": "success", "message": f"Task {payload.task_id} submitted", "url": payload.report_url}