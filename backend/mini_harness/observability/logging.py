# DOC: docs/12-observability.md
"""结构化日志：JSON 行输出 + trace_id / session_id 贯穿（指南 11.1 / 11.5）。

用法：

    from mini_harness.observability.logging import get_logger, setup_logging

    setup_logging()                                    # 应用启动时调一次
    log = get_logger(__name__)
    log.info("工具执行完成", tool="echo", ok=True, elapsed_ms=3)

要点：
- trace_id / session_id 存在 contextvars 里，**不需要逐层传参**即可自动带上；
- `setup_logging()` 只接管 `mini_harness.*` 命名空间，不影响宿主应用自己的日志配置；
- 生产用 `HARNESS_LOG_FORMAT=json`，开发用默认的 `text`。
"""

from __future__ import annotations

import json
import logging
import os
import sys
from contextvars import ContextVar, Token
from typing import Any, Optional

# ---- trace 上下文（contextvars：协程安全，跨 await 保持）----
_trace_id: ContextVar[str] = ContextVar("harness_trace_id", default="")
_session_id: ContextVar[str] = ContextVar("harness_session_id", default="")

_ROOT_LOGGER_NAME = "mini_harness"
_LOG_FORMAT_ENV = "HARNESS_LOG_FORMAT"
_DEFAULT_LOG_FORMAT = "text"
_DEFAULT_LEVEL = "INFO"


def set_trace_context(trace_id: str = "", session_id: str = "") -> tuple[Token, Token]:
    """设置当前上下文的 trace_id / session_id，返回 token 供还原。"""
    return _trace_id.set(trace_id), _session_id.set(session_id)


def reset_trace_context(tokens: tuple[Token, Token]) -> None:
    """还原 trace 上下文（与 `set_trace_context` 成对使用）。"""
    _trace_id.reset(tokens[0])
    _session_id.reset(tokens[1])


def current_trace_id() -> str:
    return _trace_id.get()


def current_session_id() -> str:
    return _session_id.get()


class JsonFormatter(logging.Formatter):
    """一行一条 JSON：字段固定，便于被 Loki / ELK 直接采集与聚合。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "trace_id": current_trace_id(),
            "session_id": current_session_id(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(fields)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    """开发用可读格式；trace_id 截断到 8 位，避免刷屏。"""

    def format(self, record: logging.LogRecord) -> str:
        trace = current_trace_id()[:8]
        prefix = f"[{trace}] " if trace else ""
        extra = ""
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict) and fields:
            extra = " " + " ".join(f"{k}={v}" for k, v in fields.items())
        base = f"{self.formatTime(record, '%H:%M:%S')} {record.levelname:<7} {record.name} {prefix}{record.getMessage()}{extra}"
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


class StructuredLogger:
    """薄封装：`log.info("消息", key=value)` 的字段会进 JSON/文本输出。"""

    __slots__ = ("_logger",)

    def __init__(self, name: str) -> None:
        self._logger = logging.getLogger(name)

    def _log(self, level: int, message: str, exc_info: bool = False, **fields: Any) -> None:
        self._logger.log(level, message, extra={"fields": fields} if fields else None, exc_info=exc_info)

    def debug(self, message: str, **fields: Any) -> None:
        self._log(logging.DEBUG, message, **fields)

    def info(self, message: str, **fields: Any) -> None:
        self._log(logging.INFO, message, **fields)

    def warning(self, message: str, **fields: Any) -> None:
        self._log(logging.WARNING, message, **fields)

    def error(self, message: str, **fields: Any) -> None:
        self._log(logging.ERROR, message, **fields)

    def exception(self, message: str, **fields: Any) -> None:
        self._log(logging.ERROR, message, exc_info=True, **fields)


def get_logger(name: str) -> StructuredLogger:
    """取结构化 logger。传入 `__name__` 即可（内核模块名形如 `mini_harness.runtime.engine`）。"""
    return StructuredLogger(name)


def log_format() -> str:
    """当前日志格式：`json` 或 `text`（环境变量 `HARNESS_LOG_FORMAT`）。"""
    return (os.getenv(_LOG_FORMAT_ENV) or _DEFAULT_LOG_FORMAT).strip().lower()


def setup_logging(level: Optional[str] = None, fmt: Optional[str] = None) -> None:
    """初始化内核日志：接管 `mini_harness.*`，不污染宿主应用的 root logger。

    Args:
        level: 日志级别；默认取 `LOG_LEVEL` 环境变量（与 `.env` 既有配置一致），再退 `INFO`。
        fmt:  `json` 或 `text`；默认取 `HARNESS_LOG_FORMAT`，再退 `text`。
    """
    resolved_level = (level or os.getenv("LOG_LEVEL") or _DEFAULT_LEVEL).strip().upper()
    resolved_fmt = (fmt or log_format()).strip().lower()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if resolved_fmt == "json" else TextFormatter())

    logger = logging.getLogger(_ROOT_LOGGER_NAME)
    logger.handlers[:] = [handler]
    logger.setLevel(resolved_level)
    logger.propagate = False   # 交由本 handler 输出，避免上层 root 重复打印
