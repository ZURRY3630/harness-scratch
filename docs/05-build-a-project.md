# 05 - 完整案例：从 0 到 1 做一个客服助手

本篇带你做完一个真实项目：**电商客服助手**。做完之后你会拥有一个能查订单、答政策、建工单，
并且会自动给手机号、邮箱打码的 Agent。

全部代码在仓库里可以直接对照：`projects/customer_service/`。**不需要修改 `backend/mini_harness/` 中任何一行。**

## 1. 场景与目标

| 需求 | 落地方式 |
|---|---|
| 回答订单状态 | 工具 `lookup_order` |
| 回答退换货政策 | 工具 `refund_policy` |
| 创建售后工单 | 工具 `create_ticket`（有副作用 → 逐次审批） |
| 记住用户偏好 | 启用内置工具 `memory_save` / `memory_search` |
| 不让手机号、邮箱进入账本与模型上下文 | 钩子 `PIIMaskHooks.after_tool_execute` |

## 2. 最终目录结构

```
projects/customer_service/
├── config.yaml            项目配置：身份、工具集、钩子
├── prompts/
│   └── system.md          角色与边界
├── tools/
│   ├── __init__.py        空文件即可（装载器会跳过 _ 开头的文件）
│   ├── orders.py          只读工具：查订单、查政策
│   └── tickets.py         有副作用工具：建工单、列工单
└── hooks.py               钩子：脱敏 + 埋点
```

## 3. 第 1 步：建目录

```bash
mkdir -p projects/customer_service/prompts projects/customer_service/tools
touch projects/customer_service/tools/__init__.py
```

## 4. 第 2 步：写配置

文件 `projects/customer_service/config.yaml`：

```yaml
name: customer_service
agent_name: 客服助手
language: 中文

system_prompt_path: prompts/system.md

tools:
  builtin:
    - memory_save
    - memory_search
  plugins_dir: tools

permission:
  approval_store: true

hooks:
  - projects.customer_service.hooks.PIIMaskHooks
```

逐项说明：

| 配置 | 作用 |
|---|---|
| `agent_name` | 注入提示词占位符 `{{AGENT_NAME}}`，同时作为界面标题显示 |
| `language` | 注入 `{{LANGUAGE}}`，决定框架级提示词里的回答语言 |
| `system_prompt_path` | 项目级提示词；相对本配置文件所在目录解析 |
| `tools.builtin` | 只启用需要的内置工具；不列出来的不会被注册，模型也看不到 |
| `tools.plugins_dir` | 本项目工具目录，启动时扫描其中所有 `@tool` 函数 |
| `hooks` | 钩子类的导入路径，格式为 `模块路径.类名`；顺序即执行顺序 |

未在这里声明的字段（模型、预算、路径边界等）自动继承 `configs/default.yaml` 与环境变量。

## 5. 第 3 步：写角色提示词

文件 `projects/customer_service/prompts/system.md`：

```markdown
# 角色

你是某电商平台的客服助手。

## 职责

- 处理订单查询、退换货政策、售后工单三类问题；
- 回答前先用工具核实事实（订单状态、政策条款），不要凭记忆回答；
- 查不到订单时，如实告知并建议用户核对订单号，不要编造订单信息。

## 边界

- 不承诺赔偿金额、不修改订单数据；
- 涉及投诉升级时，创建工单并告知用户预计处理时限；
- 与售后无关的问题（闲聊、技术咨询等），礼貌说明职责范围。
```

提示词与框架级 `backend/mini_harness/prompts/base_system.md` 自动拼接：框架级负责"工具使用纪律、
不可信内容处理、表达方式"，项目级只管角色与边界。因此你不需要在项目提示词里重复工具纪律。

## 6. 第 4 步：写只读工具

文件 `projects/customer_service/tools/orders.py`：

```python
"""订单与政策查询工具（只读，全自动执行）。"""

from __future__ import annotations

from mini_harness.sdk.decorator import tool
from mini_harness.tools.levels import PermissionLevel

# 演示数据：真实项目里换成订单服务 / 商品中心 API
_ORDERS = {
    "SO2026001": {"status": "已发货", "item": "机械键盘 K8", "carrier": "顺丰", "eta": "2026-09-25", "phone": "13800138000"},
    "SO2026002": {"status": "待付款", "item": "显示器支架", "carrier": "-", "eta": "-", "phone": "13900139000"},
    "SO2026003": {"status": "已签收", "item": "人体工学椅", "carrier": "京东", "eta": "2026-09-20", "phone": "13700137000"},
}

_POLICIES = {
    "数码": "7 天无理由退货（需保持外观完好、配件齐全），15 天换货。",
    "家具": "30 天无理由退货（需用户承担退回运费），非质量问题拆封后不支持退货。",
    "耗材": "一经拆封不支持退货，质量问题 15 天内换新。",
}

@tool(
    name="lookup_order",
    description="按订单号查询订单状态、商品、承运商、预计送达时间与收件人联系方式。",
    permission=PermissionLevel.FULL_TRUST,
)
def lookup_order(order_id: str) -> str:
    order = _ORDERS.get(order_id.strip().upper())
    if order is None:
        return f"未找到订单 {order_id}，请核对订单号后重试。"
    return (
        f"订单 {order_id}：状态={order['status']}，商品={order['item']}，"
        f"承运商={order['carrier']}，预计送达={order['eta']}，"
        f"收件人手机={order['phone']}，邮箱=buyer@example.com。"
    )

@tool(
    name="refund_policy",
    description="按商品类别查询退换货政策。类别取值：数码 / 家具 / 耗材。",
    permission=PermissionLevel.FULL_TRUST,
)
def refund_policy(category: str) -> str:
    policy = _POLICIES.get(category.strip())
    if policy is None:
        return f"未收录类别 '{category}'。可用类别：{'、'.join(_POLICIES)}。"
    return f"{category}类退换货政策：{policy}"
```

