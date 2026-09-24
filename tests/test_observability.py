"""可观测性测试：结构化日志 / trace_id 贯穿 / span 树 / 指标分位数 / 阈值告警 / 引擎埋点。"""

from __future__ import annotations

import json
import logging
import sys
from collections import OrderedDict

import pytest

from mini_harness.core.events import EventType, ev
from mini_harness.core.message import ToolCall
from mini_harness.models.provider import ModelResponse
from mini_harness.observability.logging import (
    JsonFormatter,
    get_logger,
    reset_trace_context,
    set_trace_context,
    setup_logging,
)
from mini_harness.observability.metrics import (
    AlertEvaluator,
    AlertThresholds,
    MetricsRegistry,
)
from mini_harness.observability.spans import Tracer

from conftest import MockProvider
from test_engine import collect, make_engine


# ---------------------------------------------------------------- 日志
def test_json_log_carries_trace_and_session(capsys):
    """JSON 日志必须带 trace_id / session_id，业务字段平铺进输出。"""
    setup_logging(level="INFO", fmt="json")
    tokens = set_trace_context("trace-abc", "sess-1")
    try:
        get_logger("mini_harness.test").info("工具执行完成", tool="echo", ok=True)
    finally:
        reset_trace_context(tokens)

    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["msg"] == "工具执行完成"
    assert payload["trace_id"] == "trace-abc"
    assert payload["session_id"] == "sess-1"
    assert payload["tool"] == "echo" and payload["ok"] is True
    assert payload["level"] == "INFO"


def test_json_formatter_includes_exception():
    """异常日志要带堆栈，便于排障。"""
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.LogRecord("mini_harness.test", logging.ERROR, __file__, 1,
                                   "炸了", None, sys.exc_info())
    out = json.loads(JsonFormatter().format(record))
    assert out["msg"] == "炸了"
    assert "ValueError: boom" in out["exc"]


def test_setup_logging_does_not_touch_root(caplog):
    """只接管 mini_harness.*，不污染宿主应用的 root logger。"""
    root_handlers = list(logging.getLogger().handlers)
    setup_logging(level="DEBUG", fmt="text")
    assert list(logging.getLogger().handlers) == root_handlers
    assert logging.getLogger("mini_harness").level == logging.DEBUG


# ---------------------------------------------------------------- span
def test_tracer_builds_parent_child_tree():
    """span 自动挂到当前 span 之下，trace_id 一致，结束后投递。"""
    captured = []
    tracer = Tracer([_ListSink(captured)])

    run = tracer.start("run", session_id="s1", trace_id="t1")
    token = tracer.attach(run)
    child = tracer.start("tool_execute", tool="echo")
    tracer.finish(child, status="ok", elapsed_ms=3)
    tracer.finish(run, outcome="ok")
    tracer.detach(token)

    assert [s.name for s in captured] == ["tool_execute", "run"]
    child_span, run_span = captured
    assert child_span.parent_span_id == run_span.span_id
    assert child_span.trace_id == run_span.trace_id == "t1"
    assert child_span.session_id == "s1"
    assert child_span.to_dict()["kind"] == "span"


def test_tracer_span_context_marks_error():
    """with 语法糖：异常时 span 状态置 error 并原样抛出。"""
    captured = []
    tracer = Tracer([_ListSink(captured)])
    with pytest.raises(RuntimeError):
        with tracer.span("llm_call"):
            raise RuntimeError("模型炸了")
    assert captured[0].status == "error"
    assert captured[0].attributes["error"] == "RuntimeError"


def test_sink_failure_does_not_break_tracer():
    """观测链路故障不得影响业务：sink 抛异常时 tracer 照常返回。"""
    tracer = Tracer([_ExplodingSink()])
    span = tracer.start("run")
    tracer.finish(span, outcome="ok")   # 不抛异常即通过
    assert span.end is not None


class _ListSink:
    def __init__(self, bucket):
        self.bucket = bucket

    def emit(self, span):
        self.bucket.append(span)

    def flush(self):
        pass


class _ExplodingSink:
    def emit(self, span):
        raise RuntimeError("sink 挂了")

    def flush(self):
        raise RuntimeError("sink 挂了")


# ---------------------------------------------------------------- 指标
def test_metrics_counter_and_histogram_percentiles():
    """直方图分位数按线性插值计算；counters 求和/求比值可用。"""
    m = MetricsRegistry()
    for v in (10, 20, 30, 40, 100):
        m.observe("tool_latency_ms", v, tool="echo")
    stats = m.snapshot()["histograms"]["tool_latency_ms{tool=echo}"]
    assert stats["count"] == 5 and stats["avg"] == 40.0 and stats["max"] == 100.0
    assert stats["p50"] == 30.0
    assert 80 < stats["p99"] <= 100

    m.incr("tool_calls_total", tool="echo", status="ok")
    m.incr("tool_calls_total", tool="echo", status="ok")
    m.incr("tool_calls_total", tool="echo", status="error")
    assert m.counter("tool_calls_total", tool="echo", status="ok") == 2
    assert m.sum_counters("tool_calls_total") == 3
    assert m.rate("tool_calls_total", "tool_calls_total") == 1.0
    assert m.tool_names() == ["echo"]


