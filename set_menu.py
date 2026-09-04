import os
import asyncio
import httpx
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан. Установите переменную окружения BOT_TOKEN (см. .env.example).")

WEBAPP_URL = os.getenv("WEBAPP_URL", "https://frontend-tma-2w9i.onrender.com")


async def set_bot_menu():
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setChatMenuButton"
    payload = {
        "menu_button": {
            "type": "web_app",
            "text": "🚀 Открыть TMA",
            "web_app": {"url": WEBAPP_URL},
        }
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload)
        print("Результат установки меню:", response.json())


if __name__ == "__main__":
    asyncio.run(set_bot_menu())
