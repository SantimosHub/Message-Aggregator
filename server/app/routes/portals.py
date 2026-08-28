"""
Этап 3, PLAN.md: подключение внешнего портала сотрудником через виджет.

Поток:
1. Виджет присылает домен + способ авторизации + сами credentials.
2. Проверяем credentials напрямую у платформы/портала (без сохранения, если невалидны).
3. При успехе создаём отдельный групповой чат на основном портале (README, раздел 1:
   "по одному чату на каждый подключённый внешний портал, для каждого сотрудника").
4. Сохраняем запись в БД со статусом active.

Ошибки валидации возвращаются как понятный 400 с сообщением — сервер не падает
(см. PLAN.md, Этап 3, последний пункт).
"""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import get_current_owner_user_id
from ..config import get_settings
from ..external_portal_client import ExternalPortalCredentialsError, validate_vibe_api_key, validate_webhook
from ..repositories import external_portals as repo
from ..vibe_client import VibeApiError, create_group_chat, send_chat_message

router = APIRouter(prefix="/api/portals", tags=["external-portals"])


class ConnectPortalRequest(BaseModel):
    domain: str = Field(..., min_length=1, description="Домен внешнего портала, для отображения")
    auth_type: Literal["vibe_api", "webhook"]
    credentials: str = Field(..., min_length=1, description="Ключ vibe_api_... или URL вебхука")


class PortalResponse(BaseModel):
    id: int
    domain: str
    auth_type: str
    main_chat_id: Optional[int]
    status: str
    created_at: str

    @classmethod
    def from_portal(cls, portal: repo.ExternalPortal) -> "PortalResponse":
        """Собирает ответ БЕЗ поля credentials — секрет никогда не уходит обратно клиенту."""
        return cls(
            id=portal.id,
            domain=portal.domain,
            auth_type=portal.auth_type,
            main_chat_id=portal.main_chat_id,
            status=portal.status,
            created_at=portal.created_at,
        )


@router.get("", response_model=list[PortalResponse])
async def list_my_portals(owner_user_id: int = Depends(get_current_owner_user_id)) -> list[PortalResponse]:
    """Список внешних порталов текущего сотрудника (полезно для проверки до появления UI, Этап 6)."""
    portals = await repo.list_portals_by_owner(owner_user_id)
    return [PortalResponse.from_portal(p) for p in portals]


@router.post("", response_model=PortalResponse, status_code=201)
async def connect_portal(
    body: ConnectPortalRequest,
    owner_user_id: int = Depends(get_current_owner_user_id),
) -> PortalResponse:
    try:
        return await _connect_portal_impl(body, owner_user_id)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — см. докстринг ниже
        # По умолчанию необработанное исключение в FastAPI отдаётся как
        # ПЛОСКИЙ ТЕКСТ "Internal Server Error" без тела JSON — фронтенд не
        # может показать причину, только резервный "Не удалось подключить
        # портал." (это и произошло при первом реальном тесте на живом
        # портале). Ловим здесь и всегда возвращаем JSON с типом+сообщением
        # исключения — так реальная причина видна прямо в виджете.
        raise HTTPException(
            status_code=500,
            detail=f"Внутренняя ошибка сервера: {type(exc).__name__}: {exc}",
        ) from exc


async def _connect_portal_impl(
    body: ConnectPortalRequest,
    owner_user_id: int,
) -> PortalResponse:
    print(f"[connect_portal] СТАРТ domain={body.domain!r} auth_type={body.auth_type}", flush=True)

    # 1. Валидация credentials — до создания чата и записи в БД.
    try:
        print("[connect_portal] шаг 1: валидация credentials...", flush=True)
        if body.auth_type == "vibe_api":
            await validate_vibe_api_key(body.credentials)
        else:
            await validate_webhook(body.credentials)
        print("[connect_portal] шаг 1: OK", flush=True)
    except ExternalPortalCredentialsError as exc:
        print(f"[connect_portal] шаг 1: ExternalPortalCredentialsError: {exc}", flush=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — временная диагностика, ловим ЛЮБОЕ исключение
        print(f"[connect_portal] шаг 1: НЕОЖИДАННОЕ исключение {type(exc).__name__}: {exc}", flush=True)
        raise

    # 2. Создание отдельного чата на основном портале для этого сотрудника+портала.
    settings = get_settings()
    chat_title = f"{settings.placement_title}: {body.domain}"
    try:
        print("[connect_portal] шаг 2: создаю чат...", flush=True)
        chat_id = await create_group_chat(title=chat_title, user_ids=[owner_user_id])
        print(f"[connect_portal] шаг 2: OK chat_id={chat_id}", flush=True)
    except VibeApiError as exc:
        print(f"[connect_portal] шаг 2: VibeApiError: {exc.payload}", flush=True)
        raise HTTPException(
            status_code=502,
            detail=f"Не удалось создать чат на основном портале: {exc.payload}",
        ) from exc

    # 3. Сохранение записи в БД со статусом active.
    print("[connect_portal] шаг 3: сохраняю в БД...", flush=True)
    portal = await repo.create_portal(
        owner_user_id=owner_user_id,
        domain=body.domain,
        auth_type=body.auth_type,
        credentials=body.credentials,
        main_chat_id=chat_id,
    )
    print(f"[connect_portal] шаг 3: OK portal_id={portal.id}", flush=True)

    # 4. Приветственное сообщение — не критично для успеха подключения,
    #    поэтому ошибку отправки только логируем, не роняем запрос.
    try:
        print("[connect_portal] шаг 4: отправляю приветственное сообщение...", flush=True)
        await send_chat_message(
            chat_id,
            f"Портал «{body.domain}» подключён. Сюда будут приходить сообщения сотруднику с этого портала.",
        )
        print("[connect_portal] шаг 4: OK", flush=True)
    except VibeApiError as exc:
        print(f"[connect_portal] шаг 4: VibeApiError (игнорируем): {exc.payload}", flush=True)

    print(f"[connect_portal] ФИНИШ portal_id={portal.id}", flush=True)
    return PortalResponse.from_portal(portal)


@router.delete("/{portal_id}", status_code=204)
async def disconnect_portal(portal_id: int, owner_user_id: int = Depends(get_current_owner_user_id)) -> None:
    """Отключение внешнего портала. Проверяем, что удаляет владелец записи, а не чужую."""
    portal = await repo.get_portal(portal_id)
    if portal is None or portal.owner_user_id != owner_user_id:
        raise HTTPException(status_code=404, detail="Портал не найден")
    await repo.delete_portal(portal_id)
