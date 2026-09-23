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
