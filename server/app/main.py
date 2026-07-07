from fastapi import Depends, FastAPI, Request

from .auth import get_current_owner_user_id

app = FastAPI(title="Bitrix24 Message Aggregator")


@app.get("/health")
async def health() -> dict:
    """Проверка живости — используется деплоем/мониторингом, без авторизации."""
    return {"status": "ok"}


@app.get("/api/debug/headers")
async def debug_headers(request: Request) -> dict:
    """
    ВРЕМЕННЫЙ диагностический эндпоинт (Этап 1, отладка Gateway).
    Показывает ВСЕ заголовки, реально дошедшие до контейнера — чтобы понять,
    добавляет ли Gateway X-Vibe-Authorization, или проблема где-то раньше.
    УДАЛИТЬ после того, как разберёмся с проблемой авторизации placement.
    """
    return {"headers": dict(request.headers), "cookies": dict(request.cookies)}


@app.get("/api/me")
async def whoami(owner_user_id: int = Depends(get_current_owner_user_id)) -> dict:
    """
    Проверочный эндпоинт Этапа 1: подтверждает, что backend различает
    сотрудников, открывших виджет (owner_user_id из Gateway-сессии).
    Реальный UI списка внешних порталов появится на Этапе 6.
    """
    return {"owner_user_id": owner_user_id}
