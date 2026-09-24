# DOC: docs/12-observability.md
"""指标与告警：计数器 / 直方图（P50/P95/P99）/ 阈值告警（指南 11.1 / 11.5）。

设计取舍：
- **零依赖**：进程内聚合，不引入 prometheus_client 等第三方库；
  需要远端采集时读 `GET /api/metrics` 的 JSON 快照或 `snapshot()` 自行上报。
- **有界采样**：每个直方图序列只保留最近 `max_samples` 个观测值，避免长跑进程内存增长；
  因此 P99 是**滑动窗口**分位数，不是全生命周期精确值。
- **标签基数受控**：标签只允许来自工具名、状态枚举这类有限集合，不做自由文本打标。

告警阈值取自指南 11.5：错误率 >5% critical、>1% warning，模型调用 P99 延迟 >5s warning，
单工具失败率 >20% warning；样本不足（`min_samples`）时不告警，5 分钟内同一告警只报一次。
"""

from __future__ import annotations

import math
import os
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Optional

_DEFAULT_MAX_SAMPLES = 1000
_ALERT_DEDUP_SECONDS = 300.0

# 需要计算分位数的指标序列（其余只记计数）
_HISTOGRAM_METRICS = ("llm_latency_ms", "tool_latency_ms", "approval_wait_ms")

# 工具调用状态口径：失败 = 引擎/工具报错、参数校验未通过、被钩子拦截
# （denied / handoff 是权限决策的结果，不计入失败率，否则策略收紧会被误判为故障）
_TOOL_FAILURE_STATUSES = ("error", "invalid", "hook_blocked")


def _key(name: str, labels: dict[str, Any]) -> str:
    if not labels:
        return name
    parts = ",".join(f"{k}={labels[k]}" for k in sorted(labels))
    return f"{name}{{{parts}}}"


def _percentile(sorted_values: list[float], q: float) -> float:
    """线性插值分位数（q 取 0~1）；空列表返回 0。"""
    n = len(sorted_values)
    if n == 0:
        return 0.0
    if n == 1:
        return sorted_values[0]
    pos = (n - 1) * q
    low, high = math.floor(pos), math.ceil(pos)
    if low == high:
        return sorted_values[int(pos)]
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (pos - low)


class MetricsRegistry:
    """进程内指标注册表：计数器 + 直方图 + 即时量（gauge）。"""

    def __init__(self, max_samples: int = _DEFAULT_MAX_SAMPLES) -> None:
        self.max_samples = max_samples
        self._counters: dict[str, float] = {}
        self._histograms: dict[str, Deque[float]] = {}
        self._gauges: dict[str, float] = {}
        self._tool_names: set[str] = set()

    # ----- 写入 -----
    def incr(self, name: str, value: float = 1.0, **labels: Any) -> None:
        """计数器自增（默认 +1）。"""
        key = _key(name, labels)
        self._counters[key] = self._counters.get(key, 0.0) + value
        self._index_labels(labels)

    def observe(self, name: str, value: float, **labels: Any) -> None:
        """记录一个观测量（延迟、耗时等），进直方图。"""
        key = _key(name, labels)
        series = self._histograms.get(key)
        if series is None:
            series = deque(maxlen=self.max_samples)
            self._histograms[key] = series
        series.append(float(value))
        self._index_labels(labels)

    def _index_labels(self, labels: dict[str, Any]) -> None:
        """登记出现过的工具名，供告警按工具聚合（避免从字符串键名反解）。"""
        tool = labels.get("tool")
        if isinstance(tool, str):
            self._tool_names.add(tool)

    def tool_names(self) -> list[str]:
        return sorted(self._tool_names)

    def set_gauge(self, name: str, value: float) -> None:
        """设置即时量（如当前缓存条目数）。"""
        self._gauges[name] = float(value)

    def reset(self) -> None:
        """清空全部指标（测试用）。"""
        self._counters.clear()
        self._histograms.clear()
        self._gauges.clear()
        self._tool_names.clear()

    # ----- 读取 -----
    def counter(self, name: str, **labels: Any) -> float:
        return self._counters.get(_key(name, labels), 0.0)

    def values(self, name: str, **labels: Any) -> list[float]:
        return list(self._histograms.get(_key(name, labels), ()))

    def percentile(self, name: str, q: float, **labels: Any) -> float:
        return _percentile(sorted(self.values(name, **labels)), q)

    def sum_counters(self, name: str) -> float:
        """同一指标名下所有标签序列求和（例：`tool_calls_total` 全部状态合计）。"""
        return sum(v for k, v in self._counters.items() if k == name or k.startswith(name + "{"))

    def sum_counters_with_label(self, name: str, label: str, value: str) -> float:
        """同一指标名下、某标签等于给定值的所有序列求和。

        例：`sum_counters_with_label("tool_calls_total", "tool", "echo")` —— 不关心状态，
        把 echo 的 ok / error / denied 等全部加起来。
        """
        target = f"{label}={value}"
        total = 0.0
        for key, v in self._counters.items():
            if not key.startswith(name + "{"):
                continue
            labels = key[key.index("{") + 1: -1].split(",")
            if target in labels:
                total += v
        return total

    def rate(self, numerator: str, denominator: str) -> float:
        """两个计数器之和的比值；分母为 0 时返回 0。"""
        den = self.sum_counters(denominator)
        return self.sum_counters(numerator) / den if den else 0.0

    def histogram_stats(self, key: str) -> dict[str, float]:
        series = sorted(self._histograms.get(key, ()))
        if not series:
            return {"count": 0, "avg": 0.0, "p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0}
        return {
            "count": len(series),
            "avg": round(sum(series) / len(series), 2),
            "p50": round(_percentile(series, 0.50), 2),
            "p95": round(_percentile(series, 0.95), 2),
            "p99": round(_percentile(series, 0.99), 2),
            "max": round(series[-1], 2),
        }

    def snapshot(self) -> dict[str, Any]:
        """可直接 JSON 序列化的快照（`GET /api/metrics` 的载荷）。"""
        return {
            "counters": {k: round(v, 3) for k, v in sorted(self._counters.items())},
            "histograms": {k: self.histogram_stats(k) for k in sorted(self._histograms)},
            "gauges": {k: round(v, 3) for k, v in sorted(self._gauges.items())},
        }


# ---------------------------------------------------------------- 告警
@dataclass
class AlertThresholds:
    """告警阈值（可用环境变量覆盖，见 `from_env`）。"""

    error_rate_critical: float = 0.05
    error_rate_warning: float = 0.01
    llm_latency_p99_ms: float = 5000.0
    tool_failure_rate_warning: float = 0.20
    min_samples: int = 20
    dedup_seconds: float = _ALERT_DEDUP_SECONDS

    @classmethod
    def from_env(cls) -> "AlertThresholds":
        """从环境变量读取覆盖值；未设置的项保持默认。"""
        def _f(key: str, default: float) -> float:
            raw = (os.getenv(key) or "").strip()
            try:
                return float(raw) if raw else default
            except ValueError:
                return default

        return cls(
            error_rate_critical=_f("HARNESS_ALERT_ERROR_RATE_CRITICAL", cls.error_rate_critical),
            error_rate_warning=_f("HARNESS_ALERT_ERROR_RATE_WARNING", cls.error_rate_warning),
            llm_latency_p99_ms=_f("HARNESS_ALERT_LLM_LATENCY_P99_MS", cls.llm_latency_p99_ms),
            tool_failure_rate_warning=_f("HARNESS_ALERT_TOOL_FAILURE_RATE", cls.tool_failure_rate_warning),
            min_samples=int(_f("HARNESS_ALERT_MIN_SAMPLES", cls.min_samples)),
        )


@dataclass
class Alert:
    """一条告警。`level` 取 `critical` / `warning`。"""

    level: str
    name: str
    message: str
    value: float
    threshold: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "name": self.name,
            "message": self.message,
            "value": round(self.value, 4),
            "threshold": self.threshold,
        }