三个要点：

- `permission=PermissionLevel.FULL_TRUST`：只读查询不需要打扰用户；有副作用的工具才用 `ASK_FIRST`。
- `description` 里写明**参数取值范围**（"类别取值：数码 / 家具 / 耗材"），模型选参数的准确率直接取决于这句话。
- 找不到数据时返回**可行动**的提示（"请核对订单号后重试"），而不是空字符串——模型能据此组织追问。

## 7. 第 5 步：写有副作用的工具

文件 `projects/customer_service/tools/tickets.py`：

```python
"""售后工单工具（有副作用，逐次审批）。"""

from __future__ import annotations

import itertools
import time

from mini_harness.sdk.decorator import tool
from mini_harness.tools.levels import PermissionLevel

_TICKET_SEQ = itertools.count(1)
_TICKETS: list[dict] = []

@tool(
    name="create_ticket",
    description="创建售后工单，返回工单号。summary 为一行标题，detail 为问题细节。",
    permission=PermissionLevel.ASK_FIRST,   # 有副作用：每次都要人工批准
)
def create_ticket(summary: str, detail: str) -> str:
    ticket_id = f"T{time.strftime('%Y%m%d')}-{next(_TICKET_SEQ):04d}"
    _TICKETS.append({"ticket_id": ticket_id, "summary": summary, "detail": detail})
    return f"已创建工单 {ticket_id}：{summary}（预计 1 个工作日内响应）"

@tool(
    name="list_tickets",
    description="列出本次运行期间已创建的工单，用于确认工单是否创建成功。",
    permission=PermissionLevel.FULL_TRUST,
)
def list_tickets() -> str:
    if not _TICKETS:
        return "暂无工单。"
    return "\n".join(f"- {t['ticket_id']} {t['summary']}" for t in _TICKETS)
```

要点：

- 有副作用的工具声明 `ASK_FIRST`：模型提出调用后会**挂起等你批准**，批准后才真正执行。
- 提供一个配套的只读工具（`list_tickets`）让模型能自证"确实建成了"，减少"我已完成"式的幻觉。

## 8. 第 6 步：写钩子

文件 `projects/customer_service/hooks.py`：

```python
"""项目钩子：工具结果脱敏 + 调用埋点。

脱敏为什么放在 `after_tool_execute`：工具返回的原文会进入对话账本并回灌给模型，
在写账本之前清洗，才能真正保证敏感信息不落库、不出现在后续上下文里。
"""

from __future__ import annotations

import re

from mini_harness.core.hooks import HarnessHooks

_PHONE = re.compile(r"1[3-9]\d{9}")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")

def _log(msg: str) -> None:
    print(f"[customer_service] {msg}", flush=True)

class PIIMaskHooks(HarnessHooks):
    """把工具结果里的手机号、邮箱打码，并记录每次 LLM 调用的规模。"""

    async def after_tool_execute(self, name: str, args: dict, result: str) -> str:
        masked = _PHONE.sub(lambda m: m.group()[:3] + "****" + m.group()[-4:], result)
        masked = _EMAIL.sub("***@***", masked)
        if masked != result:
            _log(f"工具 {name} 的返回内容已脱敏")
        return masked

    async def before_llm_call(self, messages, tools):
        _log(f"调用模型：{len(messages)} 条消息 / {len(tools or [])} 个工具")
        return messages, tools

    async def on_error(self, exc: BaseException) -> None:
        _log(f"引擎错误：{type(exc).__name__}: {exc}")
```

只覆盖了需要的方法，其余三个点位保持基类的空实现。钩子的完整接口与调用时机见
[10-custom-hooks.md](10-custom-hooks.md)；五个点位的方法签名在 [contracts.md](contracts.md)。

## 9. 第 7 步：启动

```bash
# 在仓库根目录执行
$env:PROJECT_CONFIG_PATH = "projects/customer_service/config.yaml"
python -m uvicorn mini_harness.main:app --app-dir backend --port 8765
```

确认装载结果：

