# 契约 - 稳定接口清单

这一篇是二开者与内核之间的**唯一承诺**。以下接口一旦发布即视为冻结：改名、改签名、改返回值都算破坏性变更，必须记入 [appendix/migration.md](appendix/migration.md)。

稳定性分三级：

| 级别 | 含义 | 覆盖范围 |
|---|---|---|
| **冻结** | 破坏性变更需走迁移说明 | @tool、PermissionLevel、HarnessHooks、EventType、ProjectConfig、BaseModelProvider、ToolRegistry 公开方法 |
| **半稳定** | 可增加成员，不删改既有成员 | TraceWriter、EvalRunner、@register_* 装饰器 |
| **不承诺** | 随时可改 | 所有 `_` 前缀的私有成员、SQLite 表结构 |

---

## 1. 工具装饰器 `@tool`

位置：`mini_harness.sdk.decorator.tool`

```python
def tool(
    name: str,
    description: str,
    *,
    permission: PermissionLevel = PermissionLevel.ASK_FIRST,
    parameters: Optional[dict] = None,   # 手动 JSON Schema，优先于签名推导
    path_arg: Optional[str] = None,      # 哪个参数是文件路径（触发边界校验）
) -> Callable[[Callable], Callable]: ...
```

被装饰函数的约定：

| 约定 | 说明 |
|---|---|
| 参数类型 | 只支持 `str` / `int` / `float` / `bool`；更复杂的结构用 `parameters` 手写 schema |
| 同步 | 必须是同步函数；内核在线程池中执行并施加 `tool_timeout` 超时 |
| 返回值 | 会被 `str()` 化后写入账本；不要返回不可序列化对象 |
| 必填参数 | 无默认值的参数自动进入 schema 的 `required` |
| `path_arg` | 指定后，该参数会按 `permission.allowed_paths` 做边界校验，越界即拒绝执行 |
| `description` | 模型据此决定是否调用，写清"什么时候用、参数取值"比写实现细节更重要 |

服务端校验顺序：Schema 校验（缺参 / 未知参数 / 类型）→ 路径边界 → Hook 前置 → 权限裁决 → 执行。
任一环节失败都会把**可行动的失败原因**写入账本回灌给模型。

---

## 2. 权限级别 `PermissionLevel`

位置：`mini_harness.tools.levels`

| 枚举值 | 字符串 | 决策 | 典型场景 |
|---|---|---|---|
| `FULL_TRUST` | `full_trust` | 直接执行 | 只读查询：查订单、查政策、检索记忆 |
| `AUTO_WITH_NOTIFICATION` | `auto_with_notification` | 直接执行 + 下发改动通知 | 幂等轻量操作：取当前时间、计算 |
| `ASK_FIRST` | `ask_first` | 逐个调用等人工批准（默认值） | 有副作用的写：写记忆、建工单 |
| `APPROVE_ALWAYS` | `approve_always` | 首次等批准，记住该类调用后自动放行 | 高频重复的同型写操作 |
| `MANUAL_ONLY` | `manual_only` | 不执行、不拒绝，转人工 | 需要人现场完成的操作 |

解析顺序：**数据库中的运行时覆盖 > 工具声明的默认值**。运行时覆盖通过 `PUT /api/tools/{name}/permission` 写入并持久化，对所有会话立即生效。

`APPROVE_ALWAYS` 的"记住同类"以「工具名 + 参数键集合与类型」为指纹，存在进程内存中；进程重启或引擎缓存淘汰后需要重新批准。

---

## 3. 钩子 `HarnessHooks`

位置：`mini_harness.core.hooks`

```python
class HarnessHooks:
    async def before_llm_call(
        self, messages: list[dict], tools: Optional[list[dict]]
    ) -> tuple[list[dict], Optional[list[dict]]]: return messages, tools

    async def after_llm_call(self, response: Any) -> Any: return response

    async def before_tool_execute(self, name: str, args: dict) -> tuple[bool, str]: return True, ""

    async def after_tool_execute(self, name: str, args: dict, result: str) -> str: return result

    async def on_error(self, exc: BaseException) -> None: return None
```

| 点位 | 触发时机 | 返回值约定 |
|---|---|---|
| `before_llm_call` | 上下文组装完成、请求模型之前 | 返回改写后的 `(messages, tools)`；**只影响本次请求，不回写账本** |
| `after_llm_call` | 模型响应完成、写入账本之前 | 返回改写后的响应对象；返回 `None` 视为不改 |
| `before_tool_execute` | 校验通过、**权限裁决之前** | 返回 `(allowed, reason)`；`allowed=False` 时拦截，原因写入账本 |
| `after_tool_execute` | 工具执行完成、写入账本之前 | 返回清洗后的结果字符串；清洗后的内容才会回灌给模型 |
| `on_error` | 引擎内部错误路径 | 无返回值，仅观测 |

