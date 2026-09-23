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

# 4) 启动
python -m uvicorn mini_harness.main:app --app-dir backend --port 8765
# 打开 http://127.0.0.1:8765  （FastAPI 托管 dist/ 构建产物）
```

### 前端开发模式（热更新）

```bash
cd frontend
npm run dev        # Vite 开发服务器 http://localhost:5173，/api 代理到 :8765
```

## 结构

```
backend/mini_harness/
├── core/          config(.env 全量生效) · message(消息模型) · events(类型化事件)
├── persistence/   database.py(SQLite: 会话/消息/摘要/长期记忆/审批)
├── memory/        session_store(账本) · compressor(上下文压缩) · longterm(长期记忆)
├── context/       budget(Token 预算) · assembler(缓存友好组装)
├── models/        provider(ABC) · openai_provider(流式+重试+缓存统计)
├── tools/         registry(Schema+路径校验) · permission(五级权限) · builtin(含记忆工具)
├── runtime/       engine(Agent Loop 唯一执行入口)
└── api/           routes(FastAPI + SSE)
frontend/           Vue 3 工程（npm + Vite + SFC）
├── package.json    vue@3.5 · vite@6 · @vitejs/plugin-vue
├── vite.config.js  dev 代理 /api -> :8765；build 输出 dist/
├── index.html      Vite 入口
├── src/
│   ├── main.js     createApp 入口
│   ├── App.vue     根组件：布局 + SSE 消费状态机 + 跨组件状态
│   ├── api.js      REST 封装 + async generator SSE 消费器
│   ├── styles/main.css  设计变量 + 全局样式（#app 分栏）
│   └── components/
│       ├── SessionList.vue   会话列表（切换/删除）
│       ├── MemoryPanel.vue   长期记忆管理（增/删）
│       ├── ToolPanel.vue     工具权限管理（下拉改级别/覆盖标记/重置）
│       ├── StatsPanel.vue    Token 用量统计
│       ├── MessageFeed.vue   消息流（用户/助手/摘要/工具卡片）
│       ├── ApprovalCard.vue  审批卡片（记住同类 + supersede 提示）
│       └── InputBar.vue      输入框（Enter 发送/Shift+Enter 换行/自动高度）
└── dist/           构建产物（FastAPI 托管；gitignore）
tests/              后端单元/集成测试（37 个）
legacy/             v0.1 教学版 + v0.2 无构建前端存档
```

## 修改指南

| 要改什么 | 改哪里 |
|---|---|
| 聊天/审批/流式逻辑 | `frontend/src/App.vue` 的 `runStream` 事件状态机 |
| 消息气泡/工具卡片样式 | `frontend/src/components/MessageFeed.vue` |
| 审批卡片文案与按钮 | `frontend/src/components/ApprovalCard.vue` |
| 配色/设计变量 | `frontend/src/styles/main.css` 的 `:root` |
| 后端地址/代理 | `frontend/vite.config.js`（dev）；`backend/mini_harness/core/config.py`（CORS） |
| 新增 REST 调用 | `frontend/src/api.js` |
| 系统提示词/工具注册 | `backend/mini_harness/runtime/engine.py` / `tools/builtin.py` |

## 环境变量（.env）

`LLM_API_KEY / LLM_BASE_URL / LLM_MODEL`（或 OPENAI_* 兼容）、
`MAX_TURNS`（10）、`EXECUTION_TIMEOUT_SECONDS`（300）、
`CONTEXT_TOKEN_BUDGET`（24000）、`RESERVE_OUTPUT_TOKENS`（4096）、
`COMPRESS_THRESHOLD`（0.8）、`KEEP_RECENT_MESSAGES`（8）、
`MEMORY_DB_PATH`（data/harness.db）、`LONGTERM_TOP_K`（3）、`LOG_LEVEL`、`CORS_ORIGINS`。

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
pytest tests/ -v          # 后端 37 个用例
cd frontend && npm run build   # 前端构建检查
```
