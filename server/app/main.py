from fastapi import Depends, FastAPI

from .auth import get_current_owner_user_id

app = FastAPI(title="Bitrix24 Message Aggregator")


@app.get("/health")
async def health() -> dict:
    """Проверка живости — используется деплоем/мониторингом, без авторизации."""
    return {"status": "ok"}


@app.get("/api/me")
async def whoami(owner_user_id: int = Depends(get_current_owner_user_id)) -> dict:
    """
    Проверочный эндпоинт Этапа 1: подтверждает, что backend различает
    сотрудников, открывших виджет (owner_user_id из Gateway-сессии).
    Реальный UI списка внешних порталов появится на Этапе 6.
    """
    return {"owner_user_id": owner_user_id}
