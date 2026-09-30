"""
AI 质检分析引擎
"""
import json
from services.maas_client import MaaSClientError, chat_completion
import config

ISSUE_CATEGORIES = (
    "答非所问", "意图识别错误", "无效回复", "严重语法/乱码",
    "语言错误", "语气问题", "疑似错误承诺",
)
SEVERITIES = ("严重", "中级", "一般", "需人工复核")
CANDIDATE_KNOWLEDGE_DECISIONS = (
    "candidate_ready", "candidate_needs_enrichment", "candidate_pending_feedback",
)
KNOWLEDGE_DECISIONS = CANDIDATE_KNOWLEDGE_DECISIONS + (
    "not_candidate", "manual_review", "no_human_answer",
)
KNOWLEDGE_CATEGORIES = ("游戏玩法", "引导流程", "活动规则", "道具与奖励", "账号与登录")
KNOWLEDGE_VALIDATION_STATUSES = ("player_validated", "unvalidated", "not_applicable")
SPEAKERS = ("player", "ai", "human_agent", "system", "unknown")
TRANSLATION_STATUSES = ("pending", "processing", "success", "not_required", "uncertain", "failed")
HANDOFF_DECISIONS = (
    "handoff_required", "handoff_reasonable", "handoff_not_required",
    "handoff_unreasonable", "manual_review",
)
HANDOFF_REASON_TYPES = (
    "玩家明确要求人工", "需要人工权限或后台处理", "需要身份或资料核验",
    "敏感或高风险事项", "AI方案无效或问题持续", "投诉或情绪升级",
    "超出AI安全处理范围", "可由AI继续处理", "证据不足",
    "玩家消息包含附件", "玩家发送表情贴纸",
)
MUST_HANDOFF_REASON_TYPES = ("玩家消息包含附件", "玩家发送表情贴纸")
DECISION_VALIDATION_STATUS = {
    "candidate_ready": "player_validated",
    "candidate_needs_enrichment": "player_validated",
    "candidate_pending_feedback": "unvalidated",
    "manual_review": "unvalidated",
    "not_candidate": "not_applicable",
    "no_human_answer": "not_applicable",
}


class AnalysisProtocolError(RuntimeError):
    """MaaS responded, but the body does not satisfy the slice contract."""

    def __init__(self, message: str, *, code: str = "invalid_maas_response", raw_response: str | None = None,
                 response_metadata: dict | None = None):
        super().__init__(message)
        self.code = code
        self.raw_response = raw_response
        self.response_metadata = response_metadata or {}


QC_SYSTEM_PROMPT = """你是一名专业的海外AI客服质量检测员。你的任务是找出AI客服回复中**真正存在**的问题，避免误判。

【核心原则：宁可漏判，不可误判】
- 只有明确、确定的问题才标记为问题，模棱两可的情况放入"需人工复核"。
- AI礼貌打招呼（"Hello！"/"Hi there！"等问候语）不是问题。
- AI要求用户提供截图/视频以便排查技术问题，不是问题。
- AI说明邮箱无法更改等系统限制，并告知等待人工客服，不是问题。
- AI在本轮对话已回答过的问题再次被问时重复回答，不是问题。
- auto_reply 系统自动回复不在质检范围内。
- 若上下文不完整无法确认是否有问题，标记为"需人工复核"，不要强行判错。

【严格的问题判断标准】
只在以下情况才标记问题：
- AI提供了**明显错误**的游戏信息（如错误的活动日期、错误的充值比例等）
- AI**明确承诺**了一件它无法或不应承诺的事情
- AI**完全答非所问**，对用户诉求毫无回应
- AI应该转人工但没有转：仅限于以下两种情况
  * 用户**明确说出**"转人工""要求真人""contact human/real agent"等话语
  * 用户情绪**极度激动**（出现强烈抱怨、威胁投诉、反复表达强烈不满）
- AI回复出现**严重语法错误或乱码**，影响理解
- AI**重复发送完全相同的回复**（不是相似，是完全一样）

【不是问题的情况（必须跳过）】
- 普通问候语、开场白
- 请用户提供截图/账号/信息以便核实
- 说明系统限制（不可改邮箱、服务器已关闭等）
- 推荐用户联系人工客服
- 简洁的确认/感谢回复
- 套话模板——除非完全与用户问题无关
- 用户没有表达不满，AI回复符合场景

【问题严重程度】
- 严重：提供错误信息、做出无法兑现的承诺
- 中级：完全答非所问、本应转人工却未转
- 一般：格式或语气小问题
- 需人工复核：信息不完整，无法确认

【输出格式（仅输出JSON，禁止其他文字）】
{
  "has_issue": true/false,
  "issues": [
    {
      "issue_type": "问题大类，只能从以下7类选择：" + "、".join(ISSUE_CATEGORIES),
      "severity": "严重|中级|一般|需人工复核",
      "ai_sentence_orig": "AI回复中有问题的具体句子（原文）",
      "ai_sentence_cn": "该句子的中文翻译",
      "reason": "明确说明为什么这是问题，不要模糊表述",
      "suggestion": "具体改进建议",
      "revised_reply": "修改后的建议回复（原语言）",
      "revised_reply_cn": "修改建议的中文翻译"
    }
  ]
}

如果没有问题，issues为空数组，has_issue为false。"""


