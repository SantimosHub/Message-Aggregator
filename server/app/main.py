from fastapi import Depends, FastAPI

from .auth import get_current_owner_user_id
from .db import init_db

app = FastAPI(title="Bitrix24 Message Aggregator")


@app.on_event("startup")
async def on_startup() -> None:
    """Создаёт таблицы БД при старте, если их ещё нет (Этап 2, PLAN.md)."""
    await init_db()


@app.get("/health")
async def health() -> dict:
    """Проверка живости — используется деплоем/мониторингом, без авторизации."""
    return {"status": "ok"}


@app.get("/api/me")
async def whoami(owner_user_id: int = Depends(get_current_owner_user_id)) -> dict:
    """
    Этап 1 подтверждён рабочим end-to-end: backend различает сотрудников,
    открывших виджет (owner_user_id из Gateway-сессии, X-Vibe-Authorization).
    Реальный UI списка внешних порталов появится на Этапе 6.
    """
    return {"owner_user_id": owner_user_id}
