"""
Тонкий клиент для вызова API Вайбкод (https://vibecode.bitrix24.tech/v1).

Используется для:
- определения личности сотрудника, открывшего виджет (GET /v1/me с сессией из Gateway);
- (позже, Этап 3+) создания чатов и отправки сообщений от имени приложения.
"""
from __future__ import annotations

import httpx

from .config import get_settings


class VibeApiError(RuntimeError):
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self.payload = payload
        super().__init__(f"Vibe API error {status_code}: {payload}")


async def _request(method: str, path: str, *, headers: dict, json: dict | None = None) -> dict:
    """Общий helper: выполняет запрос, парсит ответ, кидает VibeApiError при неуспехе."""
    settings = get_settings()
    async with httpx.AsyncClient(base_url=settings.vibe_api_base_url, timeout=15) as client:
        resp = await client.request(method, path, headers=headers, json=json)
    try:
        data = resp.json()
    except ValueError as exc:
        raise VibeApiError(resp.status_code, {"non_json_body": resp.text[:500]}) from exc
    if resp.status_code >= 400 or not data.get("success", True):
        raise VibeApiError(resp.status_code, data)
    return data


async def create_group_chat(title: str, user_ids: list[int]) -> int:
    """
    POST /v1/chats — создаёт групповой чат (im.chat.add). Только X-Api-Key
    (vibe_app_...), сессия НЕ нужна — фоновая операция от имени приложения
    (см. README, раздел 9). Возвращает числовой chatId.

    Используется на Этапе 3: один отдельный чат на каждый подключаемый
    сотрудником внешний портал (README, раздел 1).
    """
    settings = get_settings()
    data = await _request(
        "POST",
        "/v1/chats",
        headers={"X-Api-Key": settings.vibe_app_key},
        json={"title": title, "users": user_ids},
    )
    return data["data"]


async def send_chat_message(chat_id: int, text: str) -> int:
    """
    POST /v1/chats/chat{chatId}/messages — отправляет сообщение в групповой чат
    (im.message.add). Возвращает ID отправленного сообщения.
    """
    settings = get_settings()
    dialog_id = f"chat{chat_id}"
    data = await _request(
        "POST",
        f"/v1/chats/{dialog_id}/messages",
        headers={"X-Api-Key": settings.vibe_app_key},
        json={"message": text},
    )
    return data["data"]


async def get_me(session_bearer: str | None = None) -> dict:
    """
    GET /v1/me — с ключом vibe_app_... приложения.

    Если передан session_bearer (значение из заголовка X-Vibe-Authorization,
    который Gateway подставляет сотруднику при открытии placement), ответ
    будет содержать currentUser.bitrixUserId — реального сотрудника,
    который сейчас смотрит на виджет.

    Без session_bearer ответ описывает сам ключ (currentUser: null) — этого
    достаточно для проверки живости приложения, но не для идентификации
    конкретного сотрудника.
    """
    settings = get_settings()
    headers = {"X-Api-Key": settings.vibe_app_key}
    if session_bearer:
        headers["Authorization"] = session_bearer if session_bearer.startswith("Bearer ") else f"Bearer {session_bearer}"

    async with httpx.AsyncClient(base_url=settings.vibe_api_base_url, timeout=15) as client:
        resp = await client.get("/v1/me", headers=headers)

    try:
        data = resp.json()
    except ValueError as exc:
        raise VibeApiError(resp.status_code, {"non_json_body": resp.text[:500]}) from exc
    if resp.status_code >= 400 or not data.get("success", True):
        raise VibeApiError(resp.status_code, data)
    return data["data"]


async def exchange_code_for_session(code: str, redirect_uri: str) -> dict:
    """
    POST /v1/oauth/token — обмен одноразового кода на сессионный токен
    (vibe_session_...). Используется в разовом скрипте bind_placement.py
    и при self-hosted OAuth-редиректе, если появится (см. README, раздел 2).
    """
    settings = get_settings()
    payload = {
        "app_key": settings.vibe_app_key,
        "code": code,
        "redirect_uri": redirect_uri,
    }
    async with httpx.AsyncClient(base_url=settings.vibe_api_base_url, timeout=15) as client:
        resp = await client.post("/v1/oauth/token", json=payload)

    data = resp.json()
    if resp.status_code >= 400 or not data.get("success", True):
        raise VibeApiError(resp.status_code, data)
    return data


async def bind_placement(session_bearer: str, placement: str, handler_url: str, title: str) -> dict:
    """
    POST /v1/placements/bind — регистрация виджета в интерфейсе Битрикс24.
    Разовая операция администратора (см. README, раздел 4.1) — требует
    ключ vibe_app_... + сессионный Bearer-токен, полученный через OAuth.
    """
    settings = get_settings()
    headers = {
        "X-Api-Key": settings.vibe_app_key,
        "Authorization": session_bearer if session_bearer.startswith("Bearer ") else f"Bearer {session_bearer}",
    }
    payload = {"placement": placement, "handler": handler_url, "title": title}

    async with httpx.AsyncClient(base_url=settings.vibe_api_base_url, timeout=15) as client:
        resp = await client.post("/v1/placements/bind", headers=headers, json=payload)

    data = resp.json()
    if resp.status_code >= 400 or not data.get("success", True):
        raise VibeApiError(resp.status_code, data)
    return data
