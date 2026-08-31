from pydantic import BaseModel

class TaskSubmit(BaseModel):
    task_id: str
    report_url: str = None # Ссылка на скриншот/отчет

class UserResponse(BaseModel):
    telegram_id: int
    username: str | None
    balance: int
    
    class Config:
        orm_mode = True