```bash
curl http://127.0.0.1:8765/api/health
# {"status":"ok","version":"0.4.0","frontend":"built","project":"customer_service","agent_name":"客服助手"}

curl http://127.0.0.1:8765/api/tools
```

`/api/tools` 应返回 6 个工具（顺序即下表）：

| 工具 | 生效级别 | 来源 |
|---|---|---|
| `memory_save` | `ask_first` | 内置 |
| `memory_search` | `full_trust` | 内置 |
| `lookup_order` | `full_trust` | 本项目 |
| `refund_policy` | `full_trust` | 本项目 |
| `create_ticket` | `ask_first` | 本项目 |
| `list_tickets` | `full_trust` | 本项目 |

工具改动后需要重启进程：装载发生在启动时，没有热重载。

## 10. 第 8 步：验证四个场景

以下都是真实运行结果。

**① 查订单（只读，全自动）**

> 用户：我的订单 SO2026001 到哪了？

服务端先打印一行（来自钩子），随后工具直接执行：

```
[customer_service] 调用模型：2 条消息 / 6 个工具
[customer_service] 工具 lookup_order 的返回内容已脱敏
[customer_service] 调用模型：4 条消息 / 6 个工具
```

工具结果事件（注意手机号与邮箱已经被钩子改写过，账本里存的就是这一份）：

```json
{"tool": "lookup_order", "ok": true,
 "result": "订单 SO2026001：状态=已发货，商品=机械键盘 K8，承运商=顺丰，预计送达=2026-09-25，收件人手机=138****8000，邮箱=***@***。"}
```

最终回答：

```
您的订单 SO2026001 目前状态如下：

- 状态：已发货
- 商品：机械键盘 K8
- 承运商：顺丰
- 预计送达：2026-09-25
- 收件人手机：138****8000
```

**② 查政策（模型自行选类别）**

> 用户：机械键盘想退货，属于什么政策？

模型把"机械键盘"归到"数码"类，调用 `refund_policy(category="数码")`，返回：

```
数码类退换货政策：7 天无理由退货（需保持外观完好、配件齐全），15 天换货。
```

**③ 建工单（挂起 → 批准 → 执行）**

> 用户：帮订单 SO2026001 建一个工单，说明是空格键失灵

模型先查订单（同样被脱敏），然后请求创建工单，引擎**挂起**等待批准：

```json
{"type": "approval_required",
 "data": {"call_id": "call_00_EpecTZTBcvPLEWybb74R7464",
          "tool_name": "create_ticket",
          "arguments": {"summary": "订单 SO2026001 机械键盘 K8 空格键失灵",
                        "detail": "用户反馈订单 SO2026001（商品：机械键盘 K8，承运商：顺丰，预计送达 2026-09-25）的空格键失灵，疑似质量问题。请售后核实处理，判断是否符合换货/退货条件。"}}}
```

前端会弹出审批卡片。点"批准"（或 `POST /api/approvals {session_id, call_id, decision:"approved"}`）后继续执行：

```json
{"tool": "create_ticket", "ok": true,
 "result": "已创建工单 T20260923-0001：订单 SO2026001 键盘空格键失灵（预计 1 个工作日内响应）"}
{"tool": "list_tickets", "ok": true, "result": "- T20260923-0001 订单 SO2026001 键盘空格键失灵"}
```

工单号含当天日期，你运行时看到的编号会不同。最终回答：

```
工单已创建成功 ✅

- 工单号：T20260923-0001
- 问题：订单 SO2026001（机械键盘 K8）空格键失灵
- 预计响应：1 个工作日内
```

**④ 越界提问（角色边界生效）**

> 用户：帮我写一个 Python 快速排序

预期回答：说明这是售后客服，不提供编程协助，并询问是否需要订单或售后相关帮助。
若回答开始写代码，说明 `prompts/system.md` 的"边界"一段写得不够明确，回去加具体要求。

## 11. 跑通标志

四项都满足就说明项目搭好了：

- [ ] `api/health` 的 `project` / `agent_name` 是你配置的值
- [ ] `api/tools` 返回 6 个工具，权限级别与第 7 节的表一致
- [ ] 查订单的 `tool_executed` 事件里，手机号显示为 `138****8000`
- [ ] 建工单时先出现 `approval_required`（而不是直接执行），批准后才出现 `ok: true` 的 `tool_executed`

## 12. 接下来可以改什么

| 想做的事 | 去哪一篇 |
|---|---|
| 调预算、轮次上限、可写目录 | [06-configure.md](06-configure.md) |
| 换成真实接口、加更多工具 | [07-custom-tools.md](07-custom-tools.md) |
| 换成公司自研模型 | [08-custom-provider.md](08-custom-provider.md) |
| 加敏感词拦截、加审计埋点 | [10-custom-hooks.md](10-custom-hooks.md) |
| 让高危工具默认转人工 | [11-custom-gate.md](11-custom-gate.md) |
| 为这个项目建回归用例 | [13-evaluation.md](13-evaluation.md) |

全部扩展点索引见 [04-extension-points.md](04-extension-points.md)。
