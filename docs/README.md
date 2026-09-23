# 文档索引

| 文档 | 对应版本 | 最后更新 |
|---|---|---|
| README.md | v0.4.0 | 2026-09-23 |
| 01-quickstart.md | v0.4.0 | 2026-09-23 |
| 02-concepts.md | v0.4.0 | 2026-09-23 |
| contracts.md | v0.4.0 | 2026-09-23 |
| 03-architecture.md | v0.4.0 | 2026-09-23 |
| 04-extension-points.md | v0.4.0 | 2026-09-23 |
| 05-build-a-project.md | v0.4.0 | 2026-09-23 |
| 06-15、appendix/* | v0.4.0 | 2026-09-23 |

---

# MiniHarness 框架文档

## 这是什么

MiniHarness 是一个 Python Agent 框架：内核（`backend/mini_harness/`）只负责模型调用循环、工具执行、权限门控、上下文与记忆，领域逻辑全部放在 `projects/<项目名>/` 里。你写配置、提示词、工具和钩子，内核一行都不用改。

## 解决什么问题

直接调 API 时，你要自己实现：多轮工具调用循环、模型幻觉工具的校验、危险操作的人工审批、上下文超预算后的压缩、会话状态持久化、崩溃后恢复。用这个框架时，这些能力已经在内核里跑通了，你只需要声明"我的助手是谁、能用哪些工具、什么工具要审批"。

## 核心优势

- **配置驱动**：换领域 = 加一个 `projects/<name>/` 目录（YAML + 提示词 + 工具文件），内核零改动。
- **扩展点齐备**：模型供应商、记忆后端、工具、生命周期钩子、权限策略、Trace、评估集，全部按契约插拔。
- **状态外置**：消息、审批、权限覆盖、长期记忆都落 SQLite，进程可重启、会话可续跑。

## 快速开始

```bash
git clone <本仓库> && cd harness-scratch
uv sync                                  # 或 pip install -e ".[dev]"
```

在仓库根创建 `.env`（模型接入信息）：

```bash
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
```

跑起来（`--app-dir backend` 让 Python 找到内核包）：

```bash
python -m uvicorn mini_harness.main:app --app-dir backend --port 8765
# 浏览器打开 http://127.0.0.1:8765
```

不想起服务、只想在脚本里用引擎：

```python
import asyncio
from mini_harness.core.config import load_project_config
from mini_harness.api.routes import build_engine

engine = build_engine("demo", load_project_config("projects/customer_service/config.yaml"))

async def main():
    async for event in engine.run("我的订单 SO2026001 到哪了？"):
        print(event.type.value, event.data)

asyncio.run(main())
```

## 文档导航

| 文档 | 内容 |
|---|---|
| [01-quickstart.md](01-quickstart.md) | 从零跑通第一个项目（含环境准备与三个常见坑） |
| [02-concepts.md](02-concepts.md) | 10 个核心术语 |
| [contracts.md](contracts.md) | 稳定接口契约：工具装饰器、权限级别、钩子、事件、配置字段、Provider |
| [03-architecture.md](03-architecture.md) | 三层架构与数据流 |
| [04-extension-points.md](04-extension-points.md) | 扩展点索引表：想改 X 该看哪篇 |
| [05-build-a-project.md](05-build-a-project.md) | 完整案例：0 到 1 做一个客服助手项目 |

## License / 贡献

内部项目，未附带开源许可；提交前请按 [contracts.md](contracts.md) 与 [appendix/migration.md](appendix/migration.md) 同步接口契约与迁移记录。
