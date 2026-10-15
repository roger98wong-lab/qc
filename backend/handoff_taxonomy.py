"""Closed business taxonomy for the manual/backend handoff reason."""

BACKEND_HANDOFF_REASON = "需要人工权限或后台处理"
UNCLASSIFIED_DETAIL = "未细分"
INSUFFICIENT_DETAIL = "信息不足无法判断"
HANDOFF_DETAIL_TYPES = (
    "充值金额查询", "VIP认证", "兑换社区互动码", "普通礼包码咨询", "VIP专属福利码",
    "合服转服跨服", "功能或数值Bug", "活动排期或资格咨询", "玩法建议或功能请求",
    "活动奖励未发放需核实后台日志", "账号绑定找回解绑", "确认规则机制",
    "充值未到账或支付异常", "客户端闪退或无法进入", INSUFFICIENT_DETAIL,
    "资源操作", "角色或道具数据异常需核实后台日志", "举报他人违规作弊",
    "记录转达跟进", "其他需后台处理",
)


def display_detail_type(handoff: dict) -> str | None:
    """Read old records safely without mutating or classifying them."""
    if handoff.get("reason_type") != BACKEND_HANDOFF_REASON:
        return None
    detail = handoff.get("detail_type")
    return detail if isinstance(detail, str) and detail in HANDOFF_DETAIL_TYPES else UNCLASSIFIED_DETAIL