def _require_confidence(value, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= float(value) <= 1:
        raise AnalysisProtocolError(f"{field} 必须是 0 到 1 之间的数字")
    return float(value)


def _require_message_ids(value, field: str, message_roles: dict[str, str], allowed_roles: set[str] | None = None) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
        raise AnalysisProtocolError(f"{field} 必须是消息 ID 数组")
    missing = [x for x in value if x not in message_roles]
    if missing:
        raise AnalysisProtocolError(f"{field} 引用了不存在的 message_id: {missing[:3]}")
    if allowed_roles is not None:
        invalid = [x for x in value if message_roles[x] not in allowed_roles]
        if invalid:
            raise AnalysisProtocolError(f"{field} 引用了错误角色的消息: {invalid[:3]}")
    seen = []
    for item in value:
        if item not in seen:
            seen.append(item)
    return seen


def _require_bool(value, field: str) -> bool:
    if not isinstance(value, bool):
        raise AnalysisProtocolError(f"{field} 必须是布尔值")
    return value


def _optional_text(value):
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _normalize_content_type_token(value) -> str:
    if value is None or isinstance(value, bool):
        return ""
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]
    return text


def _slice_has_transfer_form_event(messages) -> bool:
    """True when the slice carries an in-app transfer-form submitted event."""
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        event_type = str(message.get("event_type") or "").strip().lower()
        if event_type == "transfer_form_submitted":
            return True
        if _normalize_content_type_token(message.get("content_type")) == "3":
            return True
    return False


_TRANSFER_PROCESS_MARKERS = (
    "转接", "已转", "登记", "记录了您的问题", "记录了你的问题", "已记录您的问题", "已记录你的问题",
    "上线后", "优先处理", "已受理", "排队",
    "おつなぎ", "つなぎしました",
    "transferred you", "registered your", "when an agent is online", "when the operator",
    "handled with priority",
    "зарегистрировал",
)


def _message_body(message: dict) -> str:
    return str(message.get("text") or message.get("content") or "")


def _slice_has_transfer_like_system_message(messages) -> bool:
    """True when a system/auto_reply body looks like transfer or offline queue."""
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        speaker = str(message.get("speaker") or "")
        is_system = speaker == "system" or message.get("is_auto")
        if not is_system:
            continue
        blob = _message_body(message).lower()
        if any(marker.lower() in blob for marker in _TRANSFER_PROCESS_MARKERS):
            return True
    return False


def _slice_has_visible_transfer_process(message_roles: dict[str, str], messages=None) -> bool:
    """Allow occurred=true without AI only when a transfer action is visible.

    Greeting auto_replies such as “我是 AI 助手” are speaker=system but are
    not a transfer. Pure player+human still returns False.
    """
    del message_roles
    if _slice_has_transfer_form_event(messages):
        return True
    return _slice_has_transfer_like_system_message(messages)


