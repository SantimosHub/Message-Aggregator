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

Способ вызова методов зависит от auth_type записи (README, раздел 2):
- `webhook` — напрямую REST Битрикс24 на домене портала, синхронным
  `requests` в отдельном потоке (см. external_portal_client.py — обходной
  путь вокруг обрыва соединения Gateway на произвольные внешние домены).
- `vibe_api` — через платформу Вайбкод, `POST /v1/batch` (см. PLAN.md,
  Этап 4). ВАЖНО: конкретный контракт этого прокси-эндпоинта для
  произвольных REST-методов (не задокументированных в открытом Entity API)
  не был проверен вживую в рамках этой сессии — реализация ниже следует
  стандартному формату Битрикс batch (`cmd: {alias: "method?querystring"}`)
  по аналогии с уже подтверждёнными вызовами `/v1/chats`. Перед боевым
  использованием обязательно проверить одним реальным vibe_api-порталом
  (см. Этап 7 плана) и поправить `_vibe_batch_call`, если формат отличается.

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
from urllib.parse import urlencode

import httpx
import requests

from .repositories.external_portals import ExternalPortal

VIBE_API_BASE_URL = "https://vibecode.bitrix24.tech"


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
    author_name: str
    text: str
    date: str


# --------------------------------------------------------------------------
# Низкоуровневые вызовы методов Битрикс24, в зависимости от auth_type.
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


async def _vibe_batch_call(vibe_api_key: str, method: str, params: dict) -> dict:
    """
    Вызов произвольного REST-метода внешнего портала через Vibe API,
    POST /v1/batch (см. предупреждение в докстринге модуля выше — контракт
    не проверен вживую, следует стандартному формату Битрикс batch).
    """
    cmd_value = method if not params else f"{method}?{urlencode(params)}"
    body = {"halt": 0, "cmd": {"call": cmd_value}}

    async with httpx.AsyncClient(base_url=VIBE_API_BASE_URL, timeout=20) as client:
        resp = await client.post("/v1/batch", headers={"X-Api-Key": vibe_api_key}, json=body)

    try:
        data = resp.json()
    except ValueError as exc:
        raise ExternalPortalApiError(f"Vibe API вернул не-JSON ответ ({method})") from exc

    if resp.status_code in (401, 403):
        raise ExternalPortalApiError(
            f"Ключ vibe_api невалиден/отозван ({method}): {data}", is_auth_error=True
        )
    if resp.status_code >= 400 or not data.get("success", True):
        raise ExternalPortalApiError(f"Ошибка Vibe API ({method}): {data}")

    # Формат batch-ответа Битрикс24: result.result[alias] — сам результат,
    # result.result_error[alias] — ошибка конкретного под-вызова.
    result = data.get("data") or data.get("result") or {}
    batch_result = result.get("result", result)
    if isinstance(batch_result, dict) and "result_error" in result and result["result_error"].get("call"):
        raise ExternalPortalApiError(f"Ошибка метода {method} внутри batch: {result['result_error']['call']}")
    return batch_result.get("call", batch_result) if isinstance(batch_result, dict) else batch_result


async def _call_method(portal: ExternalPortal, method: str, params: dict) -> dict:
    if portal.auth_type == "webhook":
        return await _webhook_call(portal.credentials, method, params)
    return await _vibe_batch_call(portal.credentials, method, params)


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
        fetched.append(
            FetchedMessage(
                dialog_id=dialog_id,
                dialog_title=dialog_id,
                is_open_line=True,
                message_id=msg_id,
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
