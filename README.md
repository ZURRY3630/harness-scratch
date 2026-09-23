# MiniHarness v0.3

最小但生产向的 Agent Harness：FastAPI + Vue 3 (Vite/npm) 前后端分离 + 上下文压缩 / 长期记忆 / Prompt 缓存 / Token 预算。

参考：《智能体 Harness 工程指南》 https://yeasy.gitbook.io/harness_engineering_guide

## 运行

```bash
# 1) 后端依赖（项目根目录）
uv sync            # 或 pip install -e ".[dev]"

# 2) 前端依赖 + 构建（frontend/ 目录）
npm install        # 国内网络慢可用: npm install --registry=https://registry.npmmirror.com
npm run build      # 产物输出到 frontend/dist/

# 3) 配置 .env（已存在则跳过；至少需要 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL）

# 4) 启动（默认加载 projects/default/config.yaml）
python -m uvicorn mini_harness.main:app --app-dir backend --port 8765
# 换项目：设 PROJECT_CONFIG_PATH=projects/<name>/config.yaml 后重启
# 打开 http://127.0.0.1:8765  （FastAPI 托管 dist/ 构建产物）
```

### 前端开发模式（热更新）

```bash
cd frontend
npm run dev        # Vite 开发服务器 http://localhost:5173，/api 代理到 :8765
```

## 结构

```
backend/mini_harness/   框架本体（通用内核，不含任何领域逻辑）
├── core/           config(env 层 + ProjectConfig) · message · events · hooks(五点位) · registry(组件注册表)
├── prompts/        base_system.md（框架级系统提示词，领域无关）
├── persistence/    database.py(SQLite: 会话/消息/摘要/长期记忆/审批)
├── memory/         session_store(账本) · compressor(上下文压缩) · longterm(长期记忆)
├── context/        budget(Token 预算) · assembler(缓存友好组装) · prompt_loader(prompt 分层拼接)
├── models/         provider(ABC) · openai_provider(流式+重试+缓存统计)
├── tools/          registry(Schema+路径校验) · permission(五级权限) · levels · loader(装载器) · builtin/(通用工具)
├── sdk/            decorator.py（插件作者唯一契约：@tool）
├── runtime/        engine(Agent Loop，唯一执行入口)
├── observability/  trace.py(TraceWriter 落盘接口)
├── eval/           runner.py(TestCase/EvalResult/EvalRunner 占位)
└── api/            routes(FastAPI + SSE + 组装层 build_engine)
projects/               领域项目：换项目 = 换/加一个目录，内核零改动
└── default/        config.yaml · prompts/system.md · tools/(领域工具) · hooks.py(示例钩子)
configs/                全局默认值 default.yaml + 项目配置模板 example_project.yaml
frontend/               Vue 3 工程（npm + Vite + SFC）
├── src/App.vue         根组件：布局 + SSE 消费状态机 + 跨组件状态
├── src/api.js          REST 封装 + async generator SSE 消费器
├── src/components/     SessionList / MemoryPanel / ToolPanel / StatsPanel / MessageFeed / ApprovalCard / InputBar
└── dist/               构建产物（FastAPI 托管；gitignore）
tests/                  后端单元/集成测试（37 个）
legacy/                 v0.1 教学版 + v0.2 无构建前端存档
```

## 配置驱动（换项目不改内核）

- 环境变量层 `core/config.py::Config`：密钥、端点、路径、运维参数（`.env`）。
- 领域层 `core/config.py::ProjectConfig`：从 YAML 加载，`configs/default.yaml` 打底 + 项目配置覆盖。
- **YAML 写 `null` 或省略 = 回填环境变量层的值**；路径字段的相对路径按配置文件所在目录解析。
- 选择项目：`PROJECT_CONFIG_PATH=projects/<name>/config.yaml`（不设则用 `projects/default/config.yaml`）。

```
projects/coding_agent/
├── config.yaml          # agent_name / system_prompt_path / tools / hooks / budget ...
├── prompts/system.md    # 角色提示词（与框架级 base_system.md 拼接）
├── tools/*.py           # 领域工具（@tool 函数，自动注册）
└── hooks.py             # 生命周期钩子
```

## 扩展点