def _validate_human_handoff(handoff, message_roles: dict[str, str], payload_messages=None) -> dict:
    if not isinstance(handoff, dict) or handoff.get("decision") not in HANDOFF_DECISIONS:
        raise AnalysisProtocolError("human_handoff.decision 非法或缺失")
    decision = handoff["decision"]
    occurred = _require_bool(handoff.get("handoff_occurred"), "human_handoff.handoff_occurred")
    has_ai = any(role == "ai" for role in message_roles.values())
    if occurred and not has_ai and not _slice_has_visible_transfer_process(message_roles, payload_messages):
        raise AnalysisProtocolError("无 AI、无转接表单、也无系统转接过程时不得判定已发生转人工")
    reason_type = handoff.get("reason_type")
    if reason_type not in HANDOFF_REASON_TYPES:
        raise AnalysisProtocolError("human_handoff.reason_type 非法或缺失")
    has_human = any(role == "human_agent" for role in message_roles.values())
    if decision == "handoff_required" and has_human:
        raise AnalysisProtocolError("已有人工客服消息时不得判定应转未转")
    if decision == "handoff_required" and occurred:
        raise AnalysisProtocolError("handoff_required 时必须尚未转人工")
    if decision == "handoff_reasonable" and not occurred:
        raise AnalysisProtocolError("handoff_reasonable 时必须已经转人工")
    if decision == "handoff_not_required" and occurred:
        raise AnalysisProtocolError("handoff_not_required 时必须尚未转人工")
    if decision == "handoff_unreasonable" and not occurred:
        raise AnalysisProtocolError("handoff_unreasonable 时必须已经转人工")
    if reason_type in MUST_HANDOFF_REASON_TYPES and decision not in ("handoff_required", "handoff_reasonable"):
        raise AnalysisProtocolError("附件或表情贴纸类只能判定为应转或合理转")
    needs_review = _require_bool(handoff.get("needs_manual_review"), "human_handoff.needs_manual_review")
    review_reason = _optional_text(handoff.get("manual_review_reason"))
    if decision == "manual_review":
        if not needs_review or not review_reason:
            raise AnalysisProtocolError("human_handoff 需人工复核时必须填写原因")
    elif needs_review or review_reason:
        raise AnalysisProtocolError("非人工复核时不得填写 human_handoff.manual_review_reason")
    evidence_ids = _require_message_ids(
        handoff.get("evidence_message_ids", []), "human_handoff.evidence_message_ids", message_roles,
    )
    if reason_type in MUST_HANDOFF_REASON_TYPES and not any(message_roles.get(mid) == "player" for mid in evidence_ids):
        raise AnalysisProtocolError("附件或表情贴纸类必须引用玩家消息作为证据")
    return {
        "decision": decision,
        "handoff_occurred": occurred,
        "reason_type": reason_type,
        "confidence": _require_confidence(handoff.get("confidence"), "human_handoff.confidence"),
        "evidence_message_ids": evidence_ids,
        "reason": handoff.get("reason") if isinstance(handoff.get("reason"), str) else "",
        "needs_manual_review": needs_review,
        "manual_review_reason": review_reason,
    }


def _validate_term_suggestions(terms_block, message_roles: dict[str, str], *, allow_terms: bool) -> dict:
    if not isinstance(terms_block, dict) or not isinstance(terms_block.get("has_terms"), bool):
        raise AnalysisProtocolError("term_suggestions.has_terms 必须是布尔值")
    terms = terms_block.get("terms")
    if not isinstance(terms, list):
        raise AnalysisProtocolError("term_suggestions.terms 必须是数组")
    if terms_block["has_terms"] != bool(terms):
        raise AnalysisProtocolError("term_suggestions.has_terms 与 terms 是否为空不一致")
    if not allow_terms and terms:
        raise AnalysisProtocolError("非知识候选不得输出术语建议")
    if len(terms) > 3:
        raise AnalysisProtocolError("term_suggestions.terms 最多 3 条")
    normalized = []
    for index, term in enumerate(terms):
        if not isinstance(term, dict):
            raise AnalysisProtocolError(f"term_suggestions.terms[{index}] 必须是对象")
        text_value = _optional_text(term.get("text")) or _optional_text(term.get("suggested_standard_term"))
        zh_cn = _optional_text(term.get("zh_cn")) or _optional_text(term.get("zh_cn_meaning")) or text_value
        if not text_value:
            raise AnalysisProtocolError(f"term_suggestions.terms[{index}] 缺少术语名称")
        normalized.append({"text": text_value, "zh_cn": zh_cn or text_value})
    return {"has_terms": bool(normalized), "terms": normalized}