其他约定：

- 方法写成 `async` 或同步函数都可以，两种都会被正确调用。
- 钩子自身抛异常**不会中断运行**：引擎会把它转成一条带 `hook_error` 的事件后继续。
- 同一点位按配置顺序链式执行，前一个钩子的输出是后一个钩子的输入。
- `before_tool_execute` 排在权限裁决之前，因此"危险调用拦截"可以避免弹出无意义的审批框。

---

## 4. 事件类型 `EventType`

位置：`mini_harness.core.events`。事件是引擎与调用方之间唯一的输出契约，`Event` 有三个字段：`type`、`data`、`session_id`。

| 事件 | data 字段 | 说明 |
|---|---|---|
| `run_started` | — | 一次运行开始 |
| `turn_started` | `turn: int` | 第几轮（从 1 开始） |
| `delta` | `text: str` | 模型增量文本；也用于下发 `[自动执行] <工具名>` 这类通知行 |
| `tool_executed` | `tool`、`call_id`、`ok`、`result`、`elapsed_ms` | 工具执行结果；失败时附 `invalid` / `handoff` / `superseded` / `hook_blocked` 之一为 `true`，被钩子拦截时另有 `reason` |
| `approval_required` | `call_id`、`tool_name`、`arguments` | 需要人工审批；**引擎在此挂起**，直到批准后 `resume()` |
| `turn_finished` | `content`、`usage` | 该轮产出最终回答 |
| `context_compressed` | `summarized_messages`、`kept_messages`、`summary_tokens` | 上下文触发压缩 |
| `budget_exceeded` | `max_turns`、`usage` | 轮次预算耗尽 |
| `run_finished` | `usage` | 一次运行结束（正常、挂起、报错都会下发） |
| `error` | `message`；钩子异常时附 `hook_error: true` | 错误 |
| `memory_saved` | — | 保留类型，当前版本不由引擎产出 |
| `tool_call_started` | — | 保留类型，当前版本不由引擎产出 |

`usage` 字段结构：`budget`、`hard_cap`、`estimated_context_tokens`、`prompt_tokens`、`completion_tokens`、`llm_calls`、`tool_calls`、`compressions`、`cache_hits`。

---

## 5. 项目配置 `ProjectConfig`

位置：`mini_harness.core.config`。加载后所有字段都是具体值：YAML 写 `null` 或省略时，回填环境变量层的值。

| 字段 | 类型 | 默认值 / 回填来源 |
|---|---|---|
| `name` | `str` | 未写时取项目配置文件名（不含扩展名） |
| `agent_name` | `str` | `Agent`；注入提示词占位符 `{{AGENT_NAME}}` |
| `language` | `str` | `中文`；注入 `{{LANGUAGE}}` |
| `system_prompt_path` | `str \| None` | `null`（只用框架级提示词）；相对路径按配置文件所在目录解析 |
| `provider.type` | `str` | `openai` |
| `provider.model` | `str` | `LLM_MODEL` / `OPENAI_MODEL` |
| `provider.api_key` | `str` | `LLM_API_KEY` / `OPENAI_API_KEY` |
| `provider.base_url` | `str` | `LLM_BASE_URL` / `OPENAI_BASE_URL` |
| `provider.temperature` | `float` | `0.0` |
| `provider.max_retries` | `int` | `2` |
| `provider.request_timeout` | `float` | `120.0` |
| `memory.type` | `str` | `sqlite` |
| `memory.path` | `str` | `MEMORY_DB_PATH`（`data/harness.db`，相对进程工作目录） |
| `memory.longterm_top_k` | `int` | `LONGTERM_TOP_K`（`3`） |
| `budget.context_token_budget` | `int` | `CONTEXT_TOKEN_BUDGET`（`24000`） |
| `budget.reserve_output_tokens` | `int` | `RESERVE_OUTPUT_TOKENS`（`4096`） |
| `budget.compress_threshold` | `float` | `COMPRESS_THRESHOLD`（`0.8`） |
| `compressor.keep_recent` | `int` | `KEEP_RECENT_MESSAGES`（`8`） |
| `assembler` | `dict` | `{}`（预留） |
| `tools.builtin` | `list[str]` | `[]`；内置工具**工具名**白名单 |
| `tools.plugins_dir` | `str \| None` | `null`；项目工具目录 |
| `permission.type` | `str` | `interactive` |
| `permission.allowed_paths` | `list[str]` | `[]`；可用 `HARNESS_ALLOWED_PATHS` 覆盖 |
| `permission.approval_store` | `bool` | `true` |
| `hooks` | `list[str]` | `[]`；钩子类的导入路径 |
| `max_turns` | `int` | `MAX_TURNS`（`10`） |
| `tool_timeout` | `float` | `EXECUTION_TIMEOUT_SECONDS`（`300`） |