| 想加什么 | 怎么做 | 是否需要改内核 |
|---|---|---|
| 新领域项目 | 复制 `projects/default/`，改 `config.yaml` + 提示词 | 否 |
| 新工具 | `projects/<name>/tools/*.py` 写 `@tool` 函数 | 否 |
| 输入过滤 / 结果清洗 / 埋点 | 继承 `HarnessHooks`，填进 `hooks:` 配置 | 否 |
| 新模型供应商 | 实现 `BaseModelProvider` + `@register_provider("claude")` | 否 |
| 新记忆后端 | 实现类 + `@register_memory("postgres")` | 否 |
| 新权限门控 | 实现类 + `@register_gate("xxx")` | 否 |
| 新事件类型 / 新循环阶段 | `core/events.py` / `runtime/engine.py` | 是（内核不变量） |

## 修改指南

| 要改什么 | 改哪里 |
|---|---|
| 聊天/审批/流式逻辑 | `frontend/src/App.vue` 的 `runStream` 事件状态机 |
| 消息气泡/工具卡片样式 | `frontend/src/components/MessageFeed.vue` |
| 审批卡片文案与按钮 | `frontend/src/components/ApprovalCard.vue` |
| 配色/设计变量 | `frontend/src/styles/main.css` 的 `:root` |
| 后端地址/代理 | `frontend/vite.config.js`（dev）；`backend/mini_harness/core/config.py`（CORS） |
| 新增 REST 调用 | `frontend/src/api.js` |
| 角色 / 工具集 / 模型 / 预算 | `projects/<name>/config.yaml`（内核零改动） |
| 系统提示词 | `projects/<name>/prompts/system.md`；框架级条款见 `backend/mini_harness/prompts/base_system.md` |
| 工具的实现与权限默认值 | `backend/mini_harness/tools/builtin/*.py` 或 `projects/<name>/tools/*.py` |
| Agent 循环本身 | `backend/mini_harness/runtime/engine.py` |

## 环境变量（.env）

**LLM**：`LLM_API_KEY / LLM_BASE_URL / LLM_MODEL`（或 OPENAI_* 兼容）
**项目选择**：`PROJECT_CONFIG_PATH`（默认 `projects/default/config.yaml`）
**引擎/预算**：`MAX_TURNS`（10）、`EXECUTION_TIMEOUT_SECONDS`（300）、`CONTEXT_TOKEN_BUDGET`（24000）、
`RESERVE_OUTPUT_TOKENS`（4096）、`COMPRESS_THRESHOLD`（0.8）、`KEEP_RECENT_MESSAGES`（8）
**存储**：`MEMORY_DB_PATH`（data/harness.db）、`LONGTERM_TOP_K`（3）
**运维**：`HARNESS_ALLOWED_PATHS`（覆盖 YAML 的 `permission.allowed_paths`，逗号/分号分隔）、
`HARNESS_TRACE_PATH`（设置即启用 JSONL 事件落盘）、`LOG_LEVEL`、`CORS_ORIGINS`

> 注意：对应项在 YAML 里写了具体值时，YAML 优先；只有 YAML 留 `null`/省略才读环境变量。

## API

- `POST /api/chat` `{session_id?, message}` → SSE 事件流
- `POST /api/approvals` `{session_id, call_id, decision: approved|denied, remember?}` → SSE 续跑
- `POST /api/handoff` `{session_id, call_id, result}` → 人工执行结果回填后续跑
- `GET/POST/DELETE /api/sessions`、`GET /api/sessions/{id}/messages`
- `GET/POST/DELETE /api/memories`
- `GET /api/tools` — 工具列表（default/effective/override/path_guard）
- `PUT /api/tools/{name}/permission` `{permission}` — 修改权限（运行时覆盖，持久化，立即生效）
- `DELETE /api/tools/{name}/permission` — 清除覆盖，回到声明默认
- `GET /api/health`

## 工具权限管理

五个级别：`full_trust`（全自动）· `auto_with_notification`（自动+通知）· `ask_first`（逐次审批）· `approve_always`（记住批准）· `manual_only`（仅人工）。

- 解析顺序：**DB 覆盖 > 工具声明默认**（`PermissionGate.effective_level()` 每次决策实时读取）
- 修改入口：前端“工具”Tab 下拉选择，或 `PUT /api/tools/{name}/permission`
- 修改立即对当前与后续所有会话生效（持久化在 SQLite `tool_permissions` 表）
- 非法覆盖值自动回退到声明默认，不会崩
- 测试覆盖：提权/降权/HANDOFF 拦截/脏数据回退/清除/API 端到端/聊天行为即时变化（test_tool_management.py）

## 测试

```bash
pytest tests/ -v                    # 后端 37 个用例
# Windows 上若 %TEMP% 无写权限，加 --basetemp=.pytest_tmp
cd frontend && npm run build        # 前端构建检查
```