def _validate_knowledge_suggestion(knowledge, message_roles: dict[str, str]) -> dict:
    if not isinstance(knowledge, dict) or knowledge.get("decision") not in KNOWLEDGE_DECISIONS:
        raise AnalysisProtocolError("knowledge_suggestion.decision 非法或缺失")
    decision = knowledge["decision"]
    if knowledge.get("answer_source") != "human_agent":
        raise AnalysisProtocolError("knowledge_suggestion.answer_source 必须是 human_agent")
    expected_validation = DECISION_VALIDATION_STATUS[decision]
    validation_status = knowledge.get("validation_status") or expected_validation
    if validation_status not in KNOWLEDGE_VALIDATION_STATUSES:
        raise AnalysisProtocolError("knowledge_suggestion.validation_status 非法")
    if validation_status != expected_validation:
        raise AnalysisProtocolError("knowledge_suggestion.validation_status 与 decision 不一致")
    is_candidate = knowledge.get("is_candidate")
    expected_candidate = decision in CANDIDATE_KNOWLEDGE_DECISIONS
    if is_candidate is None:
        is_candidate = expected_candidate
    if not isinstance(is_candidate, bool) or is_candidate != expected_candidate:
        raise AnalysisProtocolError("knowledge_suggestion.is_candidate 必须与 decision 一致")
    question_ids = _require_message_ids(knowledge.get("question_message_ids", []), "knowledge_suggestion.question_message_ids", message_roles, {"player"})
    answer_ids = _require_message_ids(knowledge.get("answer_message_ids", []), "knowledge_suggestion.answer_message_ids", message_roles, {"human_agent"})
    feedback_ids = _require_message_ids(knowledge.get("feedback_message_ids", []), "knowledge_suggestion.feedback_message_ids", message_roles, {"player"})
    evidence_ids = _require_message_ids(knowledge.get("evidence_message_ids", []), "knowledge_suggestion.evidence_message_ids", message_roles)
    needs_review = _require_bool(knowledge.get("needs_manual_review"), "knowledge_suggestion.needs_manual_review")
    review_reason = _optional_text(knowledge.get("manual_review_reason"))
    reject_reason = _optional_text(knowledge.get("reject_reason"))
    category = knowledge.get("category")
    title = _optional_text(knowledge.get("title"))
    questions = knowledge.get("standard_questions") if isinstance(knowledge.get("standard_questions"), list) else []
    questions = [item for item in questions if isinstance(item, str) and item.strip()]
    standard_answer = _optional_text(knowledge.get("standard_answer"))
    keywords = knowledge.get("keywords") if isinstance(knowledge.get("keywords"), list) else []
    keywords = [item for item in keywords if isinstance(item, str) and item.strip()]
    scope = knowledge.get("applicable_scope") if isinstance(knowledge.get("applicable_scope"), dict) else {}
    applicable_scope = {
        "channel": _optional_text(scope.get("channel")),
        "game": _optional_text(scope.get("game")),
        "region": _optional_text(scope.get("region")),
    }
    has_human = any(role == "human_agent" for role in message_roles.values())
    if decision != "no_human_answer" and not has_human:
        raise AnalysisProtocolError("切片没有人工客服消息，knowledge decision 必须是 no_human_answer")
    if decision == "no_human_answer":
        if answer_ids or question_ids or feedback_ids or evidence_ids:
            raise AnalysisProtocolError("no_human_answer 不得引用问答或证据消息")
        if category is not None or title or questions or standard_answer or keywords:
            raise AnalysisProtocolError("no_human_answer 不得填写知识候选内容")
        if needs_review or review_reason:
            raise AnalysisProtocolError("no_human_answer 不得标记人工复核")
        reject_reason = None
        category = None
        title = None
        questions = []
        standard_answer = None
        keywords = []
    elif decision in CANDIDATE_KNOWLEDGE_DECISIONS:
        if category not in KNOWLEDGE_CATEGORIES:
            raise AnalysisProtocolError("候选知识分类非法或缺失")
        if not title or not questions or not standard_answer:
            raise AnalysisProtocolError("候选知识必须包含标题、标准问题和标准答案")
        if not answer_ids:
            raise AnalysisProtocolError("候选知识必须引用人工客服回复")
        if reject_reason:
            raise AnalysisProtocolError("候选知识不得填写 reject_reason")
        if decision == "candidate_pending_feedback":
            if feedback_ids:
                raise AnalysisProtocolError("待验证候选不得引用玩家正向反馈")
            if not needs_review or not review_reason:
                raise AnalysisProtocolError("待验证候选必须标记人工复核并填写原因")
        else:
            if not feedback_ids:
                raise AnalysisProtocolError("已验证候选必须引用玩家正向反馈")
            if decision == "candidate_ready" and (needs_review or review_reason):
                raise AnalysisProtocolError("可直接沉淀不得标记人工复核")
            if decision == "candidate_needs_enrichment" and not _optional_text(knowledge.get("reason")):
                raise AnalysisProtocolError("需补充后沉淀必须说明待补充内容")
    elif decision == "not_candidate":
        if not reject_reason:
            raise AnalysisProtocolError("not_candidate 必须填写 reject_reason")
        if needs_review or review_reason:
            raise AnalysisProtocolError("not_candidate 不得标记人工复核")
    elif decision == "manual_review":
        if not needs_review or not review_reason:
            raise AnalysisProtocolError("manual_review 必须填写 manual_review_reason")
        reject_reason = None
    return {
        "answer_source": "human_agent",
        "decision": decision,
        "is_candidate": is_candidate,
        "validation_status": validation_status,
        "confidence": _require_confidence(knowledge.get("confidence"), "knowledge_suggestion.confidence"),
        "category": category,
        "question_message_ids": question_ids,
        "answer_message_ids": answer_ids,
        "feedback_message_ids": feedback_ids,
        "evidence_message_ids": evidence_ids,
        "title": title,
        "standard_questions": questions,
        "standard_answer": standard_answer,
        "applicable_scope": applicable_scope,
        "keywords": keywords,
        "reason": knowledge.get("reason") if isinstance(knowledge.get("reason"), str) else "",
        "reject_reason": reject_reason,
        "needs_manual_review": needs_review,
        "manual_review_reason": review_reason,
    }


