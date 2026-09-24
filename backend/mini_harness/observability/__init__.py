"""可观测性：结构化日志 + 调用链 span + 指标与告警 + Trace 落盘。

对外入口（详见 docs/12-observability.md）：

    setup_logging()           初始化内核日志（JSON 行 / 文本行）
    get_logger(__name__)      取结构化 logger（自动带 trace_id / session_id）
    Tracer / Span / SpanSink  调用链
    MetricsRegistry / METRICS 指标注册表
    AlertEvaluator            阈值告警
    TraceWriter               事件落盘接口
"""

from __future__ import annotations

from .logging import (
    StructuredLogger,
    current_session_id,
    current_trace_id,
    get_logger,
    reset_trace_context,
    set_trace_context,
    setup_logging,
)
from .metrics import METRICS, Alert, AlertEvaluator, AlertThresholds, MetricsRegistry
from .spans import SPANS, InMemorySpanSink, NullSpanSink, Span, SpanSink, Tracer
from .trace import JsonlTraceWriter, NullTraceWriter, TraceWriter

__all__ = [
    # 日志
    "setup_logging",
    "get_logger",
    "StructuredLogger",
    "set_trace_context",
    "reset_trace_context",
    "current_trace_id",
    "current_session_id",
    # 调用链
    "Tracer",
    "Span",
    "SpanSink",
    "NullSpanSink",
    "InMemorySpanSink",
    "SPANS",
    # 指标
    "MetricsRegistry",
    "METRICS",
    "Alert",
    "AlertEvaluator",
    "AlertThresholds",
    # 落盘
    "TraceWriter",
    "NullTraceWriter",
    "JsonlTraceWriter",
]
