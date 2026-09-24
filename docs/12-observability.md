# 12 - 接入日志 / Trace / 指标

## 何时需要

你需要把运行过程接到公司日志系统、需要留一份可回放的调用链用于排障，或者需要错误率/延迟/工具失败率这类指标来驱动告警时，读这一篇。

三者的分工：**日志**回答"刚才发生了什么"，**span**回答"这一次运行的耗时花在哪"，**指标**回答"整体趋势是否健康"。

## 接口约定

### 1. 结构化日志

```python
from mini_harness.observability.logging import (
    get_logger, setup_logging, set_trace_context, reset_trace_context,
    current_trace_id, current_session_id, log_format,
)

setup_logging(level=None, fmt=None) -> None
#   level：默认取环境变量 LOG_LEVEL，再退 "INFO"
#   fmt：  "json" | "text"，默认取 HARNESS_LOG_FORMAT，再退 "text"
#   只接管 mini_harness.* 命名空间，不动宿主的 root logger

get_logger(name) -> StructuredLogger
#   .debug/.info/.warning/.error(msg, **fields)
#   .exception(msg, **fields)   # 带堆栈
#   fields 会平铺进输出，trace_id / session_id 自动带上，无需逐层传参
```

`setup_logging(fmt="json")` 的输出（一行一条，可直接被采集器抓取）：

```json
{"ts": "2026-09-24T14:51:20", "level": "INFO", "logger": "mini_harness.runtime.engine", "msg": "工具执行完成", "trace_id": "59ba6f7f…", "session_id": "obs1", "tool": "lookup_order", "ok": true, "elapsed_ms": 0, "decision": "allow", "approval_wait_ms": null}
```

### 2. 调用链 Span

```python
from mini_harness.observability.spans import Tracer, Span, SpanSink, NullSpanSink, InMemorySpanSink, SPANS

tracer = Tracer([sink1, sink2])            # 无 sink = 纯 no-op
tracer.start(name, *, session_id="", trace_id="", **attributes) -> Span   # 父节点自动推导
tracer.finish(span, status="ok", **attributes) -> Span                    # 结束并投递
tracer.attach(span) -> Token               # 设为当前 span，其后的 span 成为它的子节点
tracer.detach(token) -> None
tracer.flush() -> None
tracer.current() -> Span | None

with tracer.span("llm_call") as sp:        # 语法糖：自动挂父节点、异常置 error、退出即投递
    ...
```

`SpanSink` 必须实现 `emit(span: Span) -> None`（不得抛异常；调用侧也兜了一层），`flush() -> None` 可选。引擎产出的 span 层级：`run`（`max_turns` / `outcome`）→ `llm_call`（`model` / `elapsed_ms` / `messages` / `tools`）与 `tool_execute`（`tool` / `decision` / `ok` / `elapsed_ms` / `approval_wait_ms`）。

### 3. 指标与告警

```python
from mini_harness.observability.metrics import (
    MetricsRegistry, METRICS, AlertEvaluator, AlertThresholds,
)

m = MetricsRegistry(max_samples=1000)
m.incr(name, value=1.0, **labels); m.observe(name, value, **labels); m.set_gauge(name, value)
m.counter(name, **labels) -> float; m.sum_counters(name) -> float
m.sum_counters_with_label(name, label, value) -> float
m.percentile(name, q, **labels) -> float; m.snapshot() -> dict; m.reset()

ev = AlertEvaluator(m, AlertThresholds(min_samples=20))
ev.evaluate(now=None) -> list[Alert]      # 已过去重（默认 5 分钟）
```

`METRICS` 是进程级默认注册表。引擎未显式传入 `metrics` 时写它，`GET /api/metrics` 也读它。

引擎内置指标：

| 指标 | 类型 | 标签 | 含义 |
|---|---|---|---|
| `agent_runs_total` | counter | `status` = ok / error / suspended / budget_exceeded / aborted | 运行结果分布 |
| `agent_turns_total` | counter | — | 轮次总数 |
| `llm_calls_total` | counter | `status` = ok / error / empty | 模型调用结果分布 |
| `llm_latency_ms` | histogram | — | 模型调用耗时 |
| `llm_tokens_total` | counter | `kind` = prompt / completion | token 用量 |
| `llm_cache_hits_total` | counter | — | 前缀缓存命中次数 |
| `tool_calls_total` | counter | `tool`、`status` = ok / error / invalid / denied / handoff / hook_blocked | 工具调用结果分布 |
| `tool_latency_ms` | histogram | `tool` | 工具执行耗时 |
| `approvals_requested_total` | counter | `tool` | 审批请求数 |
| `approvals_decided_total` | counter | `decision` = approved / denied | 审批决策数 |
| `approval_wait_ms` | histogram | `tool` | 从下发审批到实际执行的等待时长 |
| `compressions_total` | counter | — | 上下文压缩次数 |
| `hook_errors_total` | counter | — | 钩子执行失败次数 |
| `errors_total` | counter | `where` = engine | 引擎异常次数 |
| `engine_cache_size` | gauge | — | 引擎 LRU 缓存条目数 |

内置告警（阈值可用环境变量覆盖）：