class AlertEvaluator:
    """按阈值评估告警；同一告警在去重窗口内只报一次（指南 11.5 去重 5 分钟）。"""

    def __init__(self, registry: MetricsRegistry, thresholds: Optional[AlertThresholds] = None) -> None:
        self.registry = registry
        self.thresholds = thresholds or AlertThresholds.from_env()
        self._last_fired: dict[str, float] = {}

    def evaluate(self, now: Optional[float] = None) -> list[Alert]:
        """返回本次应该触发的告警（已过去重）。"""
        now = time.time() if now is None else now
        th = self.thresholds
        r = self.registry
        candidates: list[Alert] = []

        runs = r.sum_counters("agent_runs_total")
        if runs >= th.min_samples:
            error_rate = r.counter("agent_runs_total", status="error") / runs
            if error_rate >= th.error_rate_critical:
                candidates.append(Alert("critical", "error_rate",
                                        f"错误率 {error_rate:.1%} 超过 {th.error_rate_critical:.1%}",
                                        error_rate, th.error_rate_critical))
            elif error_rate >= th.error_rate_warning:
                candidates.append(Alert("warning", "error_rate",
                                        f"错误率 {error_rate:.1%} 超过 {th.error_rate_warning:.1%}",
                                        error_rate, th.error_rate_warning))

        llm_calls = r.sum_counters("llm_calls_total")
        if llm_calls >= th.min_samples:
            p99 = r.percentile("llm_latency_ms", 0.99)
            if p99 > th.llm_latency_p99_ms:
                candidates.append(Alert("warning", "llm_latency_p99",
                                        f"模型调用 P99 延迟 {p99:.0f}ms 超过 {th.llm_latency_p99_ms:.0f}ms",
                                        p99, th.llm_latency_p99_ms))

        for tool in r.tool_names():
            total = r.sum_counters_with_label("tool_calls_total", "tool", tool)
            if total < th.min_samples:
                continue
            failures = sum(r.counter("tool_calls_total", tool=tool, status=s)
                           for s in _TOOL_FAILURE_STATUSES)
            rate = failures / total
            if rate > th.tool_failure_rate_warning:
                candidates.append(Alert("warning", f"tool_failure_rate:{tool}",
                                        f"工具 {tool} 失败率 {rate:.1%} 超过 {th.tool_failure_rate_warning:.1%}",
                                        rate, th.tool_failure_rate_warning))

        return self._dedup(candidates, now)

    # ----- 内部 -----
    def _dedup(self, alerts: list[Alert], now: float) -> list[Alert]:
        out: list[Alert] = []
        for alert in alerts:
            last = self._last_fired.get(alert.name)
            if last is not None and now - last < self.thresholds.dedup_seconds:
                continue
            self._last_fired[alert.name] = now
            out.append(alert)
        return out


# 进程级默认注册表：引擎未显式注入时使用它，`GET /api/metrics` 也读它
METRICS = MetricsRegistry()
