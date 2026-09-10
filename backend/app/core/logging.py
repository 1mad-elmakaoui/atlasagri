"""Journalisation structurée.

Les journaux servent à l'exploitation et à l'audit, pas au débogage improvisé.
Un filtre supprime les valeurs sensibles : une clé d'API ne doit jamais
apparaître dans un journal, y compris par accident via un objet de contexte.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

from app.core.config import settings

_SENSITIVE_KEY_PATTERN = re.compile(
    r"(api[_-]?key|secret|password|token|authorization|client[_-]?secret)", re.IGNORECASE
)
_REDACTED = "«masqué»"


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: (_REDACTED if _SENSITIVE_KEY_PATTERN.search(str(k)) else _redact(v))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(v) for v in value]
    return value


class JsonFormatter(logging.Formatter):
    """Format JSON : exploitable par un agrégateur sans parsing fragile."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        context = getattr(record, "context", None)
        if context:
            payload["context"] = _redact(context)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter() if settings.is_production else logging.Formatter(
            "%(asctime)s %(levelname)-8s %(name)s :: %(message)s", datefmt="%H:%M:%S"
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level)
    # Les journaux d'accès uvicorn font doublon avec notre middleware.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.LoggerAdapter:
    return _ContextLogger(logging.getLogger(name), {})


class _ContextLogger(logging.LoggerAdapter):
    """Permet `logger.info("...", context={...})` sans casser la signature standard."""

    def process(self, msg: str, kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        context = kwargs.pop("context", None)
        if context is not None:
            kwargs.setdefault("extra", {})["context"] = context
        return msg, kwargs
