# eval —— 评估框架（P1-3 占位）

本期只固定接口，实现在后续迭代补齐。现在放进来是为了让"评测"从一开始就是一等公民，
而不是事后补丁。

## 现状

- `runner.py` 定义了 `TestCase` / `EvalResult` / `EvalRunner`，方法与签名已固定；
- 全部实现体是 `raise NotImplementedError`；`import mini_harness.eval` 可用。

## 设计约束（实现时必须遵守）

1. **离线可跑**：被测引擎的 provider 换成脚本化的 mock（参考 `tests/conftest.py` 的
   `MockProvider`），不得依赖真实模型网络调用——否则结果不可复现、也无法进 CI。
2. **只看事件契约**：断言基于 `RuntimeEngine.run()` 产出的 `Event` 流与账本状态，
   不依赖任何私有属性；这样评测对内核重构免疫。
3. **一次运行一份 trace**：结果里带 `Event.to_trace_dict()` 序列，便于失败时回放。

## 计划覆盖的断言维度

| 维度 | 例子 |
|---|---|
| 工具调用序列 | 期望 `[get_time]` 恰好一次、顺序正确 |
| 审批行为 | 高风险工具是否挂起等待审批，而非直接执行 |
| 最终回答 | 是否包含关键结论、是否误报"已执行" |
| 预算 | `max_turns` 耗尽、上下文压缩是否按阈值触发 |
| 成本 | `prompt_tokens` / `cache_hits` 是否在预期区间 |

## 待办

- [ ] `load_cases()`：YAML/JSON 用例集加载与校验
- [ ] `run_case()`：mock provider 组装 + 事件流消费 + 断言
- [ ] `run_suite()`：批量执行 + 结果聚合（通过率、耗时、token）
- [ ] CLI 入口（`python -m mini_harness.eval.runner <cases.yaml>`）
- [ ] 与 `observability/trace.py` 打通：失败用例自动存一份 JSONL