def validate_slice_result(result: object, payload: dict) -> dict:
    """Validate business fields and message references before persistence."""
    if not isinstance(result, dict):
        raise AnalysisProtocolError("MaaS 返回 JSON 顶层必须是对象")
    if result.get("slice_id") != payload["slice_id"]:
        raise AnalysisProtocolError("MaaS 返回的 slice_id 与输入不一致")
    if result.get("analysis_status") not in {"completed", "partial", "failed"}:
        raise AnalysisProtocolError("analysis_status 非法")
    if result["analysis_status"] == "failed":
        return result

    message_roles = {x["message_id"]: x["speaker"] for x in payload["messages"]}
    # The model may enrich language and translation only. Original speaker,
    # text, sequence and timestamps remain system-owned facts from Excel.
    returned_messages = result.get("messages")
    if not isinstance(returned_messages, list):
        raise AnalysisProtocolError("messages 必须是每条输入消息对应的翻译结果数组")
    by_id = {}
    for index, message in enumerate(returned_messages):
        if not isinstance(message, dict) or not isinstance(message.get("message_id"), str):
            raise AnalysisProtocolError(f"messages[{index}] 缺少 message_id")
        message_id = message["message_id"]
        if message_id not in message_roles or message_id in by_id:
            raise AnalysisProtocolError(f"messages[{index}].message_id 非法或重复")
        translation = message.get("translation")
        if not isinstance(translation, dict) or translation.get("status") not in TRANSLATION_STATUSES:
            raise AnalysisProtocolError(f"messages[{index}].translation.status 非法或缺失")
        if translation.get("target_language") != "zh-CN":
            raise AnalysisProtocolError(f"messages[{index}].translation.target_language 必须是 zh-CN")
        if translation.get("status") in {"success", "uncertain"} and not isinstance(translation.get("translated_text"), str):
            raise AnalysisProtocolError(f"messages[{index}] 翻译成功时必须提供 translated_text")
        by_id[message_id] = message
    if set(by_id) != set(message_roles):
        raise AnalysisProtocolError("messages 必须覆盖输入中的每一条消息")
    # Build an immutable transcript from the input, carrying forward only the
    # permitted MaaS enrichment fields. This prevents any accidental content
    # truncation or role rewrite in the model response from reaching the UI.
    result["messages"] = [{
        **source,
        "source_language": by_id[source["message_id"]].get("source_language"),
        "translation": by_id[source["message_id"]]["translation"],
    } for source in payload["messages"]]
    quality = result.get("quality_check")
    if not isinstance(quality, dict) or not isinstance(quality.get("has_issue"), bool):
        raise AnalysisProtocolError("quality_check.has_issue 必须是布尔值")
    issues = quality.get("issues")
    if not isinstance(issues, list) or len(issues) > 3:
        raise AnalysisProtocolError("quality_check.issues 必须是最多 3 项的数组")
    if quality["has_issue"] != bool(issues):
        raise AnalysisProtocolError("quality_check.has_issue 与 issues 是否为空不一致")

    normalized_issues = []
    for index, issue in enumerate(issues):
        if not isinstance(issue, dict):
            raise AnalysisProtocolError(f"issues[{index}] 必须是对象")
        if issue.get("issue_type") not in ISSUE_CATEGORIES:
            raise AnalysisProtocolError(f"issues[{index}].issue_type 非法")
        if issue.get("severity") not in SEVERITIES:
            raise AnalysisProtocolError(f"issues[{index}].severity 非法")
        item = dict(issue)
        item["confidence"] = _require_confidence(issue.get("confidence"), f"issues[{index}].confidence")
        item["ai_message_ids"] = _require_message_ids(issue.get("ai_message_ids"), f"issues[{index}].ai_message_ids", message_roles, {"ai"})
        item["evidence_message_ids"] = _require_message_ids(issue.get("evidence_message_ids"), f"issues[{index}].evidence_message_ids", message_roles)
        normalized_issues.append(item)
    normalized_issues.sort(key=lambda x: x["confidence"], reverse=True)
    quality["issues"] = normalized_issues

    result["human_handoff"] = _validate_human_handoff(result.get("human_handoff"), message_roles, payload.get("messages"))
    knowledge = _validate_knowledge_suggestion(result.get("knowledge_suggestion"), message_roles)
    result["knowledge_suggestion"] = knowledge
    result["term_suggestions"] = _validate_term_suggestions(
        result.get("term_suggestions") or {"has_terms": False, "terms": []},
        message_roles,
        allow_terms=knowledge["decision"] in CANDIDATE_KNOWLEDGE_DECISIONS,
    )
    if not isinstance(result.get("warnings"), list):
        result["warnings"] = []
    if not isinstance(result.get("errors"), list):
        result["errors"] = []
    return result


