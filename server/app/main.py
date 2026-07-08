from fastapi import Depends, FastAPI, Request

from .auth import get_current_owner_user_id
from .config import get_settings

app = FastAPI(title="Bitrix24 Message Aggregator")


def _mask(value: str, keep_start: int = 15, keep_end: int = 5) -> str:
    if not value:
        return "<EMPTY>"
    if len(value) <= keep_start + keep_end:
        return f"<len={len(value)}>"
    return f"{value[:keep_start]}...{value[-keep_end:]} (len={len(value)})"


@app.get("/health")
async def health() -> dict:
    """Проверка живости — используется деплоем/мониторингом, без авторизации."""
    return {"status": "ok"}


@app.get("/api/debug/headers")
async def debug_headers(request: Request) -> dict:
    """
    ВРЕМЕННЫЙ диагностический эндпоинт (Этап 1, отладка Gateway).
    Показывает ВСЕ заголовки, реально дошедшие до контейнера, и замаскированные
    настройки, реально загруженные из окружения — чтобы понять, где расхождение.
    УДАЛИТЬ после того, как разберёмся с проблемой авторизации placement.
    """
    settings = get_settings()
    return {
        "headers": dict(request.headers),
        "cookies": dict(request.cookies),
        "settings": {
            "vibe_app_key": _mask(settings.vibe_app_key),
            "vibe_api_base_url": settings.vibe_api_base_url,
            "app_base_url": settings.app_base_url,
            "placement_title": settings.placement_title,
        },
    }


@app.get("/api/debug/key-check")
async def debug_key_check() -> dict:
    """
    ВРЕМЕННЫЙ диагностический эндпоинт. Проверяет VIBE_APP_KEY сам по себе,
    БЕЗ сессии Gateway — вызывает GET /v1/me только с X-Api-Key.
    Если тут тоже INVALID_API_KEY — дело в самом значении ключа/окружения,
    а не в связке ключ+сессия. УДАЛИТЬ после диагностики.
    """
    from .vibe_client import VibeApiError, get_me

    try:
        result = await get_me(session_bearer=None)
        return {"ok": True, "result": result}
    except VibeApiError as exc:
        return {"ok": False, "status_code": exc.status_code, "payload": exc.payload}


@app.get("/api/me")
async def whoami(owner_user_id: int = Depends(get_current_owner_user_id)) -> dict:
    """
    Проверочный эндпоинт Этапа 1: подтверждает, что backend различает
    сотрудников, открывших виджет (owner_user_id из Gateway-сессии).
    Реальный UI списка внешних порталов появится на Этапе 6.
    """
    return {"owner_user_id": owner_user_id}
