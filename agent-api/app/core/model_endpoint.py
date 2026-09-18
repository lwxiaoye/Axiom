"""Request/task-local model connection; inherited by child tasks, never global settings."""
from contextvars import ContextVar
from typing import Optional

_connection: ContextVar[Optional[dict]] = ContextVar("model_connection", default=None)


def bind_model_connection(connection: Optional[dict]) -> None:
    _connection.set(connection)


def get_model_connection() -> Optional[dict]:
    return _connection.get()


def get_model_base_url() -> str:
    from app.core.config import settings
    connection = _connection.get()
    return connection["base_url"] if connection else settings.NEWAPI_BASE_URL