async def analyze_slice(payload: dict) -> dict:
    """Send exactly one already-prepared Excel slice to MaaS."""
    if not isinstance(payload, dict) or not isinstance(payload.get("messages"), list) or not payload["messages"]:
        raise AnalysisProtocolError("切片输入缺少 messages", code="invalid_slice_input")
    if not isinstance(payload.get("slice_id"), str) or not payload["slice_id"].strip():
        raise AnalysisProtocolError("切片输入缺少 slice_id", code="invalid_slice_input")
    if "language" in payload:
        raise AnalysisProtocolError("MaaS 输入不得包含 language", code="invalid_slice_input")
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # MaaS uses the beginning of the user message as the searchable session
    # title. The title is transport-only; the slice JSON contract stays intact.
    user_prompt = f"slice_id={payload['slice_id']}\n\n{body}"
    try:
        completion = await chat_completion(
            [{"role": "user", "content": user_prompt}],
            timeout=config.MAAS_TIMEOUT_SECONDS,
        )
    except MaaSClientError as exc:
        raise AnalysisProtocolError(
            str(exc), code=exc.code, raw_response=exc.raw_response,
            response_metadata=exc.response_metadata,
        ) from exc
    response_text = completion.content
    response_metadata = completion.response_metadata
    cleaned = response_text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        cleaned = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise AnalysisProtocolError(
            "MaaS 返回不是合法 JSON", raw_response=response_text,
            response_metadata=response_metadata,
        ) from exc
    try:
        validated = validate_slice_result(result, payload)
    except AnalysisProtocolError as exc:
        exc.raw_response = response_text
        exc.response_metadata = response_metadata
        raise
    validated["_raw_response"] = response_text
    validated["_response_metadata"] = response_metadata
    return validated


