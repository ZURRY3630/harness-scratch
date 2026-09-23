# 12 - 接入日志 / Trace / 指标

计划内容：三类观测手段的分工（应用日志 / 事件 Trace / 指标）、`TraceWriter` 接口与 JSONL 落盘、
把事件投递到 OpenTelemetry、Loki、ClickHouse 的实现示例、启用方式（`HARNESS_TRACE_PATH`）、
采样与容量控制、用 Hook 采集自定义指标。

契约摘要见 [contracts.md](contracts.md) 第 7 节；事件字段见 [appendix/event-reference.md](appendix/event-reference.md)。

> TODO: 待完善（第三轮补齐）
