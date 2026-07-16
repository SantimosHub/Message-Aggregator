"""
Конфигурация приложения. Все секреты — только из переменных окружения,
никогда не хардкодим в коде и не коммитим в git (см. .env.example).
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Ключ авторизации (OAuth app) основного портала — встраивание виджета,
    # создание чатов/отправка сообщений. НИКОГДА не в БД, не в коде.
    vibe_app_key: str = ""

    # Базовый URL самого Vibe API (одинаковый для всех порталов).
    vibe_api_base_url: str = "https://vibecode.bitrix24.tech"

    # Публичный URL этого backend-приложения (нужен для placement handler и OAuth redirect_uri).
    app_base_url: str = ""

    # Заголовок пункта меню / плейсмента в интерфейсе Битрикс24.
    placement_title: str = "Message Aggregator"

    # Строка подключения к БД. ЛОКАЛЬНО можно оставить как есть.
    # В ПРОДАКШЕНЕ (galaxy-сервер) ФС эфемерна между деплоями — файл БД
    # обязательно должен лежать по пути /data/... (единственный том,
    # переживающий передеплой), например: sqlite:////data/app.db
    database_url: str = "sqlite:///./db/app.db"

    # Ключ шифрования для хранения credentials внешних порталов в БД (Этап 2).
    credentials_encryption_key: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
