# 08 - 接入新的模型 Provider

计划内容：`BaseModelProvider` 接口约定、`ModelResponse` / `ToolCall` 字段要求、流式协议、
`call_id` 与参数解析的兜底规则、用 `@register_provider` 注册并在 YAML 里切换、
Claude（Anthropic 协议）与 Ollama（本地推理）的完整接入示例。

契约摘要见 [contracts.md](contracts.md) 第 6 节。

> TODO: 待完善（第二轮补齐）