| 告警 | 默认阈值 | 级别 |
|---|---|---|
| `error_rate` | > 5% / > 1% | critical / warning |
| `llm_latency_p99` | > 5000ms | warning |
| `tool_failure_rate:<工具名>` | > 20% | warning |

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `LOG_LEVEL` | `INFO` | 内核日志级别 |
| `HARNESS_LOG_FORMAT` | `text` | `json` 为生产结构化输出 |
| `HARNESS_TRACE_PATH` | 未设置 | 设置后把事件与 span 落成 JSONL |
| `HARNESS_ALERT_ERROR_RATE_CRITICAL` | `0.05` | 错误率 critical 阈值 |
| `HARNESS_ALERT_ERROR_RATE_WARNING` | `0.01` | 错误率 warning 阈值 |
| `HARNESS_ALERT_LLM_LATENCY_P99_MS` | `5000` | 模型调用 P99 延迟阈值 |
| `HARNESS_ALERT_TOOL_FAILURE_RATE` | `0.20` | 单工具失败率阈值 |
| `HARNESS_ALERT_MIN_SAMPLES` | `20` | 样本数不足时不告警 |

## 完整示例

把 span 投递到你自己的观测系统（示例：按 OTLP 风格打点到本机 HTTP 端点），并顺手记录一个业务指标：

```python
"""myproject/observability.py —— 自定义 SpanSink + 业务指标埋点。"""

from __future__ import annotations

import json
import urllib.request

from mini_harness.core.hooks import HarnessHooks
from mini_harness.observability.metrics import METRICS
from mini_harness.observability.spans import Span, SpanSink


class HttpSpanSink(SpanSink):
    """把 span 批量 POST 到采集端点；网络故障只丢观测数据，不影响对话。"""

    def __init__(self, endpoint: str, batch_size: int = 50) -> None:
        self.endpoint = endpoint
        self.batch_size = batch_size
        self._buffer: list[dict] = []

    def emit(self, span: Span) -> None:
        self._buffer.append(span.to_dict())
        if len(self._buffer) >= self.batch_size:
            self.flush()

    def flush(self) -> None:
        if not self._buffer:
            return
        batch, self._buffer = self._buffer, []
        payload = json.dumps({"spans": batch}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.endpoint, data=payload,
                                     headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=3).read()
        except Exception:      # noqa: BLE001 —— 观测链路故障不得影响业务
            pass


class BusinessMetrics(HarnessHooks):
    """用钩子采集领域指标：统计"涉及退款的建单尝试"。"""

    async def before_tool_execute(self, name: str, args: dict):
        if name == "create_ticket" and "退款" in str(args.get("detail", "")):
            METRICS.incr("business_refund_ticket_attempt_total", tool=name)
        return True, ""
```

接线（`engine.tracer` 可整体替换；import 见上面的接口约定）：

```python
engine = build_engine("demo", load_project_config("projects/customer_service/config.yaml"))
engine.tracer = Tracer([SPANS, HttpSpanSink("http://127.0.0.1:4318/v1/spans")])
```

## 接入步骤

**1. 打开结构化日志**：部署环境设 `HARNESS_LOG_FORMAT=json` 与 `LOG_LEVEL=INFO`，日志收集器按字段 `trace_id` 聚合同一次运行的全部日志。

**2. 打开调用链落盘**（可选）：设 `HARNESS_TRACE_PATH=/var/log/harness/trace.jsonl`。文件里两种记录靠 `kind` 区分：

```bash
grep '"kind": "span"' /var/log/harness/trace.jsonl | tail -n 5     # 最近的 span
grep '"kind": "event"' /var/log/harness/trace.jsonl | wc -l        # 事件总数
```

**3. 起监控轮询**：`GET /api/metrics` 返回 `{counters, histograms, gauges, alerts}`；告警是**拉取式**的，每次查询按阈值评估并按 5 分钟去重，轮询间隔建议 15–60 秒。

```bash
curl -s http://127.0.0.1:8765/api/metrics | python -m json.tool
```

**4. 查看最近的调用链**：`GET /api/traces?session_id=<会话>&limit=50` 返回进程内环形缓冲里的最近 span（默认保留 500 条，按时间倒序）。

**5. 自定义 sink**：按其 `emit` / `flush` 实现即可，在组装处注入 `Tracer([SPANS, your_sink])`；要看别的指标就在 Hook 里调 `METRICS.incr/observe`。

## 常见错误

- **日志什么都没输出** → 宿主应用没有调用 `setup_logging()`（内核不自作主张配置 root logger）。已在 `uvicorn mini_harness.main:app` 启动时自动调用；脚本方式接入需自己调一次。
- **日志里 `trace_id` 是空串** → 该日志不在 `engine.run()` 的执行上下文里（例如在 FastAPI 请求处理函数、定时任务中直接打日志）。这类位置需要自行 `set_trace_context(trace_id, session_id)`，用完 `reset_trace_context(tokens)` 还原。
- **`GET /api/traces` 返回空数组** → 三种可能：这个进程还没跑过一轮对话；`session_id` 传错（该会话确实没有 span）；进程刚重启（缓冲是内存态，不持久化，要落盘请设 `HARNESS_TRACE_PATH`）。
- **span 的 `parent_span_id` 全是 `null`** → 自建 span 时没有 `tracer.attach(root_span)`，父节点推导不到。引擎内部已自动处理；自建时用 `with tracer.span(...)` 或成对调用 `attach` / `detach`。
- **自定义 sink 拖慢了对话** → `emit` 里做了同步网络请求。改为写入本地队列、由后台线程批量发送；`emit` 必须快速返回。
- **告警一直不触发** → 先看样本数：`runs < HARNESS_ALERT_MIN_SAMPLES`（默认 20）时不评估；再看是否在 5 分钟去重窗口内；最后确认指标名/标签与上表一致（`tool_calls_total` 需要同时有 `tool` 与 `status` 标签）。
