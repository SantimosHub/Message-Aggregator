"""
Подключение к БД и инициализация схемы (Этап 2, PLAN.md).

СХЕМА — по README.md, раздел 4.3, таблица `external_portals`, с учётом правки:
`last_message_cursor` хранится как JSON-карта {dialog_id: last_message_id},
а не одно значение на портал (см. README для обоснования).

ВАЖНО про путь к файлу БД: на galaxy-сервере ФС эфемерна между деплоями —
файл БД должен лежать в /data (единственный переживающий передеплой том).
См. DATABASE_URL в .env / config.py.
"""
from __future__ import annotations

import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import aiosqlite

from .config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS external_portals (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id        INTEGER NOT NULL,
    domain               TEXT NOT NULL,
    auth_type            TEXT NOT NULL CHECK (auth_type IN ('vibe_api', 'webhook')),
    credentials          TEXT NOT NULL,
    main_chat_id         INTEGER,
    last_message_cursor  TEXT NOT NULL DEFAULT '{}',
    status               TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'error', 'disabled')),
    created_at           TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_external_portals_owner
    ON external_portals (owner_user_id);
"""


def _sqlite_path_from_url(database_url: str) -> Path:
    """
    Извлекает путь к файлу из строки вида sqlite:///./db/app.db или sqlite:////data/app.db.
    (три слэша — относительный путь, четыре — абсолютный, как в стандарте SQLAlchemy).
    """
    match = re.match(r"^sqlite:///(/?.*)$", database_url)
    if not match:
        raise ValueError(
            f"Ожидается database_url вида sqlite:///путь, получено: {database_url!r}"
        )
    raw_path = match.group(1)
    # Если исходная строка была sqlite:////abs/path — после одного среза "///" останется "/abs/path"
    return Path(raw_path)


def get_db_path() -> Path:
    settings = get_settings()
    return _sqlite_path_from_url(settings.database_url)


@asynccontextmanager
async def get_connection() -> AsyncIterator[aiosqlite.Connection]:
    """Контекстный менеджер соединения с БД. Использовать через `async with`."""
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    try:
        await conn.execute("PRAGMA foreign_keys = ON")
        yield conn
    finally:
        await conn.close()


async def init_db() -> None:
    """Создаёт таблицы, если их ещё нет. Вызывать один раз при старте приложения."""
    async with get_connection() as conn:
        await conn.executescript(SCHEMA)
        await conn.commit()
