"""Shared dictionary groups, seeds, and validation helpers."""

KNOWLEDGE_CATEGORY = "knowledge_category"
KNOWLEDGE_BASE = "knowledge_base"
ISSUE_TAG = "issue_tag"
ISSUE_TYPE = "issue_type"

GROUP_LABELS = {
    KNOWLEDGE_CATEGORY: "知识分类",
    KNOWLEDGE_BASE: "知识库选项",
    ISSUE_TAG: "问题标签",
    ISSUE_TYPE: "问题类型",
}

DICTIONARY_SEEDS = {
    KNOWLEDGE_CATEGORY: [
        (10, "游戏玩法", "游戏玩法"), (20, "引导流程", "引导流程"),
        (30, "活动规则", "活动规则"), (40, "道具与奖励", "道具与奖励"),
        (50, "账号与登录", "账号与登录"),
    ],
    KNOWLEDGE_BASE: [
        (10, "游戏玩法知识库", "游戏玩法知识库"),
        (20, "通用业务知识库", "通用业务知识库"),
    ],
    ISSUE_TAG: [
        (10, "知识库优化", "知识库优化"), (20, "AI 逻辑优化", "AI 逻辑优化"),
        (30, "无效问题", "无效问题"), (40, "用户抗拒 AI", "用户抗拒 AI"),
    ],
    ISSUE_TYPE: [
        (10, "兜底异常", "兜底异常"), (20, "其他待复核", "其他待复核"),
        (30, "其他疑似问题", "其他疑似问题"), (40, "情绪风险", "情绪风险"),
        (50, "技术异常", "技术异常"), (60, "数据异常", "数据异常"),
        (70, "无效回复", "无效回复"), (80, "服务未满足", "服务未满足"),
        (90, "知识错误", "知识错误"), (100, "知识错误/幻觉风险", "知识错误/幻觉风险"),
        (110, "转人工问题", "转人工问题"), (120, "风险场景未满足", "风险场景未满足"),
    ],
}


def seed_values(group_key: str) -> set[str]:
    return {value for _sort, value, _label in DICTIONARY_SEEDS.get(group_key, [])}


def active_values(db, group_key: str) -> set[str]:
    """Return only enabled values, including when an entire group is disabled."""
    from database import SystemDictionary

    rows = db.query(SystemDictionary.value).filter(
        SystemDictionary.group_key == group_key,
        SystemDictionary.enabled.is_(True),
    ).all()
    return {row.value for row in rows}


def is_active_value(db, group_key: str, value: object) -> bool:
    return isinstance(value, str) and value.strip() in active_values(db, group_key)
