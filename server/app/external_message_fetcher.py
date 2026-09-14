"""
Опрос внешнего портала на новые сообщения (Этап 4, PLAN.md).

Источники (оба используются одновременно, см. решение по Этапу 4):
- обычные диалоги/групповые чаты — `im.recent.list` для обнаружения диалогов
  с новой активностью, `im.dialog.messages.get` для чтения самих сообщений;
- диалоги открытых линий — те же `im.recent.list` (поле `lines` в ответе
  отмечает такие диалоги), но сообщения читаются через
  `imopenlines.session.history.get` (единственный способ читать историю
  открытой линии, не будучи участником чата — подтверждено документацией
  Битрикс24 REST).

Все вызовы идут через входящий вебхук напрямую на REST Битрикс24 домена
портала, синхронным `requests` в отдельном потоке (см.
external_portal_client.py — обходной путь вокруг обрыва соединения Gateway
на произвольные внешние домены). Авторизация через личный ключ Вайбкод
(`vibe_api`) как способ подключения внешнего портала убрана — контракт
прокси-эндпоинта `/v1/batch` для этого сценария не был проверен вживую, и
через него не определить ID сотрудника на внешнем портале для пометки
собственных сообщений прочитанными (см. db.py, owner_external_user_id).

Логика курсора (см. README, раздел 4.3):
- `last_message_cursor` — JSON-карта {dialog_id: last_message_id}.
- Если диалог встречается ВПЕРВЫЕ (нет в карте) — не подтягиваем всю
  историю, а просто запоминаем текущий последний id как отправную точку
  (иначе при первом подключении портала в чат обвалится вся история).
- Дальше — только сообщения с id строго больше сохранённого курсора.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

import requests

from .repositories.external_portals import ExternalPortal


class ExternalPortalApiError(Exception):
    """Ошибка при опросе внешнего портала — авторизационная или сетевая."""

    def __init__(self, message: str, *, is_auth_error: bool = False):
        super().__init__(message)
        self.is_auth_error = is_auth_error


@dataclass
class FetchedMessage:
    dialog_id: str
    dialog_title: str
    is_open_line: bool
    message_id: int
    author_id: Optional[str]  # числовой ID автора НА ВНЕШНЕМ портале (str или None) — для сравнения с owner_external_user_id
    author_name: str
    text: str
    date: str


# --------------------------------------------------------------------------
# Низкоуровневый вызов методов Битрикс24 через входящий вебхук.
# --------------------------------------------------------------------------


def _sync_post_json(url: str, payload: dict) -> tuple[int, dict | None]:
    resp = requests.post(url, json=payload, timeout=15)
    try:
        return resp.status_code, resp.json()
    except ValueError:
        return resp.status_code, None


async def _webhook_call(webhook_url: str, method: str, params: dict) -> dict:
    """Прямой вызов метода REST Битрикс24 через входящий вебхук."""
    url = webhook_url.rstrip("/") + f"/{method}.json"
    try:
        status_code, data = await asyncio.to_thread(_sync_post_json, url, params)
    except requests.RequestException as exc:
        raise ExternalPortalApiError(f"Сетевая ошибка вебхука ({method}): {exc}") from exc

    if data is None:
        raise ExternalPortalApiError(f"Вебхук вернул не-JSON ответ ({method})")
    if status_code in (401, 403) or data.get("error") in ("NO_AUTH_FOUND", "INVALID_CREDENTIALS", "expired_token"):
        raise ExternalPortalApiError(
            f"Вебхук неавторизован ({method}): {data.get('error_description', data)}",
            is_auth_error=True,
        )
    if "error" in data:
        raise ExternalPortalApiError(f"Ошибка вебхука ({method}): {data.get('error_description', data['error'])}")
    return data.get("result", {})


async def _call_method(portal: ExternalPortal, method: str, params: dict) -> dict:
    return await _webhook_call(portal.credentials, method, params)


# --------------------------------------------------------------------------
# Высокоуровневая логика: найти диалоги с активностью, вытащить новые
# сообщения, посчитать новый курсор.
# --------------------------------------------------------------------------


def _author_display_name(user_id, users_map: dict) -> str:
    user = users_map.get(str(user_id)) or users_map.get(user_id)
    if not user:
        return f"Пользователь #{user_id}"
    name = " ".join(filter(None, [user.get("first_name"), user.get("last_name")])).strip()
    return name or user.get("name") or f"Пользователь #{user_id}"


async def _fetch_regular_dialog_messages(
    portal: ExternalPortal, dialog_id: str, after_id: int
) -> list[FetchedMessage]:
    result = await _call_method(portal, "im.dialog.messages.get", {"DIALOG_ID": dialog_id, "LIMIT": 50})
    messages = result.get("messages", []) if isinstance(result, dict) else []
    users_map = result.get("users", {}) if isinstance(result, dict) else {}

    fetched = []
    for msg in messages:
        msg_id = int(msg.get("id", 0))
        if msg_id <= after_id:
            continue
        fetched.append(
            FetchedMessage(
                dialog_id=dialog_id,
                dialog_title=dialog_id,
                is_open_line=False,
                message_id=msg_id,
                author_id=str(msg.get("author_id")) if msg.get("author_id") is not None else None,
                author_name=_author_display_name(msg.get("author_id"), users_map),
                text=msg.get("text", ""),
                date=msg.get("date", ""),
            )
        )
    return fetched


async def _fetch_open_line_messages(
    portal: ExternalPortal, dialog_id: str, chat_id: int, after_id: int
) -> list[FetchedMessage]:
    result = await _call_method(portal, "imopenlines.session.history.get", {"CHAT_ID": chat_id})
    message_map = result.get("message", {}) if isinstance(result, dict) else {}

    fetched = []
    for msg_id_str, msg in message_map.items():
        msg_id = int(msg_id_str)
        if msg_id <= after_id:
            continue
        # senderid == "0" — служебные события чата открытой линии (создание
        # лида, смена названия и т.п.), а не сообщения клиента. Подтверждено
        # официальной документацией Битрикс24 (пример ответа
        # imopenlines.session.history.get: senderid":"0" на сообщении
        # "[b]Создан новый лид[/b]"). Такие события не пересылаем — иначе
        # они выглядят как будто их написал "Клиент открытой линии #0".
        if str(msg.get("senderid")) == "0":
            continue
        fetched.append(
            FetchedMessage(
                dialog_id=dialog_id,
                dialog_title=dialog_id,
                is_open_line=True,
                message_id=msg_id,
                author_id=str(msg.get("senderid")) if msg.get("senderid") is not None else None,
                author_name=f"Клиент открытой линии #{msg.get('senderid', '?')}",
                text=msg.get("text", ""),
                date=msg.get("date", ""),
            )
        )
    return fetched


async def fetch_new_messages(portal: ExternalPortal) -> tuple[list[FetchedMessage], dict[str, str]]:
    """
    Опрашивает один внешний портал: находит диалоги с новой активностью
    (`im.recent.list`) и вытаскивает по ним новые сообщения.

    Возвращает (новые_сообщения, обновлённая_карта_курсора). Карту курсора
    нужно сохранить через repo.update_cursor ПОСЛЕ успешной обработки
    сообщений (пересылки) вызывающим кодом — см. poller.py.
    """
    result = await _call_method(
        portal,
        "im.recent.list",
        {
            "SKIP_OPENLINES": "N",
            "SKIP_DIALOG": "N",
            "SKIP_CHAT": "N",
            "SKIP_UNDISTRIBUTED_OPENLINES": "Y",
            "LIMIT": 50,
        },
    )
    items = result.get("items", []) if isinstance(result, dict) else []

    cursor = dict(portal.last_message_cursor)
    new_messages: list[FetchedMessage] = []

    for item in items:
        dialog_id = str(item.get("id"))
        last_message = item.get("message") or {}
        last_id_in_recent = int(last_message.get("id", 0))
        is_open_line = item.get("lines") is not None

        if dialog_id not in cursor:
            # Диалог видим впервые — не тянем историю, запоминаем точку старта.
            cursor[dialog_id] = str(last_id_in_recent)
            continue

        known_last_id = int(cursor.get(dialog_id) or 0)
        if last_id_in_recent <= known_last_id:
            continue  # ничего нового с прошлого опроса

        if is_open_line:
            chat_id = item.get("chat_id") or (item.get("chat") or {}).get("id")
            dialog_messages = await _fetch_open_line_messages(portal, dialog_id, chat_id, known_last_id)
        else:
            dialog_messages = await _fetch_regular_dialog_messages(portal, dialog_id, known_last_id)

        if dialog_messages:
            new_max_id = max(m.message_id for m in dialog_messages)
            cursor[dialog_id] = str(max(new_max_id, last_id_in_recent))
            title = (item.get("title") or dialog_id)
            for m in dialog_messages:
                m.dialog_title = title
            new_messages.extend(dialog_messages)
        else:
            cursor[dialog_id] = str(last_id_in_recent)

    return new_messages, cursor
