"""
Определение личности сотрудника, открывшего виджет.

Когда приложение установлено как placement и работает за Black Hole,
Gateway сам аутентифицирует пользователя и подставляет на КАЖДЫЙ запрос
заголовок:

    X-Vibe-Authorization: Bearer vibe_session_...

Браузер этот токен никогда не видит. Наша задача — переслать его в
GET /v1/me (как обычный Authorization) и получить currentUser.bitrixUserId —
это и есть owner_user_id, по которому фильтруются "свои" внешние порталы
и чаты сотрудника (см. README, раздел 4.1).

Результат кэшируем в памяти на 24 часа (срок жизни vibe_session_...),
чтобы не дёргать /v1/me на каждый чих.
"""
from __future__ import annotations

import time

from fastapi import Header, HTTPException

from .vibe_client import VibeApiError, get_me

_SESSION_CACHE: dict[str, tuple[int, float]] = {}  # token -> (bitrix_user_id, expires_at)
_CACHE_TTL_SECONDS = 24 * 60 * 60


async def get_current_owner_user_id(
    x_vibe_authorization: str | None = Header(default=None),
) -> int:
    """
    FastAPI-зависимость: возвращает numeric bitrixUserId сотрудника,
    открывшего placement. 401, если заголовок отсутствует или сессия невалидна.

    Локальная разработка без Gateway: пока placement не забинжен, этого
    заголовка не будет — эндпоинты, которые от него зависят, вернут 401.
    Это ожидаемо на этом этапе (см. PLAN.md, Этап 1).
    """
    if not x_vibe_authorization:
        raise HTTPException(status_code=401, detail="Missing X-Vibe-Authorization header (Gateway not in front of this request)")

    cached = _SESSION_CACHE.get(x_vibe_authorization)
    if cached and cached[1] > time.time():
        return cached[0]

    try:
        me = await get_me(session_bearer=x_vibe_authorization)
    except VibeApiError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid or expired session: {exc}") from exc

    current_user = me.get("currentUser")
    if not current_user or not current_user.get("bitrixUserId"):
        raise HTTPException(status_code=401, detail="Session valid but no current user resolved")

    bitrix_user_id = int(current_user["bitrixUserId"])
    _SESSION_CACHE[x_vibe_authorization] = (bitrix_user_id, time.time() + _CACHE_TTL_SECONDS)
    return bitrix_user_id
