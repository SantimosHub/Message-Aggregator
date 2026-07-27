"""
Проверка credentials внешнего портала при подключении (Этап 3, PLAN.md).

Два способа авторизации внешнего портала (README, раздел 2):
- `vibe_api` — личный ключ Вайбкод, выпущенный НА ВНЕШНЕМ портале. Проверяем
  через GET /v1/me на той же платформе Вайбкод (общий для всех порталов
  https://vibecode.bitrix24.tech) — она сама определяет, какому порталу
  принадлежит ключ, по самому ключу.
- `webhook` — обычный входящий вебхук Битрикс24. Проверяем прямым вызовом
  метода `profile` по URL вебхука (это уже не Vibe API, а сырой REST
  Битрикс24 — см. README, раздел 4.2, п.2).

ВАЖНО про webhook-запросы: обнаружен воспроизводимый обрыв соединения
(ERR_HTTP2_PROTOCOL_ERROR/502 на уровне Gateway, БЕЗ долёта до нашего кода)
именно при вызове `httpx.AsyncClient` на РЕАЛЬНЫЙ живой внешний Битрикс24-портал
изнутри живого запроса, проксируемого через настоящую placement-сессию Gateway
(при этом тот же вызов через `/exec`, через `api-bearer`-токен, или на
несуществующий/сторонний домен — работал нормально). Похоже на платформенную
особенность туннеля Gateway. Обходной путь — синхронный `requests` в отдельном
потоке (`asyncio.to_thread`) вместо `httpx.AsyncClient` для запросов на ПРОИЗВОЛЬНЫЕ
внешние Битрикс24-порталы (для запросов на саму vibecode.bitrix24.tech проблема
не проявлялась, там `httpx.AsyncClient` оставлен как есть).
"""
from __future__ import annotations

import asyncio

import httpx
import requests

VIBE_API_BASE_URL = "https://vibecode.bitrix24.tech"


class ExternalPortalCredentialsError(Exception):
    """Невалидные credentials внешнего портала — понятная ошибка для ответа виджету."""


async def validate_vibe_api_key(key: str) -> dict:
    """
    Проверяет личный ключ vibe_api_... внешнего портала: GET /v1/me.
    Требования (README, раздел 8): скоуп `im` обязателен, режим — «Только чтение».
    Возвращает сырые данные ответа (portal, scopes, accessMode) для логирования/отображения.
    """
    async with httpx.AsyncClient(base_url=VIBE_API_BASE_URL, timeout=15) as client:
        resp = await client.get("/v1/me", headers={"X-Api-Key": key})

    try:
        data = resp.json()
    except ValueError as exc:
        raise ExternalPortalCredentialsError(
            "Платформа Вайбкод вернула не-JSON ответ при проверке ключа"
        ) from exc

    if resp.status_code >= 400 or not data.get("success", True):
        error = data.get("error", {})
        raise ExternalPortalCredentialsError(
            f"Ключ невалиден: {error.get('code', 'UNKNOWN')} — {error.get('message', data)}"
        )

    payload = data["data"]
    scopes = payload.get("scopes", [])
    if "im" not in scopes:
        raise ExternalPortalCredentialsError(
            f"У ключа нет скоупа 'im' (есть только {scopes}) — не сможем читать сообщения"
        )
    return payload


def _sync_get_json(url: str) -> tuple[int, dict | None, str]:
    """Синхронный GET (для запуска в отдельном потоке). Возвращает (status, json_или_None, raw_text)."""
    resp = requests.get(url, timeout=15)
    try:
        return resp.status_code, resp.json(), resp.text
    except ValueError:
        return resp.status_code, None, resp.text


async def validate_webhook(webhook_url: str) -> dict:
    """
    Проверяет входящий вебхук Битрикс24: вызов метода `profile`.
    webhook_url ожидается в виде https://portal.bitrix24.ru/rest/12345/xxxxxxxx/
    (со слэшем на конце — методы дописываются прямо к нему).
    """
    url = webhook_url.rstrip("/") + "/profile.json"
    try:
        status_code, data, raw_text = await asyncio.to_thread(_sync_get_json, url)
    except requests.RequestException as exc:
        raise ExternalPortalCredentialsError(
            f"Не удалось подключиться по URL вебхука: {exc}"
        ) from exc

    if data is None:
        raise ExternalPortalCredentialsError(
            "Вебхук вернул не-JSON ответ — проверь правильность URL"
        )

    if status_code >= 400 or "error" in data:
        raise ExternalPortalCredentialsError(
            f"Вебхук невалиден: {data.get('error', 'unknown')} — "
            f"{data.get('error_description', '')}"
        )
    return data


async def check_webhook_scope(webhook_url: str) -> list[str]:
    """
    Проверяет реально выданные права вебхука через метод `scope`
    (README, раздел 2: нужны `im`, `imopenlines`, опционально `user`).
    """
    url = webhook_url.rstrip("/") + "/scope.json"
    status_code, data, raw_text = await asyncio.to_thread(_sync_get_json, url)
    if data is None or status_code >= 400 or "error" in data:
        raise ExternalPortalCredentialsError(f"Не удалось получить scope вебхука: {raw_text[:300]}")
    scopes = data.get("result", [])
    if "im" not in scopes:
        raise ExternalPortalCredentialsError(
            f"У вебхука нет права 'im' (есть только {scopes}) — не сможем читать сообщения"
        )
    return scopes
