# 04 - 扩展点总览（索引）

二开者的导航图：左边是你想做的事，右边是要读的那一篇。**内核代码不允许修改**——表里所有标 ⭐~⭐⭐ 的行都能在不碰 `backend/mini_harness/` 的前提下完成。

## 1. 扩展现有项目

| 想改什么 | 扩展方式 | 详细文档 | 难度 |
|---|---|---|---|
| 改 System Prompt（角色、语气、边界） | 编辑 `projects/<name>/prompts/system.md` | [06](06-configure.md) | ⭐ |
| 改角色名与回答语言 | 编辑 YAML 的 `agent_name` / `language` | [06](06-configure.md) | ⭐ |
| 启用/关闭内置工具 | 编辑 YAML 的 `tools.builtin` | [06](06-configure.md) | ⭐ |
| 调预算（上下文上限、压缩阈值、轮次、超时） | 编辑 YAML 的 `budget` / `max_turns` / `tool_timeout` | [06](06-configure.md) | ⭐ |
| 限制工具能操作的目录 | 编辑 YAML 的 `permission.allowed_paths` | [06](06-configure.md) | ⭐ |
| 调工具权限级别（全自动/逐次审批/转人工） | 工具声明的 `permission` 或运行时 `PUT /api/tools/{name}/permission` | [06](06-configure.md) | ⭐ |
| 加领域工具 | 写 `@tool` 函数放进 `projects/<name>/tools/` | [07](07-custom-tools.md) | ⭐ |
| 安装/创建一个技能（文档 + CLI 脚本） | 放技能包 + 配 `skills.enabled` | [16](16-skills.md) | ⭐ |
| 加输入过滤 / 结果清洗 / 埋点 | 写 `HarnessHooks` 子类并填进 `hooks:` | [10](10-custom-hooks.md) | ⭐ |
| 接日志系统、留事件轨迹 | 写 Hook + 配置 `HARNESS_TRACE_PATH` | [12](12-observability.md) | ⭐ |

## 2. 替换可插拔组件

| 想改什么 | 扩展方式 | 详细文档 | 难度 |
|---|---|---|---|
| 换模型（Claude / Ollama / 本地推理） | 实现 `BaseModelProvider` + `@register_provider("your_name")` | [08](08-custom-provider.md) | ⭐ |
| 换记忆后端（Redis / Postgres） | 实现存储类 + `@register_memory("your_name")` | [09](09-custom-memory.md) | ⭐⭐ |
| 改权限策略（规则引擎 / 组织策略） | 实现 Gate + `@register_gate("your_name")` | [11](11-custom-gate.md) | ⭐⭐ |
| 事件落盘到外部系统 | 实现 `TraceWriter` | [12](12-observability.md) | ⭐⭐ |

## 3. 新建一个领域项目

| 想改什么 | 扩展方式 | 详细文档 | 难度 |
|---|---|---|---|
| 从零做一个新领域项目 | 建 `projects/<name>/`：config.yaml + prompts + tools + hooks | [05](05-build-a-project.md) | ⭐⭐ |
| 复用现有项目做变体 | 复制 `projects/default/`，改 `config.yaml` 与提示词 | [05](05-build-a-project.md) | ⭐ |

## 4. 交付与质量

| 想改什么 | 扩展方式 | 详细文档 | 难度 |
|---|---|---|---|
| 建评估集、回归验证改动 | 写 `TestCase` + 脚本化 provider | [13](13-evaluation.md) | ⭐⭐ |
| 生产部署（并发、密钥、持久化） | 环境变量 + 反向代理 + 单写者数据库 | [14](14-deployment.md) | ⭐⭐ |

## 5. 需要改内核（明确不推荐）

以下改动会破坏二开约束，只有在框架维护者评估后才可以做。列在这里是为了让你**知道边界在哪**，而不是鼓励你去做。

| 想改什么 | 要动哪里 | 影响 |
|---|---|---|
| 新增一个内置工具（所有项目共用） | `backend/mini_harness/tools/builtin/` + 在 `configs/default.yaml` 登记 | 影响所有项目，需回归全部项目 |
| 新增事件类型 | `backend/mini_harness/core/events.py` | 属于契约变更，必须记入 `appendix/migration.md` |
| 改变 Agent Loop 的执行顺序 | `backend/mini_harness/runtime/engine.py` | 会破坏既有项目对审批时机、挂起语义的依赖 |

## 6. 三个最常见的诉求，最短路径

- **"我想让助手只回答某类问题"** → 改 `projects/<name>/prompts/system.md`，写清职责与边界（[06](06-configure.md)）。
- **"我想让它能查我们内部系统的数据"** → 写一个 `@tool` 函数放进 `projects/<name>/tools/`，用 `requests` / SDK 调用内部接口（[07](07-custom-tools.md)）。
- **"我要审批高危操作，但不审批只读查询"** → 只读工具声明 `PermissionLevel.FULL_TRUST`，高危工具声明 `ASK_FIRST`（[07](07-custom-tools.md)）。

## 7. 契约入口

任何扩展点实现之前，先读 [contracts.md](contracts.md)——那里是方法的准确签名、返回值约定与稳定性等级。接口变更记录在 [appendix/migration.md](appendix/migration.md)。
