# 版本升级指引

每次新增或修改扩展点接口，都必须在这里追加一条记录。格式：版本 + 日期 + 变更分类 + 迁移做法。

版本号的唯一来源是 `pyproject.toml`，`mini_harness.__version__` 与之保持同步，`GET /api/health` 返回同一个值。

## v0.5.0 (2026-09-24)

主题：可观测性落地 —— 结构化日志、调用链 span、指标与阈值告警。

### 破坏性变更

无。本次全部是新增能力，`RuntimeEngine.__init__` 只新增可选参数。

### 新增（不影响既有代码）

- `observability/logging.py`：`setup_logging` / `get_logger` / `StructuredLogger` /
  `set_trace_context` / `reset_trace_context` / `current_trace_id` / `log_format`
- `observability/spans.py`：`Tracer` / `Span` / `SpanSink` / `NullSpanSink` / `InMemorySpanSink` / `SPANS`
- `observability/metrics.py`：`MetricsRegistry` / `METRICS` / `AlertEvaluator` / `AlertThresholds` / `Alert`
- `RuntimeEngine.__init__` 新增可选参数 `tracer`、`metrics`；新增公开方法 `flush_trace()`
- `GET /api/metrics`（指标快照 + 告警）、`GET /api/traces`（最近 span，环形缓冲）
- 环境变量：`HARNESS_LOG_FORMAT`、`HARNESS_ALERT_ERROR_RATE_CRITICAL`、
  `HARNESS_ALERT_ERROR_RATE_WARNING`、`HARNESS_ALERT_LLM_LATENCY_P99_MS`、
  `HARNESS_ALERT_TOOL_FAILURE_RATE`、`HARNESS_ALERT_MIN_SAMPLES`
- `main.py` 改用 FastAPI lifespan：启动初始化日志，退出刷观测缓冲

### 行为变化

- **内核开始输出日志**（此前内核零日志）。默认 `text` 格式、级别取 `LOG_LEVEL`；
  生产建议 `HARNESS_LOG_FORMAT=json`。日志只接管 `mini_harness.*`，不影响宿主 root logger。
- `engine.run()` 每次收尾会 `flush()` 事件与 span 缓冲（此前只在引擎被淘汰时刷盘）。
- `JsonlTraceWriter` 现在同时是 `SpanSink`；JSONL 记录新增 `kind` 字段区分 `event` / `span`。
  `Event.to_trace_dict()` 本身未变，`kind` 由 writer 附加。
- 审批决策（批准/拒绝）新增指标 `approvals_decided_total`，同时打一条结构化日志。

---

## v0.4.0 (2026-09-23)

主题：框架化改造 —— 内核与领域分离，扩展点成型。

### 破坏性变更

| 接口 | 变更前 | 变更后 | 迁移做法 |
|---|---|---|---|
| `api.routes.build_engine` | `build_engine(session_id)` | `build_engine(session_id, cfg)` | 自行加载配置：`cfg = load_project_config("projects/<name>/config.yaml")` |
| `RuntimeEngine.__init__` 的 `system_prompt` | 有默认值（内核内置中文提示词） | **必填**，无默认值 | 传 `load_system_prompt(cfg)` 的返回值，或自备字符串 |
| `RuntimeEngine.__init__` 参数顺序 | `… assembler, longterm, system_prompt, max_turns, tool_timeout` | `… assembler, system_prompt, longterm, max_turns, tool_timeout` | 用关键字参数调用即不受影响 |
| 内置工具装配 | `tools.builtin.build_builtin_tools(longterm, session_id)` | 已移除，改用 `ToolLoader(...).load_all(cfg)` | 见 [docs/07-custom-tools.md](../07-custom-tools.md) |
| 内置工具模块路径 | `mini_harness/tools/builtin.py`（单文件） | `mini_harness/tools/builtin/{system,memory}.py`（包） | 若按文件路径引用过，改为包导入 |
| 引擎实例容器 | 进程内常驻，实例永久存活 | 容量 16 的 LRU，未命中即按 `session_id` 从数据库重建 | 依赖"引擎实例长期存活"的自定义逻辑要改为无状态；审批"记住同类"（`approve_always`）会随淘汰失效 |

### 新增（不影响既有代码）

- `core/hooks.py`：`HarnessHooks` / `HookChain` / `load_hooks`，五个生命周期点位
- `core/config.py`：`ProjectConfig` / `load_project_config`
- `core/registry.py`：`@register_provider` / `@register_memory` / `@register_gate` / `ComponentRegistry`
- `sdk/decorator.py`：`@tool` 装饰器 —— 工具作者的唯一契约
- `tools/loader.py`：`ToolLoader` / `ToolContext`
- `context/prompt_loader.py`：`load_system_prompt`（框架级 + 项目级提示词拼接）
- `observability/trace.py`：`TraceWriter` / `NullTraceWriter` / `JsonlTraceWriter`
- `eval/runner.py`：`TestCase` / `EvalResult` / `EvalRunner`（接口占位）
- `Event.to_trace_dict()`
- `RuntimeEngine.__init__` 新增可选参数 `hook_chain`、`trace_writer`
- `GET /api/health` 新增 `project`、`agent_name` 字段
- 配置项：`permission.type`

### 行为变化

- 引入 `projects/<name>/config.yaml` 与 `configs/default.yaml` 两层配置。YAML 里写 `null` 或省略的字段会回填环境变量层，因此既有 `.env` 仍然生效。
- `before_tool_execute` 在权限裁决**之前**触发：被钩子拦截的调用不会出现在审批队列里。
- 启用 Trace 后事件在 `run()` 出口统一落盘，`resume()` 同样覆盖。

### 未变更（向后兼容）

- `ToolRegistry`、`PermissionGate`、`SessionStore`、`ContextCompressor`、`Budget`、`ContextAssembler` 的公开签名未变。
- 全部 `/api/*` 路由与 SSE 事件语义未变；新增字段均为附加字段。

---

## 追加记录的模板

```markdown
## vX.Y.Z (YYYY-MM-DD)

主题：<一句话说明这次改了什么>

### 破坏性变更

| 接口 | 变更前 | 变更后 | 迁移做法 |
|---|---|---|---|
| | | | |

### 新增（不影响既有代码）

- 

### 行为变化

- 
```

> TODO: 待完善（v0.1 ~ v0.3 的历史变更未回溯记录）
