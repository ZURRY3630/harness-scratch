"""MiniHarness —— 最小但生产向的 Agent Harness。

模块布局（依赖单向向下，禁止循环引用）：

    core         基础定义：配置、消息模型、类型化事件
    persistence  SQLite 持久化：会话 / 消息 / 摘要 / 长期记忆
    memory       会话账本 + 上下文压缩器 + 长期记忆库
    context      Token 预算管理 + 缓存友好的上下文组装
    models       模型供应商抽象与 OpenAI 兼容实现
    tools        工具注册表 / 权限门控 / 内置工具
    runtime      RuntimeEngine：Agent Loop，唯一执行入口
    api          FastAPI 路由 + SSE 流式接口
"""

__version__ = "0.6.0"  # 与 pyproject.toml 保持一致；接口变更需记入 docs/appendix/migration.md
