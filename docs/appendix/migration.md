# 版本升级指引

每次新增或修改扩展点接口，都必须在这里追加一条记录。格式：版本 + 日期 + 变更分类 + 迁移做法。

版本号的唯一来源是 `pyproject.toml`，`mini_harness.__version__` 与之保持同步，`GET /api/health` 返回同一个值。

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
