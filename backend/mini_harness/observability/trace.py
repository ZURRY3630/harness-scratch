# DOC: docs/12-observability.md
"""Trace 落盘接口：把引擎事件流与调用链 span 持久化，供回放 / 评测 / 排障。

一次运行会产生两类记录，写进同一份 JSONL，靠 `kind` 字段区分：

    {"kind": "event", "ts": ..., "event": "tool_executed", "session_id": ..., "data": {...}}
    {"kind": "span",  "name": "tool_execute", "trace_id": ..., "span_id": ..., "duration_ms": ...}

契约：
- `write(event)` / `emit(span)` 只做序列化与缓冲，不得阻塞主流程；
- `flush()` 把缓冲刷盘（引擎淘汰、进程退出前调用）；
- 引擎侧对异常再兜一层——观测链路故障不能拖垮 Agent。

启用方式：环境变量 `HARNESS_TRACE_PATH` 指向输出文件（见 api/routes.py 的 build_engine）。
未设置时不落盘；此时仍有进程内的 span 环形缓冲可供 `GET /api/traces` 查询。
"""

from __future__ import annotations

import abc
import json
from pathlib import Path

from ..core.events import Event
from .spans import Span, SpanSink


class TraceWriter(abc.ABC):
    """事件落盘接口。"""

    @abc.abstractmethod
    def write(self, event: Event) -> None:
        """写入一个事件（应快速返回，不阻塞主流程）。"""

    @abc.abstractmethod
    def flush(self) -> None:
        """把缓冲刷盘。"""


class NullTraceWriter(TraceWriter):
    """空实现：用于显式关闭追踪或测试注入。"""

    def write(self, event: Event) -> None:
        return None

    def flush(self) -> None:
        return None


class JsonlTraceWriter(TraceWriter, SpanSink):
    """最小可用实现：一行一条 JSON（JSONL），便于 tail / grep / 离线加载。

    同时实现 `SpanSink`，因此同一个实例既是事件落点、也是 span 落点，
    由 build_engine 一并注入引擎与 Tracer，两份记录天然按时间交错、顺序一致。
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a", encoding="utf-8")

    def write(self, event: Event) -> None:
        self._write({"kind": "event", **event.to_trace_dict()})

    def emit(self, span: Span) -> None:
        self._write(span.to_dict())

    def flush(self) -> None:
        self._fh.flush()

    def close(self) -> None:
        self.flush()
        self._fh.close()

    def _write(self, record: dict) -> None:
        self._fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
