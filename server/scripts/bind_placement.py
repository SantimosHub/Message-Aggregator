"""
РАЗОВЫЙ скрипт настройки (см. PLAN.md, Этап 1).

Регистрирует виджет приложения в интерфейсе Битрикс24 основного портала
(POST /v1/placements/bind). Это нужно сделать один раз при разворачивании —
обычные сотрудники после этого открывают виджет уже без ручного OAuth,
Gateway сам подставляет им X-Vibe-Authorization.

Зачем интерактивный скрипт, а не автоматика: страница согласия Битрикс24
отдаёт X-Frame-Options и не открывается внутри iframe/скрипта — шаг логина
администратора должен пройти в обычном браузере.

Использование:
    cd server
    python -m scripts.bind_placement

Переменные окружения (см. .env.example):
    VIBE_APP_KEY      — ключ авторизации (vibe_app_...)
    APP_BASE_URL      — публичный URL этого backend (после деплоя на Black Hole)
    PLACEMENT_TITLE   — заголовок пункта меню (по умолчанию "Message Aggregator")
"""
from __future__ import annotations

import asyncio
import secrets
import sys

from app.config import get_settings
from app.vibe_client import bind_placement, exchange_code_for_session

# Наиболее подходящий placement для "пункт меню на весь портал":
# LEFT_MENU показывает приложение как отдельный раздел в левом меню Битрикс24.
DEFAULT_PLACEMENT = "LEFT_MENU"


async def main() -> None:
    settings = get_settings()

    if not settings.vibe_app_key:
        print("ОШИБКА: не задан VIBE_APP_KEY в .env", file=sys.stderr)
        sys.exit(1)
    if not settings.app_base_url:
        print("ОШИБКА: не задан APP_BASE_URL в .env (публичный URL backend после деплоя)", file=sys.stderr)
        sys.exit(1)

    redirect_uri = f"{settings.app_base_url.rstrip('/')}/oauth/callback"
    state = secrets.token_urlsafe(24)
    authorize_url = (
        f"{settings.vibe_api_base_url}/v1/oauth/authorize"
        f"?app_key={settings.vibe_app_key}&redirect_uri={redirect_uri}&state={state}"
    )

    print("Шаг 1. Открой эту ссылку в браузере под администратором основного портала:\n")
    print(f"  {authorize_url}\n")
    print("После входа в Битрикс24 тебя перенаправит на redirect_uri с параметрами ?code=...&state=...")
    print(f"redirect_uri должен быть заранее зарегистрирован в настройках приложения как: {redirect_uri}\n")

    code = input("Шаг 2. Вставь сюда значение параметра code из адресной строки после редиректа: ").strip()
    returned_state = input("Вставь значение параметра state (для проверки CSRF): ").strip()

    if returned_state != state:
        print("ОШИБКА: state не совпадает — возможна подмена запроса, прерываю.", file=sys.stderr)
        sys.exit(1)

    print("\nШаг 3. Обмениваю code на сессионный токен...")
    token_response = await exchange_code_for_session(code=code, redirect_uri=redirect_uri)
    session_token = token_response["access_token"]
    print(f"Получен сессионный токен (действует {token_response.get('expires_in', '?')} сек).")

    print("\nШаг 4. Регистрирую placement...")
    handler_url = f"{settings.app_base_url.rstrip('/')}/placement-handler"
    result = await bind_placement(
        session_bearer=session_token,
        placement=DEFAULT_PLACEMENT,
        handler_url=handler_url,
        title=settings.placement_title,
    )
    print("Готово! Ответ платформы:")
    print(result)
    print(
        "\nТеперь сотрудники основного портала должны увидеть "
        f"«{settings.placement_title}» в левом меню Битрикс24."
    )


if __name__ == "__main__":
    asyncio.run(main())