def test_histogram_keeps_bounded_samples():
    """长跑进程内存有界：每个序列只保留最近 max_samples 个观测。"""
    m = MetricsRegistry(max_samples=10)
    for i in range(50):
        m.observe("llm_latency_ms", i)
    assert m.snapshot()["histograms"]["llm_latency_ms"]["count"] == 10
    assert m.values("llm_latency_ms")[0] == 40


def test_alerts_respect_min_samples_and_dedup():
    """样本不足不告警；同一告警在去重窗口内只报一次。"""
    m = MetricsRegistry()
    thresholds = AlertThresholds(min_samples=10, dedup_seconds=300.0)
    ev = AlertEvaluator(m, thresholds)

    for _ in range(4):
        m.incr("agent_runs_total", status="error")
        m.incr("agent_runs_total", status="ok")
    assert ev.evaluate(now=1000.0) == []          # 8 次运行 < min_samples(10)

    m.incr("agent_runs_total", status="error")
    m.incr("agent_runs_total", status="ok")
    first = ev.evaluate(now=1000.0)               # 10 次运行，错误率 50%
    assert [a.name for a in first] == ["error_rate"] and first[0].level == "critical"
    assert ev.evaluate(now=1100.0) == []          # 去重窗口内
    assert [a.name for a in ev.evaluate(now=1400.0)] == ["error_rate"]


def test_tool_failure_rate_alert():
    """单工具失败率超阈值触发告警（error / invalid / hook_blocked 计入失败）。"""
    m = MetricsRegistry()
    ev = AlertEvaluator(m, AlertThresholds(min_samples=10))
    for _ in range(7):
        m.incr("tool_calls_total", tool="flaky", status="ok")
    for _ in range(3):
        m.incr("tool_calls_total", tool="flaky", status="error")
    m.incr("tool_calls_total", tool="healthy", status="ok")

    alerts = ev.evaluate(now=0.0)
    assert [a.name for a in alerts] == ["tool_failure_rate:flaky"]
    assert alerts[0].value == pytest.approx(0.3)


def test_tool_failure_rate_boundary_is_not_alerted():
    """恰好等于阈值不告警（严格大于才触发），避免边界抖动导致误报。"""
    m = MetricsRegistry()
    ev = AlertEvaluator(m, AlertThresholds(min_samples=10))
    for _ in range(8):
        m.incr("tool_calls_total", tool="edge", status="ok")
    for _ in range(2):
        m.incr("tool_calls_total", tool="edge", status="invalid")
    assert ev.evaluate(now=0.0) == []


# ---------------------------------------------------------------- 引擎埋点
async def test_engine_records_metrics_and_spans(db, registry):
    """一次真实 run：产出 run/llm_call/tool_execute 三层 span 与对应指标。"""
    captured = []
    metrics = MetricsRegistry()
    provider = MockProvider([
        ModelResponse(content=None, tool_calls=[ToolCall(tool_name="echo", arguments={"text": "hi"}, call_id="c1")]),
        ModelResponse(content="完成"),
    ])
    engine, _store, _gate = make_engine(db, registry, provider)
    engine.metrics = metrics
    engine.tracer = Tracer([_ListSink(captured)])

    await collect(engine.run("用 echo 试一下"))

    names = [s.name for s in captured]
    assert names.count("run") == 1
    assert names.count("llm_call") == 2
    assert names.count("tool_execute") == 1
    run_span = next(s for s in captured if s.name == "run")
    assert run_span.attributes["outcome"] == "ok"
    assert all(s.trace_id == run_span.trace_id for s in captured)      # 同一条 trace
    assert all(s.parent_span_id == run_span.span_id for s in captured if s.name != "run")

    assert metrics.counter("agent_runs_total", status="ok") == 1
    assert metrics.counter("agent_turns_total") >= 2
    assert metrics.counter("llm_calls_total", status="ok") == 2
    assert metrics.counter("tool_calls_total", tool="echo", status="ok") == 1
    assert metrics.percentile("tool_latency_ms", 0.5, tool="echo") >= 0
    assert metrics.counter("approvals_requested_total") == 0


