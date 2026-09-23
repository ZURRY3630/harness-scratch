# 01 - 从零跑通第一个项目

本篇只做一件事：让你在 10 分钟内看到一个能调工具、能流式回答的 Agent 跑起来。
（架构与设计说明见 [03-architecture.md](03-architecture.md)，本篇不涉及。）

## 1. 环境准备

前提：Python 3.10+（推荐 3.11）、git、一个 OpenAI 协议兼容的模型服务（DeepSeek / OpenAI / 通义 / Ollama 均可）。

```bash
git clone <本仓库> && cd harness-scratch

# 安装依赖（二选一）
uv sync                        # 有 uv 时推荐
pip install -e ".[dev]"        # 纯 pip
```

安装后确认内核可导入：

```bash
python -c "import mini_harness; print(mini_harness.__version__)"
# 0.4.0
```

## 2. 配置模型接入

在**仓库根目录**创建 `.env`（该文件已在 `.gitignore` 中，不会入库）：

```bash
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
```

| 变量 | 说明 |
|---|---|
| `LLM_API_KEY` | 密钥；也兼容 `OPENAI_API_KEY` |
| `LLM_BASE_URL` | 服务端点；也兼容 `OPENAI_BASE_URL`。Ollama 填 `http://localhost:11434/v1` |
| `LLM_MODEL` | 模型名；也兼容 `OPENAI_MODEL` |

## 3. 建一个最小项目

在 `projects/` 下新建你自己的目录，三个文件即可成项目：

```
projects/my_first_project/
├── config.yaml           # 项目配置
├── prompts/system.md     # 角色提示词
└── tools/hello.py        # 领域工具
```

**配置文件** `projects/my_first_project/config.yaml`：

```yaml
name: my_first_project
agent_name: 我的第一个助手
language: 中文
system_prompt_path: prompts/system.md      # 相对本文件所在目录

tools:
  builtin:                                 # 内置工具：按工具名白名单启用
    - echo
    - get_time
  plugins_dir: tools                       # 本项目专属工具目录，启动时自动扫描
```

其余字段（模型、预算、路径边界等）都会从 `configs/default.yaml` 与环境变量补齐，先不用管。

**角色提示词** `projects/my_first_project/prompts/system.md`：

```markdown
# 角色

你是一个友好的入门助手，回答保持在一句话以内。
```

**领域工具** `projects/my_first_project/tools/hello.py`：

```python
"""入门演示工具。"""

from mini_harness.sdk.decorator import tool
from mini_harness.tools.levels import PermissionLevel


@tool(
    name="greet",
    description="按名字生成一句问候语。",
    permission=PermissionLevel.FULL_TRUST,
)
def greet(name: str) -> str:
    return f"你好，{name}！很高兴见到你。"
```

## 4. 启动

```bash
# PowerShell
$env:PROJECT_CONFIG_PATH = "projects/my_first_project/config.yaml"
python -m uvicorn mini_harness.main:app --app-dir backend --port 8765

# bash
PROJECT_CONFIG_PATH=projects/my_first_project/config.yaml \
  python -m uvicorn mini_harness.main:app --app-dir backend --port 8765
```

预期输出（启动成功日志）：

```
INFO:     Started server process [12345]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8765 (Press CTRL+C to quit)
```

`--app-dir backend` 不能省：内核包 `mini_harness/` 位于 `backend/` 下，这个参数把它加进模块搜索路径。

## 5. 确认跑通

另开一个终端，三个请求依次验证：

```bash
# 1) 项目身份：agent_name 来自你写的配置
curl http://127.0.0.1:8765/api/health
# {"status":"ok","version":"0.4.0","frontend":"built","project":"my_first_project","agent_name":"我的第一个助手"}

# 2) 工具清单：builtin 两个 + 你的 greet
curl http://127.0.0.1:8765/api/tools

# 3) 对话（SSE 流）
curl -N -X POST http://127.0.0.1:8765/api/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id":"first","message":"你好，请用 greet 工具跟我打个招呼"}'
```

第 3 条会返回一串事件（每行一条）：先是 `run_started` → `turn_started`，
工具执行时出现 `tool_executed`（`"tool":"greet"`，`"ok":true`），最后是 `turn_finished` 与 `run_finished`。
浏览器打开 `http://127.0.0.1:8765` 也能直接对话，界面左上角会显示「我的第一个助手」。

跑通的标志：`api/health` 里的 `agent_name` 是你配置的名字，且 `tool_executed` 事件里能看到 `greet`。

## 6. 如果出错，先看这里

**① `ModuleNotFoundError: No module named 'mini_harness'`**
原因：启动时漏了 `--app-dir backend`，且没有执行过 `pip install -e .`。
解决：补上 `--app-dir backend`，或先安装一次（见第 1 节）。

**② 事件里出现 `{"type": "error", "data": {"message": "模型调用失败: ..."}}`**
原因：`.env` 缺 `LLM_API_KEY` / `LLM_BASE_URL`，或变量名写成了别的（例如 `API_KEY`）。
解决：确认 `.env` 在**仓库根目录或 `backend/` 目录**下，且三个变量名与本篇第 2 节一致；Ollama 之类无需密钥的服务，`LLM_API_KEY` 随便填一个非空值。

**③ 启动时报配置错误**
`FileNotFoundError: 项目配置不存在: ...` → `PROJECT_CONFIG_PATH` 的相对路径按**当前工作目录**解析，请确认是从仓库根目录启动，或改用绝对路径。
`ValueError: 未知内置工具: ['gettime']（可用: ['echo', 'get_time', ...]）` → `config.yaml` 里工具名拼错，按错误信息里列出的可用名字改正。
