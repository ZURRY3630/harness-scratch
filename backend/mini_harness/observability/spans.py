# DOC: docs/12-observability.md
"""Span 与 Tracer：一次运行的调用链（指南 11.1 / 11.5 的分布式追踪）。

层级约定（都是同一条 trace 上的父子 span）：

    run                一次 engine.run()
    ├── llm_call       一次模型调用（含流式耗时、token、缓存命中）
    ├── tool_execute   一次工具执行（含权限决策、耗时、结果状态）
    └── approval_wait  审批等待时长（记在 tool_execute 的 attributes 上）

字段沿用 OTLP 的语义（trace_id / span_id / parent_span_id，8 字节十六进制 span_id），
但输出格式是本项目自己的扁平 JSON，需要接 OTLP 时写一个 `SpanSink` 适配即可。

`Tracer` 本身不做采样与批处理：写入成本由 `SpanSink` 决定。未配置任何 sink 时是纯 no-op。
"""

from __future__ import annotations

import contextlib
import time
import uuid
from abc import ABC, abstractmethod
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

from .logging import current_session_id, current_trace_id

_current_span: ContextVar[Optional["Span"]] = ContextVar("harness_current_span", default=None)


def _new_id(bytes_len: int) -> str:
    """十六进制随机 id：span_id 8 字节、trace_id 16 字节（对齐 OTLP 尺寸）。"""
    return uuid.uuid4().hex[: bytes_len * 2]


@dataclass
class Span:
    """一段有起止时间的工作单元。"""

    name: str
    trace_id: str
    span_id: str = field(default_factory=lambda: _new_id(8))
    parent_span_id: Optional[str] = None
    session_id: str = ""
    start: float = field(default_factory=time.time)
    end: Optional[float] = None
    status: str = "ok"
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> int:
        """耗时毫秒；未结束时按当前时刻计算。"""
        return int(((self.end or time.time()) - self.start) * 1000)

    def finish(self, status: str = "ok", **attributes: Any) -> "Span":
        self.end = time.time()
        self.status = status
        self.attributes.update(attributes)
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "span",
            "name": self.name,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "session_id": self.session_id,
            "start": round(self.start, 4),
            "end": round(self.end, 4) if self.end else None,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "attributes": self.attributes,
        }


class SpanSink(ABC):
    """span 的落点。实现不得抛异常影响主流程（调用侧也会兜一层）。"""

    @abstractmethod
    def emit(self, span: Span) -> None:
        """接收一个已结束的 span。"""

    def flush(self) -> None:
        """刷盘；默认无事可做。"""
        return None


class NullSpanSink(SpanSink):
    """空实现：显式关闭追踪时使用。"""

    def emit(self, span: Span) -> None:
        return None


class InMemorySpanSink(SpanSink):
    """环形缓冲：保留最近 N 条 span，供 `GET /api/traces` 查询。"""

    def __init__(self, maxlen: int = 500) -> None:
        from collections import deque

        self._spans: deque[Span] = deque(maxlen=maxlen)

    def emit(self, span: Span) -> None:
        self._spans.append(span)

    def recent(self, limit: int = 50, session_id: str = "") -> list[dict[str, Any]]:
        """按时间倒序返回最近 span；可只取某个会话。"""
        items = [s for s in self._spans if not session_id or s.session_id == session_id]
        return [s.to_dict() for s in reversed(items[-limit:])]

    def clear(self) -> None:
        self._spans.clear()


# 进程级默认缓冲：即使未配置落盘，也能通过 API 回看最近调用链
SPANS = InMemorySpanSink()


class Tracer:
    """span 工厂：负责父子关系推导、计时收口与投递。"""

    def __init__(self, sinks: Optional[list[SpanSink]] = None) -> None:
        self.sinks: list[SpanSink] = list(sinks or [])

    # ----- 显式 start / finish -----
    def start(self, name: str, *, session_id: str = "", trace_id: str = "", **attributes: Any) -> Span:
        """开启一个 span。父 span、trace_id、session_id 从当前上下文自动推导。"""
        parent = _current_span.get()
        return Span(
            name=name,
            trace_id=trace_id or current_trace_id() or (parent.trace_id if parent else _new_id(16)),
            parent_span_id=parent.span_id if parent else None,
            session_id=session_id or current_session_id() or (parent.session_id if parent else ""),
            attributes=dict(attributes),
        )

    def finish(self, span: Span, status: str = "ok", **attributes: Any) -> Span:
        """结束 span 并投递到所有 sink；投递失败不影响主流程。"""
        span.finish(status, **attributes)
        self.emit(span)
        return span

    def emit(self, span: Span) -> None:
        for sink in self.sinks:
            try:
                sink.emit(span)
            except Exception:  # noqa: BLE001 —— 观测链路故障不得影响业务
                pass

    def flush(self) -> None:
        for sink in self.sinks:
            try:
                sink.flush()
            except Exception:  # noqa: BLE001
                pass

    # ----- 上下文 -----
    def attach(self, span: Span) -> Token:
        """把 span 设为"当前 span"，使其后开启的 span 自动成为它的子节点。"""
        return _current_span.set(span)

    def detach(self, token: Token) -> None:
        _current_span.reset(token)

    @contextlib.contextmanager
    def span(self, name: str, *, session_id: str = "", **attributes: Any) -> Iterator[Span]:
        """`with tracer.span("x") as sp:` —— 自动挂父节点、异常置 error、退出即投递。"""
        span = self.start(name, session_id=session_id, **attributes)
        token = self.attach(span)
        try:
            yield span
        except Exception as e:  # noqa: BLE001 —— 记录后原样抛出
            self.finish(span, status="error", error=type(e).__name__)
            raise
        finally:
            if span.end is None:
                self.finish(span, status="ok")
            self.detach(token)

    @staticmethod
    def current() -> Optional[Span]:
        """当前上下文中的 span（无则 None）。"""
        return _current_span.get()