完整字段说明与写法见 [06-configure.md](06-configure.md)，默认值文件是 `configs/default.yaml`。

---

## 6. 模型供应商 `BaseModelProvider`

位置：`mini_harness.models.provider`

```python
class BaseModelProvider(abc.ABC):
    @abc.abstractmethod
    async def chat(self, messages: list[dict], tools: Optional[list] = None) -> ModelResponse: ...

    @abc.abstractmethod
    async def chat_stream(self, messages: list[dict], tools: Optional[list] = None): ...
    # 逐块产出：{"delta": str} ... 结束时产出 {"response": ModelResponse}

    async def chat_text(self, messages: list[dict]) -> str: ...   # 已有默认实现，无需重写
```

`ModelResponse` 字段：`content: str | None`、`tool_calls: list[ToolCall] | None`、`finish_reason: str`、`usage: dict`。
`ToolCall` 字段：`tool_name: str`、`arguments: dict`、`call_id: str`。

实现时必须做到的兜底：

- `call_id` 缺失时自行生成（否则工具结果无法回填配对）；
- 工具参数 JSON 解析失败时退化为 `{}`（不要抛异常中断运行）；
- `usage` 能拿到就填，拿不到留空字典。

---

## 7. 附带契约

以下接口二开时也会用到，同样属于承诺范围。

**工具装载**（`mini_harness.tools.loader.ToolLoader`）

```python
ToolLoader(registry, longterm=None, session_id="")
  .load_builtin(names: list[str]) -> None     # 名字不存在直接报错，不静默跳过
  .load_from_dir(path: str | Path) -> None    # 扫描目录下所有 .py，注册其中的 @tool 函数
  .load_all(cfg) -> None                      # 读 cfg.tools 的 builtin + plugins_dir
```

扫描规则：跳过以 `_` 开头的文件（`__init__.py` 不参与）；目录不存在视为该项目没有专属工具；文件导入失败直接报错。

**组件注册**（`mini_harness.core.registry`）

```python
@register_provider("claude")   # 模型供应商
@register_memory("redis")      # 记忆后端
@register_gate("rule_based")   # 权限门控

ComponentRegistry.build("provider", "claude", **kwargs) -> Any
ComponentRegistry.available("provider") -> list[str]
```

**Trace 落盘**（`mini_harness.observability.trace.TraceWriter`）

```python
def write(self, event: Event) -> None   # 只做序列化与缓冲，不阻塞、不抛异常
def flush(self) -> None                 # 刷盘
```

**可观测性**（`mini_harness.observability`）

```python
setup_logging(level=None, fmt=None) -> None        # 只接管 mini_harness.* 命名空间
get_logger(name) -> StructuredLogger               # .debug/.info/.warning/.error/.exception(msg, **fields)
set_trace_context(trace_id, session_id) -> tokens  # 与 reset_trace_context(tokens) 成对使用

Tracer(sinks: list[SpanSink]) -> Tracer
  .start(name, *, session_id="", trace_id="", **attributes) -> Span
  .finish(span, status="ok", **attributes) -> Span
  .attach(span) -> Token / .detach(token) / .flush() / .span(name, ...) 上下文管理器
SpanSink.emit(span: Span) -> None / .flush() -> None

MetricsRegistry(max_samples=1000)
  .incr(name, value=1.0, **labels) / .observe(name, value, **labels) / .set_gauge(name, value)
  .counter(name, **labels) / .sum_counters(name) / .sum_counters_with_label(name, label, value)
  .percentile(name, q, **labels) / .snapshot() / .reset()
AlertEvaluator(registry, thresholds=None).evaluate(now=None) -> list[Alert]
```

`RuntimeEngine` 的观测注入点为可选参数 `tracer` / `metrics`，另有公开方法 `flush_trace()`。
指标清单、阈值与启用方式见 [12-observability.md](12-observability.md)。

**评估**（`mini_harness.eval.runner`）：`TestCase` / `EvalResult` / `EvalRunner` 的字段与方法签名见 [13-evaluation.md](13-evaluation.md)。