async def test_engine_records_suspension_and_approval_wait(db, registry):
    """审批挂起 -> 批准 -> 执行：run 结果标为 suspended，并记录审批等待时长。"""
    captured = []
    metrics = MetricsRegistry()
    provider = MockProvider([
        ModelResponse(content=None, tool_calls=[ToolCall(tool_name="ask_tool", arguments={"text": "x"}, call_id="c9")]),
        ModelResponse(content="已执行"),
    ])
    engine, _store, gate = make_engine(db, registry, provider)
    engine.metrics = metrics
    engine.tracer = Tracer([_ListSink(captured)])

    events = await collect(engine.run("需要审批的操作"))
    assert EventType.APPROVAL_REQUIRED in [e.type for e in events]
    assert next(s for s in captured if s.name == "run").attributes["outcome"] == "suspended"
    assert metrics.counter("approvals_requested_total", tool="ask_tool") == 1

    captured.clear()
    gate.approve("c9")
    await collect(engine.run(None))
    # 测试里两轮几乎无间隔，等待时长可能为 0ms，因此断言"记录了一次观测"而非数值大小
    waits = metrics.snapshot()["histograms"]["approval_wait_ms{tool=ask_tool}"]
    assert waits["count"] == 1
    tool_span = next(s for s in captured if s.name == "tool_execute")
    assert tool_span.attributes["decision"] == "allow"
    assert tool_span.attributes["approval_wait_ms"] >= 0


async def test_engine_marks_error_outcome(db, registry):
    """模型异常：run span 标 error，计入 error 指标，且不崩引擎。"""
    captured = []
    metrics = MetricsRegistry()
    engine, _store, _gate = make_engine(db, registry, MockProvider([], deltas={}))
    engine.provider.chat_stream = _failing_stream
    engine.metrics = metrics
    engine.tracer = Tracer([_ListSink(captured)])

    events = await collect(engine.run("会失败"))
    assert any(e.type == EventType.ERROR for e in events)
    assert metrics.counter("agent_runs_total", status="error") == 1
    assert next(s for s in captured if s.name == "llm_call").status == "error"


async def _failing_stream(messages, tools=None):
    raise ConnectionError("网络断了")
    yield  # pragma: no cover —— 使其成为异步生成器


def test_runtime_engine_trace_writer_default_none(db, registry):
    """未注入观测组件时引擎照常工作（span 走空 Tracer）。"""
    engine, _store, _gate = make_engine(db, registry, MockProvider([ModelResponse(content="hi")]))
    assert isinstance(engine.tracer, Tracer)
    assert engine.tracer.sinks == []


# ---------------------------------------------------------------- 落盘与退出
def test_jsonl_writer_records_events_and_spans(tmp_path):
    """同一份 JSONL 里事件与 span 靠 kind 区分，且字段完整。"""
    from mini_harness.observability.trace import JsonlTraceWriter

    writer = JsonlTraceWriter(tmp_path / "trace.jsonl")
    writer.write(ev(EventType.RUN_STARTED, "s1"))
    tracer = Tracer([writer])
    tracer.finish(tracer.start("run", session_id="s1", trace_id="t1"), outcome="ok")
    writer.flush()

    records = [json.loads(line) for line in (tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["kind"] for r in records] == ["event", "span"]
    assert records[0]["event"] == "run_started" and records[0]["session_id"] == "s1"
    assert records[1]["name"] == "run" and records[1]["trace_id"] == "t1"
    assert records[1]["duration_ms"] >= 0 and records[1]["attributes"]["outcome"] == "ok"


async def test_engine_flushes_trace_after_each_run(db, registry):
    """每次 run 收尾都刷盘：进程被杀时最多丢当轮，而不是丢整个缓冲区。"""
    engine, _store, _gate = make_engine(db, registry, MockProvider([ModelResponse(content="hi")]))
    flushes = []
    engine.trace_writer = _CountingWriter(flushes)

    await collect(engine.run("你好"))
    assert flushes == ["flush"]


def test_flush_observability_flushes_cached_engines(db, registry, monkeypatch):
    """进程退出路径：flush_observability 会逐个刷缓存中的引擎。"""
    import mini_harness.api.routes as routes

    engine, _store, _gate = make_engine(db, registry, MockProvider([ModelResponse(content="hi")]))
    calls = []
    engine.flush_trace = lambda: calls.append("flushed")
    monkeypatch.setattr(routes, "_engines", OrderedDict([("s1", engine)]))
    routes.flush_observability()
    assert calls == ["flushed"]


def test_lifespan_logs_startup_and_shutdown(capsys):
    """FastAPI lifespan：启动初始化日志，退出刷盘并留痕。"""
    from fastapi.testclient import TestClient

    import mini_harness.main as main_module

    setup_logging(level="INFO", fmt="json")
    with TestClient(main_module.app):
        pass
    out = capsys.readouterr().out
    assert "内核启动" in out
    assert "内核退出，观测缓冲已刷盘" in out


class _CountingWriter:
    """只记录 flush 次数的事件落点。"""

    def __init__(self, bucket):
        self.bucket = bucket

    def write(self, event):
        pass

    def flush(self):
        self.bucket.append("flush")