async def analyze_ai_message(ai_content: str, context: str, game: str, channel: str, language: str) -> dict:
    """Legacy compatibility wrapper; new batches call :func:`analyze_slice`."""
    try:
        payload = json.loads(context)
    except (TypeError, json.JSONDecodeError) as exc:
        raise AnalysisProtocolError("旧 AI 消息没有合法切片上下文", code="legacy_context_invalid") from exc
    payload.pop("language", None)
    return await analyze_slice(payload)


REPORT_SUMMARY_PROMPT = """你是AI客服质检分析专家。请根据以下质检数据，生成一段200字以内的总结分析，重点说明：
1. 主要问题类型和规律
2. 最需要优先改进的方向
3. 整体表现判断（优秀/良好/待改进/较差）

只输出分析文字，不要标题和格式符号。"""


async def generate_report_summary(stats: dict) -> str:
    """根据统计数据生成报告摘要，MaaS 失败或返回 JSON 时用本地模板"""

    def _local_summary(stats: dict) -> str:
        total  = stats.get("total_issues", 0)
        severe = stats.get("severe", 0)
        medium = stats.get("medium", 0)
        general = stats.get("general", 0)
        review = stats.get("review", 0)
        sessions = stats.get("affected_sessions", 0)
        msgs   = stats.get("total_ai_msgs", 0)
        tops   = stats.get("top_issues", [])

        if total == 0:
            level = "本次质检未发现明确问题，AI客服整体表现良好。"
        elif severe > 0:
            level = f"发现{severe}条严重问题，需立即处理，整体表现**较差**。"
        elif medium > 5:
            level = f"中级问题较多（{medium}条），整体表现**待改进**。"
        elif medium > 0:
            level = f"存在一定数量中级问题（{medium}条），整体表现**良好但需关注**。"
        else:
            level = f"仅有一般性问题（{general}条），整体表现**良好**。"

        tops_str = "、".join(tops[:3]) if tops else "暂无"
        return (
            f"本次共质检AI消息 {msgs} 条，发现问题 {total} 条，"
            f"涉及 {sessions} 个会话。"
            f"其中严重 {severe} 条、中级 {medium} 条、一般 {general} 条、需人工复核 {review} 条。"
            f"主要问题类型：{tops_str}。{level}"
        )

    user_prompt = f"""质检统计数据：
- 分析AI消息总数：{stats.get('total_ai_msgs', 0)}
- 发现问题总数：{stats.get('total_issues', 0)}
- 涉及会话数：{stats.get('affected_sessions', 0)}
- 严重问题：{stats.get('severe', 0)}条
- 中级问题：{stats.get('medium', 0)}条
- 一般问题：{stats.get('general', 0)}条
- 需人工复核：{stats.get('review', 0)}条
- 主要问题类型：{', '.join(stats.get('top_issues', []))}
- 涵盖渠道：{', '.join(stats.get('channels', []))}
- 涵盖游戏：{', '.join(stats.get('games', []))}

请用中文写一段150字以内的总结，直接输出文字，禁止输出JSON或代码格式。"""

    try:
        result = await chat_completion(
            [{"role": "user", "content": user_prompt}],
            timeout=30,
        )
        # 如果 MaaS 返回了 JSON（被系统提示词影响），直接用本地模板
        stripped = result.content.strip()
        if stripped.startswith("{") or stripped.startswith("```"):
            return _local_summary(stats)
        return stripped
    except Exception:
        return _local_summary(stats)
