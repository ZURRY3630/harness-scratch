# 10 - 写一个钩子

计划内容：`HarnessHooks` 五个点位的触发时机与返回值约定、输入过滤 / 结果清洗 / 埋点三类典型用法、
多个钩子的链式顺序、钩子异常如何被隔离、钩子与 Prompt 缓存的关系（改写 `messages` 会破坏前缀稳定性）、
完整示例（敏感词拦截、PII 脱敏、调用计数）。

契约摘要见 [contracts.md](contracts.md) 第 3 节；可运行示例见 [05-build-a-project.md](05-build-a-project.md) 第 6 步。

> TODO: 待完善（第二轮补齐）
