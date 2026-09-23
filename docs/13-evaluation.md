# 13 - 建立评估集

计划内容：`TestCase` / `EvalResult` 字段定义、用脚本化 provider 保证离线可复现、
断言维度（工具调用序列、审批挂起、最终回答片段、预算用量）、用例集文件格式、
接入 CI 的方式、失败用例如何借助 Trace 回放定位问题。

契约摘要见 [contracts.md](contracts.md) 第 7 节；接口占位实现见 `backend/mini_harness/eval/runner.py`。

> TODO: 待完善（第三轮补齐）
