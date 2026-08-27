import asyncio

from fastapi import Depends, FastAPI

from .auth import get_current_owner_user_id
from .db import init_db
from .poller import start_poller
from .routes.portals import router as portals_router

app = FastAPI(title="Bitrix24 Message Aggregator")
app.include_router(portals_router)


@app.on_event("startup")
async def on_startup() -> None:
    """Создаёт таблицы БД при старте, если их ещё нет (Этап 2, PLAN.md)."""
    await init_db()
    app.state.poller_task = start_poller()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    """Корректно останавливает фоновый опрос внешних порталов (Этап 4, PLAN.md)."""
    task = getattr(app.state, "poller_task", None)
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


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
