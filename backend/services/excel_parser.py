"""
Excel 解析服务
支持 DC / FB / LINE / VK / VIP后台 五种渠道格式
"""
import re
import json
import numbers
import os
from html.parser import HTMLParser
import pandas as pd
from datetime import datetime
from typing import Optional


# The overseas in-app export is already a session-oriented feed.  These
# limits are deliberately conservative and configurable; splitting only ever
# happens between complete messages, never in the middle of message text.
OVERSEAS_MAX_MESSAGES = max(int(os.getenv("OVERSEAS_MAX_MESSAGES", "200")), 1)
OVERSEAS_MAX_CHARS = max(int(os.getenv("OVERSEAS_MAX_CHARS", "120000")), 1000)
OVERSEAS_NEW_COLUMNS = {"sessionId", "senderType", "createdAt"}
OVERSEAS_TEXT_COLUMNS = {"text", "content"}
TRANSFER_FORM_EVENTS = {
    "1": ("transfer_form_started", "转人工表单-引导填写"),
    "3": ("transfer_form_submitted", "转人工表单-已提交并生成工单"),
}
MBACKEND_SHEETS = {"高价值会话", "低价值会话"}
MBACKEND_REQUIRED = {"地区", "游戏", "问题ID", "回复人", "对话记录"}


class _MBackendHtmlCleaner(HTMLParser):
    """Turn presentation HTML into the exact text a reviewer/MaaS should read."""
    BLOCK_TAGS = {"p", "div", "section", "li", "tr", "ul", "ol"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.link_href: list[str] = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs = dict(attrs)
        if tag in self.BLOCK_TAGS or tag == "br": self.parts.append("\n")
        elif tag == "img": self.parts.append("[图片]")
        elif tag in {"attachment", "file"}: self.parts.append("[附件]")
        elif tag == "a": self.link_href.append(attrs.get("href", ""))

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in self.BLOCK_TAGS: self.parts.append("\n")
        elif tag == "a" and self.link_href:
            href = self.link_href.pop()
            if href and href not in "".join(self.parts[-2:]): self.parts.append(f" ({href})")

    def handle_data(self, data):
        self.parts.append(data)

    def value(self) -> str:
        text = "".join(self.parts).replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"[\t ]+\n", "\n", text)
        text = re.sub(r"\n[\t ]+", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def clean_mbackend_html(raw: str) -> str:
    if not raw: return ""
    parser = _MBackendHtmlCleaner()
    try:
        parser.feed(str(raw)); parser.close()
        return parser.value()
    except Exception:
        # The original text remains auditable; this fallback intentionally
        # removes only presentation tags and never rewrites message meaning.
        return re.sub(r"<[^>]+>", "", str(raw)).strip()


def _split_agents(raw: str) -> list[str]:
    result: list[str] = []
    for value in re.split(r"[、,，;；/／]+", raw or ""):
        name = value.strip()
        if name and name not in result: result.append(name)
    return result


def is_mbackend_dataframe(df: pd.DataFrame, sheet_name: str = "") -> bool:
    """Identify M-backend by columns only.

    DC/FB exports reuse the same tab names (高价值会话 / 低价值会话), so the
    sheet title must never override a standard-channel workbook.
    ``sheet_name`` is kept for call-site compatibility.
    """
    del sheet_name
    columns = {str(column).strip() for column in df.columns}
    return {"问题ID", "回复人", "对话记录"}.issubset(columns)


def _parse_mbackend_dialogue(dialogue: str, agents: list[str], warnings: list[str], row_number: int) -> list[dict]:
    """Parse timestamped M-backend messages without promoting unknown roles."""
    pattern = re.compile(r"^\s*\[([^\]]+)\]\s*([^:：\n]+)\s*[:：]\s?(.*)$")
    messages: list[dict] = []
    current = None
    for order, line in enumerate(str(dialogue or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")):
        match = pattern.match(line)
        if match:
            timestamp, source, raw_text = match.groups()
            try:
                datetime.strptime(timestamp.strip(), "%Y-%m-%d %H:%M:%S")
            except ValueError:
                warnings.append(f"第 {row_number} 行消息时间格式无效：{timestamp}")
                current = None
                continue
            source = source.strip()
            speaker = "player" if source == "用户" else "human_agent" if source in agents else "unknown"
            if speaker == "unknown": warnings.append(f"第 {row_number} 行出现未匹配回复人的角色“{source}”，已按未知角色保存")
            current = {"created_at": timestamp.strip(), "source": source, "speaker": speaker, "raw": raw_text, "order": order}
            messages.append(current)
        elif current is not None:
            current["raw"] = f"{current['raw']}\n{line}"
        elif line.strip():
            warnings.append(f"第 {row_number} 行存在无法识别的对话内容，未作为有效消息发送")
    for message in messages:
        message["text"] = clean_mbackend_html(message["raw"])
    messages = [message for message in messages if message["text"]]
    messages.sort(key=lambda message: (message["created_at"], message["order"]))
    return messages


def _map_mbackend_region(raw_region: str, game: str) -> str:
    """Resolve only explicit M-backend configuration; never infer language."""
    try:
        from database import MappingConfig, SessionLocal
        db = SessionLocal()
        try:
            rows = db.query(MappingConfig).filter(MappingConfig.enabled.is_(True)).all()
            for row in rows:
                if row.raw_region != raw_region: continue
                if row.raw_channel not in {"M后台", "官网客服"}: continue
                if row.game and row.game != game: continue
                if (row.match_field or "none") != "none": continue
                return row.target_region or ""
        finally:
            db.close()
    except Exception:
        pass
    return ""


def _parse_mbackend(df: pd.DataFrame, sheet_name: str) -> tuple[list[dict], list[dict], str | None, dict]:
    missing = sorted(MBACKEND_REQUIRED - {str(column).strip() for column in df.columns})
    stats = {"read_rows": len(df), "filtered_no_agent": 0, "parse_failed": 0, "valid_messages": 0, "valid_sessions": 0, "warnings": 0, "warning_messages": []}
    if missing:
        return [], [], f"M 后台问题列表缺少必填列: {missing}", stats
    sessions: list[dict] = []
    warnings: list[str] = []
    for row_number, (_, row) in enumerate(df.iterrows(), 2):
        raw_agents = _cell_text(row.get("回复人"))
        problem_id = _cell_text(row.get("问题ID"))
        if not raw_agents:
            stats["filtered_no_agent"] += 1; warnings.append(f"第 {row_number} 行回复人为空，已过滤，不传 MaaS")
            continue
        if not problem_id:
            stats["parse_failed"] += 1; warnings.append(f"第 {row_number} 行问题ID为空，无法创建会话")
            continue
        agents = _split_agents(raw_agents)
        row_warnings: list[str] = []
        raw_dialogue = _cell_text(row.get("对话记录"))
        messages = _parse_mbackend_dialogue(raw_dialogue, agents, row_warnings, row_number)
        if not messages or not any(message["speaker"] == "human_agent" for message in messages):
            stats["parse_failed"] += 1
            warnings.extend(row_warnings or [f"第 {row_number} 行未解析到匹配回复人的有效客服消息，已过滤"])
            continue
        raw_region = _cell_text(row.get("地区"))
        game = _cell_text(row.get("游戏"))
        # Mapping decisions are configuration-only. No language/game/file-name
        # inference is performed for the M-backend source.
        region = _map_mbackend_region(raw_region, game)
        game, region = _force_mushroom_rush_to_sea_adventure(game, region)
        if not region:
            row_warnings.append(f"第 {row_number} 行地区“{raw_region or '空'}”未映射，标准地区为空")
        slice_id = f"mbackend:{{batch_id}}:{problem_id}"
        compact_messages = [{
            "message_id": f"{slice_id}:m{index}", "speaker": message["speaker"],
            "speaker_source": message["source"], "text": message["text"],
            "created_at": message["created_at"], "sequence": index,
        } for index, message in enumerate(messages, 1)]
        raw_metadata = {key: _cell_text(row.get(key)) for key in ("提问账号", "提交时间", "问题类型", "用户消息数", "客服消息数", "评分", "问题是否解决", "关单方", "过滤原因", "客诉渠道") if key in row.index}
        raw_metadata.update({"source_sheet": sheet_name, "problem_id": problem_id, "reply_agents_raw": raw_agents, "reply_agents": agents, "raw_dialogue": raw_dialogue, "raw_messages": [{"created_at": item["created_at"], "source": item["source"], "content": item["raw"]} for item in messages]})
        payload = {"schema_version": "1.0.0", "slice_id": slice_id, "channel": "官网客服", "game": game or None, "region": region or None, "messages": compact_messages}
        warnings.extend(row_warnings); stats["valid_messages"] += len(messages); stats["valid_sessions"] += 1
        sessions.append({
            "channel": "官网客服", "game": game, "region": region, "session_uid": problem_id, "session_link": _cell_text(row.get("会话链接")),
            "reply_time": datetime.strptime(messages[-1]["created_at"], "%Y-%m-%d %H:%M:%S"), "ai_messages": [],
            "full_transcript": json.dumps(raw_metadata, ensure_ascii=False, separators=(",", ":")), "slice_id": slice_id,
            "slice_payload": payload, "_source_format": "m_backend", "_source_sheet": sheet_name,
            "_raw_region": raw_region, "_raw_channel": _cell_text(row.get("客诉渠道")), "_reply_agents": agents,
            "_parse_warnings": row_warnings, "_parse_stats": stats,
        })
    stats["warnings"] = len(warnings)
    stats["warning_messages"] = list(warnings)
    if not sessions and warnings:
        return [], [], "; ".join(warnings), stats
    return sessions, [], None, stats


# ── 渠道自动识别 ──────────────────────────────────────────────────────────────
CHANNEL_SIGNATURES = {
    "VIP":  ["角色ID", "角色名", "消息内容", "客服发言条数"],
    "DC":   ["会话链接", "对话记录", "咨询渠道"],
    "FB":   ["用户id（三方渠道的）", "对话记录"],
    "LINE": ["对话记录"],
    "VK":   ["对话记录"],
}

REGION_BY_AGENT = {
    "黄英杰": "欧美", "徐碧芸": "欧美",
    "梁楚恩": "欧美", "焦思阳": "欧美",
    "谭彩雯": "东南亚", "胡家圣": "东南亚", "杜永昌": "东南亚",
}


def _vip_agent_token(raw: str) -> str:
    value = str(raw or "").strip()
    while True:
        nxt = re.sub(r"^[^\w]+", "", value, count=1).strip()
        nxt = re.sub(r"^(?:客服|人工客服|human_agent|operator|agent)[\s\-－‐‑‒–—―:：/、]+", "", nxt, count=1, flags=re.I).strip()
        if nxt == value:
            break
        value = nxt
    return value


def resolve_vip_region(kefu_raw: str, messages=None) -> str | None:
    """Map VIP agent names to standard regions; never keep VIP后台 as a region."""
    candidates = []
    for agent in str(kefu_raw or "").replace("，", ",").replace("、", ",").split(","):
        token = _vip_agent_token(agent)
        if token:
            candidates.append(token)
    for message in messages or []:
        token = _vip_agent_token(message.get("role") or message.get("speaker_source") or "")
        if token:
            candidates.append(token)
    for token in candidates:
        if token in REGION_BY_AGENT:
            return REGION_BY_AGENT[token]
        for name, region in REGION_BY_AGENT.items():
            if name and name in token:
                return region
    return None

VIP_AI_MARKERS = [
    "This reply was provided by the AI",
    "Cette réponse est fournie par",
    "Bu yanıt AI",
    "Этот ответ предоставлен AI",
    "Phản hồi này được cung cấp bởi",
    "本条回复由AI",
    "Esta respuesta fue proporcionada por",
    "Questa risposta è stata fornita da",
]

# ── KB 问题分类关键词 ───────────────────────────────────────────────────────────
KB_TOPIC_RULES = [
    ("账号绑定",   ["discord", "绑定", "bind", "linked", "account link", "привязк"]),
    ("账号问题",   ["账号", "account", "аккаунт", "compte", "cuenta"]),
    ("登录问题",   ["登录", "log in", "login", "cannot login", "sign in", "unable to log", "не могу войти"]),
    ("充值问题",   ["充值", "recharge", "payment", "purchase", "buy", "пополн", "paiement"]),
    ("合服查询",   ["合服", "server merge", "merge server", "слияние", "fusion", "fusión"]),
    ("Bug反馈",   ["bug", "glitch", "issue", "crash", "error", "probl", "баг", "ошибк"]),
    ("活动查询",   ["活动", "event", "événement", "evento", "событи", "sự kiện"]),
    ("退款问题",   ["退款", "refund", "reembolso", "remboursement", "возврат"]),
    ("礼包兑换",   ["礼包", "gift code", "code", "redeem", "exchange", "code cadeau"]),
    ("角色问题",   ["角色", "character", "персонаж", "personnage"]),
    ("客服咨询",   ["客服", "support", "помощь", "aide", "ayuda", "nhân viên"]),
]


def classify_kb_topic(text: str) -> str:
    """根据文本关键词自动返回简短分类标签"""
    t = text.lower()
    for label, keywords in KB_TOPIC_RULES:
        if any(kw in t for kw in keywords):
            return label
    return "其他咨询"

SATISFIED_KEYWORDS = [
    'thank', 'thanks', 'ok', 'okay', 'got it', 'understand', 'understood',
    'i see', 'perfect', 'great', 'awesome', 'helpful', 'noted', 'received',
    'cảm ơn', 'merci', 'gracias', 'спасибо', 'danke', 'teşekkür',
    '谢谢', '明白', '好的', '了解', '收到', '感谢', '👍', '🙏', '😊', '✓', '✅',
]


def is_satisfied(text: str) -> bool:
    t = text.lower().strip()
    return any(kw in t for kw in SATISFIED_KEYWORDS)


def extract_human_kb_suggestions(
    messages: list[dict],
    game: str,
    region: str,
    channel: str,
) -> list[dict]:
    """
    从对话消息列表中提取「人工客服回答→用户满意」的KB条目。
    只保留真正的人工客服回复（非AI、非auto_reply）。
    """
    suggestions = []
    for idx, msg in enumerate(messages):
        if msg.get("is_user") or msg.get("is_ai") or msg.get("is_auto"):
            continue
        if _message_speaker(msg) != "human_agent":
            continue
        # 这是一条人工客服回复
        agent_reply = msg["content"].strip()
        if not agent_reply or len(agent_reply) < 10:
            continue
        # 检查后续1-2条是否有用户满意表达
        for nxt in messages[idx + 1: idx + 3]:
            if nxt.get("is_user") and is_satisfied(nxt["content"]):
                # 找前面最近的用户提问
                prev_question = ""
                for prev in reversed(messages[max(0, idx - 4): idx]):
                    if prev.get("is_user"):
                        prev_question = prev["content"].strip()
                        break
                if prev_question:
                    suggestions.append({
                        "game": game,
                        "region": region,
                        "channel": channel,
                        "question": prev_question,
                        "answer": agent_reply,
                        "satisfied_expr": nxt["content"][:100],
                        "agent_name": msg.get("role", ""),
                        "topic": classify_kb_topic(prev_question + " " + agent_reply),
                    })
                break
    return suggestions


def is_overseas_dataframe(df: pd.DataFrame) -> bool:
    """Recognise the overseas in-app export without falling through to legacy parsing."""
    columns = {str(column).strip() for column in df.columns}
    return OVERSEAS_NEW_COLUMNS.issubset(columns) and bool(columns & OVERSEAS_TEXT_COLUMNS)


def _cell_text(value) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, numbers.Integral):
        return str(int(value))
    if isinstance(value, numbers.Real):
        number = float(value)
        return str(int(number)) if number.is_integer() else str(value)
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]
    return str(value)


def normalize_content_type(value) -> str:
    """Normalize overseas contentType without forcing unknown values into 0/1/2/3."""
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if not text:
        return ""
    try:
        number = float(text)
    except (TypeError, ValueError):
        return text
    if number in {0.0, 1.0, 2.0, 3.0} and number == int(number):
        return str(int(number))
    return text


def _readable_structured_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return _cell_text(value)


def structured_content_to_readable_text(data) -> str:
    """Turn form JSON into labeled text without dumping the raw object as the body."""
    if data is None:
        return ""
    if isinstance(data, list):
        lines = [_readable_structured_value(item) for item in data]
        return "\n".join(line for line in lines if line)
    if not isinstance(data, dict):
        return _cell_text(data)
    lines = []
    for key, value in data.items():
        readable = _readable_structured_value(value)
        if readable == "":
            continue
        lines.append(f"{key}：{readable}")
    return "\n".join(lines)


def _overseas_raw_content(row: pd.Series) -> str:
    if "content" in row.index and _cell_text(row.get("content")):
        raw = row.get("content")
        return json.dumps(raw, ensure_ascii=False) if isinstance(raw, (dict, list)) else _cell_text(raw)
    if "text" in row.index and _cell_text(row.get("text")):
        return _cell_text(row.get("text"))
    return ""


def _overseas_body(row: pd.Series, warnings: list[str], row_number: int) -> str:
    """Extract text from text/content while preserving malformed raw content."""
    if "text" in row.index and _cell_text(row.get("text")):
        return _cell_text(row.get("text"))
    raw = row.get("content") if "content" in row.index else None
    raw_text = _cell_text(raw)
    if not raw_text:
        return ""
    try:
        decoded = raw if isinstance(raw, dict) else json.loads(raw_text)
        if isinstance(decoded, dict):
            for key in ("text", "content"):
                if decoded.get(key) is not None:
                    return _cell_text(decoded.get(key))
    except Exception:
        warnings.append(f"第 {row_number} 行 content 不是有效 JSON，已保留原始内容")
    return raw_text


def _overseas_transfer_form_fields(row: pd.Series, content_type: str, warnings: list[str], row_number: int) -> dict:
    event_type, event_label = TRANSFER_FORM_EVENTS[content_type]
    raw_content = _overseas_raw_content(row)
    structured_content = None
    readable = ""
    if raw_content:
        raw_value = row.get("content") if "content" in row.index and _cell_text(row.get("content")) else raw_content
        try:
            decoded = raw_value if isinstance(raw_value, (dict, list)) else json.loads(raw_content)
            if isinstance(decoded, (dict, list)):
                structured_content = decoded
                readable = structured_content_to_readable_text(decoded)
            else:
                readable = _cell_text(decoded)
        except Exception:
            warnings.append(f"第 {row_number} 行 contentType={content_type} 的 content 不是有效 JSON，已保留原始内容")
            readable = raw_content
    display_body = readable or raw_content
    display_text = f"[{event_label}] {display_body}".rstrip() if display_body else f"[{event_label}]"
    return {
        "text": display_text,
        "event_type": event_type,
        "event_label": event_label,
        "raw_content": raw_content,
        "structured_content": structured_content,
    }


def _overseas_created_at(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)) or pd.isna(value):
        return ""
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return str(value).strip()


def _overseas_sort_key(message: dict):
    value = message.get("created_at") or ""
    try:
        parsed = pd.to_datetime(value, errors="raise")
        return (0, parsed.to_datetime64(), str(message.get("_source_id") or ""))
    except Exception:
        return (1, str(value), str(message.get("_source_id") or ""))


def _parse_overseas(df: pd.DataFrame, shared_seen: set[str] | None = None) -> tuple[list[dict], list[dict], str | None]:
    """Convert overseas rows to the existing compact slice protocol."""
    missing = sorted(OVERSEAS_NEW_COLUMNS - {str(c).strip() for c in df.columns})
    if missing:
        return [], [], f"海外端内普客格式缺少关键字段: {missing}"
    if not ({"text", "content"} & {str(c).strip() for c in df.columns}):
        return [], [], "海外端内普客格式缺少 text 或 content 字段"

    groups: dict[str, list[dict]] = {}
    seen_ids: set[str] = shared_seen if shared_seen is not None else set()
    warnings: list[str] = []
    dedup_rows = 0
    valid_messages = 0
    for row_index, (_, row) in enumerate(df.iterrows(), 2):
        source_id = _cell_text(row.get("_id")) if "_id" in row.index else ""
        session_id = _cell_text(row.get("sessionId"))
        fallback = ""
        for field in ("connectionId", "uid", "_id"):
            candidate = _cell_text(row.get(field)) if field in row.index else ""
            if candidate:
                fallback = candidate
                break
        session_key = session_id or fallback
        if not session_id:
            warnings.append(f"第 {row_index} 行 sessionId 为空，已按 {('connectionId' if _cell_text(row.get('connectionId')) else 'uid' if _cell_text(row.get('uid')) else '_id')} 降级")
        if not session_key:
            warnings.append(f"第 {row_index} 行缺少 sessionId、connectionId、uid、_id，已跳过")
            continue

        content_type = normalize_content_type(row.get("contentType") if "contentType" in row.index else "")
        if content_type and content_type not in {"0", "1", "2", "3"}:
            warnings.append(f"第 {row_index} 行 contentType 为“{content_type}”，已按原值保留")
        if content_type in TRANSFER_FORM_EVENTS:
            form_fields = _overseas_transfer_form_fields(row, content_type, warnings, row_index)
            body = form_fields["text"]
        else:
            form_fields = {
                "text": "",
                "event_type": None,
                "event_label": None,
                "raw_content": None,
                "structured_content": None,
            }
            body = _overseas_body(row, warnings, row_index)
        sender_raw = _cell_text(row.get("senderType"))
        speaker = "player" if sender_raw in {"1", "1.0"} else "ai" if sender_raw in {"2", "2.0"} else "unknown"
        # _id is the preferred duplicate key; only use the complete fallback
        # tuple when the export omitted it.
        fallback_key = "|".join((session_key, _overseas_created_at(row.get("createdAt")), sender_raw, content_type, form_fields.get("raw_content") or body))
        dedup_key = f"id:{source_id}" if source_id else f"fallback:{fallback_key}"
        if dedup_key in seen_ids:
            dedup_rows += 1
            continue
        seen_ids.add(dedup_key)
        if body:
            valid_messages += 1
        else:
            warnings.append(f"第 {row_index} 行消息正文为空，未发送至 MaaS")
        game_raw = _cell_text(row.get("gameProductId"))
        created_at = _overseas_created_at(row.get("createdAt"))
        groups.setdefault(session_key, []).append({
            "_source_id": source_id,
            "_dedup_key": dedup_key,
            "created_at": created_at,
            "speaker": speaker,
            "speaker_source": sender_raw or None,
            "text": body,
            "content_type": content_type or None,
            "event_type": form_fields.get("event_type"),
            "event_label": form_fields.get("event_label"),
            "raw_content": form_fields.get("raw_content"),
            "structured_content": form_fields.get("structured_content"),
            "language": _cell_text(row.get("language")) or None,
            "game": game_raw,
        })

    sessions: list[dict] = []
    for session_key, messages in groups.items():
        messages.sort(key=_overseas_sort_key)
        game = next((m.get("game") for m in messages if m.get("game")), "")
        # There is no reliable region in this export.  Keep it empty unless a
        # configured mapping can resolve it; never infer from language/name.
        mapped_channel, region, mapped_game = _apply_mapping(None, "官网客服", game, "", "")
        if mapped_game:
            game = mapped_game
        game, region = _force_mushroom_rush_to_sea_adventure(game, region)
        valid_messages_for_session = [message for message in messages if message.get("text")]
        if not valid_messages_for_session:
            continue
        parts: list[list[dict]] = []
        current: list[dict] = []
        current_chars = 0
        for message in valid_messages_for_session:
            message_chars = len(message.get("text") or "")
            if current and (len(current) >= OVERSEAS_MAX_MESSAGES or current_chars + message_chars > OVERSEAS_MAX_CHARS):
                parts.append(current); current = []; current_chars = 0
            current.append(message); current_chars += message_chars
        if current:
            parts.append(current)
        for part_index, part in enumerate(parts, 1):
            base_id = session_key
            slice_id = base_id if len(parts) == 1 else f"{base_id}:part-{part_index}"
            compact_messages = [{
                "message_id": f"{slice_id}:m{index}",
                "speaker": message["speaker"],
                "speaker_source": message.get("speaker_source"),
                "content_type": message.get("content_type"),
                "event_type": message.get("event_type"),
                "event_label": message.get("event_label"),
                "text": message["text"],
                **({
                    "raw_content": message.get("raw_content"),
                    **({"structured_content": message.get("structured_content")} if message.get("structured_content") is not None else {}),
                } if message.get("content_type") in TRANSFER_FORM_EVENTS else {}),
                "created_at": message.get("created_at") or None,
                "sequence": index,
            } for index, message in enumerate(part, 1)]
            payload = {"schema_version": "1.0.0", "slice_id": slice_id, "channel": "官网客服", "game": game or None, "region": region or None, "messages": compact_messages}
            ai_messages = [{
                "msg_time": pd.to_datetime(message.get("created_at"), errors="coerce").to_pydatetime() if message.get("created_at") and not pd.isna(pd.to_datetime(message.get("created_at"), errors="coerce")) else None,
                "content": message["text"],
                "context": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                "language": message.get("language"),
            } for message in part if message["speaker"] == "ai"]
            sessions.append({
                "channel": "官网客服", "game": game, "region": region or "", "session_uid": slice_id,
                "session_link": "", "user_name": "", "reply_time": next((x["msg_time"] for x in reversed(ai_messages) if x["msg_time"]), None),
                "ai_messages": ai_messages, "full_transcript": json.dumps(messages, ensure_ascii=False, separators=(",", ":")),
                "slice_id": slice_id, "slice_payload": payload,
                "_source_format": "overseas_in_app",
                "_parse_warnings": list(warnings),
                "_parse_stats": {"read_rows": len(df), "dedup_rows": dedup_rows, "valid_messages": valid_messages, "warnings": len(warnings)},
            })
    return sessions, [], None


def detect_channel(columns: list[str]) -> str:
    cols_set = set(columns)
    if "角色ID" in cols_set and "消息内容" in cols_set:
        return "VIP"
    # 渠道列含 DC/FB/LINE/VK 字样
    for col in columns:
        val_hint = col.lower()
        if "discord" in val_hint or "dc" in val_hint:
            return "DC"
        if "facebook" in val_hint or "fb" in val_hint:
            return "FB"
        if "line" in val_hint:
            return "LINE"
        if "vk" in val_hint:
            return "VK"
    # 保底：有对话记录列就归到DC
    if "对话记录" in cols_set:
        return "DC"
    return "UNKNOWN"


def detect_channel_from_df(df: pd.DataFrame) -> str:
    if is_overseas_dataframe(df):
        return "官网客服"
    if is_mbackend_dataframe(df):
        return "官网客服"
    cols = list(df.columns)
    ch = detect_channel(cols)
    for col in ["客诉渠道", "咨询渠道"]:
        if col in df.columns:
            sample = " ".join(df[col].dropna().astype(str).head(20).tolist())
            if "问题列表" in sample: return "M后台"
            for name in ["DC", "FB", "LINE", "VK"]:
                if name.lower() in sample.lower(): return name
    if ch == "UNKNOWN" and "咨询渠道" in df.columns:
        sample = str(df["咨询渠道"].dropna().iloc[0]) if len(df) > 0 else ""
        for name in ["DC", "FB", "LINE", "VK"]:
            if name.lower() in sample.lower():
                return name
    return ch


# ── VIP 消息解析 ──────────────────────────────────────────────────────────────
def parse_vip_messages(content_str: str) -> list[dict]:
    """返回 [{'role', 'ts', 'content', 'is_ai', 'is_user'}]"""
    lines = str(content_str).split("\n")
    messages = []
    i = 0
    while i < len(lines):
        raw_line = lines[i]
        line = raw_line.strip()
        if not line:
            # Preserve paragraph breaks inside a wrapped message.  Excel
            # exports can use blank physical lines between continuation
            # paragraphs, and dropping them changes the original reply.
            if messages and messages[-1]["content"] and not messages[-1]["content"].endswith("\n"):
                messages[-1]["content"] += "\n"
            i += 1; continue

        m = re.match(r"\[(.+?)\]\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\](.*)", line)
        if m:
            role, ts, body = m.group(1), m.group(2), m.group(3).strip()
            # 检查紧随其后是否有AI标记行
            lookahead = " ".join(lines[i+1:i+3])
            is_ai = any(mk in lookahead for mk in VIP_AI_MARKERS)
            is_user = "用户" in role or "🤵" in role
            messages.append({"role": role, "ts": ts, "content": body,
                              "is_ai": is_ai, "is_user": is_user})
        elif messages:
            # Excel exports wrap long replies across physical lines.  Those
            # lines belong to the preceding timestamped message, not to a
            # separate unlabelled message and must never be dropped.
            messages[-1]["content"] = f"{messages[-1]['content']}\n{line}".strip()
        i += 1
    return messages


def _unwrap_standard_json_text(text: str) -> str:
    value = str(text or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        inner = value[1:-1].strip()
        if inner.startswith("{") or inner.startswith("["):
            return inner
    return value


def _loads_standard_json(text: str):
    raw = str(text or "").strip()
    if not raw:
        return None
    candidates = [raw]
    unwrapped = _unwrap_standard_json_text(raw)
    if unwrapped != raw:
        candidates.append(unwrapped)
        nested = _unwrap_standard_json_text(unwrapped)
        if nested != unwrapped:
            candidates.append(nested)
    seen = set()
    for item in candidates:
        if item in seen:
            continue
        seen.add(item)
        try:
            return json.loads(item)
        except Exception:
            continue
    return None


def _standard_reply_content_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (dict, list)):
        return structured_content_to_readable_text(value)
    return _cell_text(value)


def _standard_reply_content_from_object(data):
    """Return reply text when this is a quote-then-reply JSON object."""
    if not isinstance(data, dict):
        return None
    if data.get("type") != "reply":
        return None
    if not isinstance(data.get("reference"), dict):
        return None
    if "content" not in data:
        return None
    return _standard_reply_content_value(data.get("content"))


def _unescape_json_string(value: str) -> str:
    try:
        return json.loads(f'"{value}"')
    except Exception:
        return value


def _extract_top_level_reply_content(text: str):
    """Best-effort top-level content when reply JSON is recognizable but invalid."""
    raw = str(text or "")
    type_match = re.search(r'"type"\s*:\s*"reply"', raw)
    if not type_match:
        return None
    if not re.search(r'"reference"\s*:', raw):
        return None
    search_area = raw[type_match.end():]
    match = re.search(r'"content"\s*:\s*"((?:\\.|[^"\\])*)"', search_area)
    if match:
        return _unescape_json_string(match.group(1))
    if re.search(r'"content"\s*:\s*null', search_area):
        return ""
    return None


def clean_standard_message_body(body) -> str:
    """Keep reply JSON as its own content; leave ordinary text/JSON unchanged.

    Standard-channel AI/agent messages sometimes store a quote-then-reply
    envelope. Only that envelope is reduced to the top-level content. Old
    slices are not rewritten; re-upload to pick this up.
    """
    raw = body if isinstance(body, str) else _cell_text(body)
    parsed = _loads_standard_json(raw)
    if isinstance(parsed, dict):
        extracted = _standard_reply_content_from_object(parsed)
        if extracted is not None:
            return extracted
        if "content" not in parsed:
            return raw
        value = parsed.get("content")
        if isinstance(value, str):
            return value
        return _standard_reply_content_value(value)
    fallback = _extract_top_level_reply_content(raw)
    if fallback is not None:
        return fallback
    return raw


def _finalize_standard_message(message: dict) -> None:
    message["content"] = clean_standard_message_body(message.get("content"))


# ── 标准渠道对话解析 ────────────────────────────────────────────────────────────
def parse_standard_dialogue(dialogue_str: str, reply_person: str = "") -> list[dict]:
    """返回 [{'role', 'ts', 'content', 'is_ai', 'is_user'}]"""
    messages = []
    for raw_line in str(dialogue_str).split("\n"):
        line = raw_line.strip()
        if not line:
            if messages and messages[-1]["content"] and not str(messages[-1]["content"]).endswith("\n"):
                messages[-1]["content"] = str(messages[-1]["content"]) + "\n"
            continue
        m = re.match(r"\[(.+?)\]\s*\[(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\]:\s*(.*)", line, re.DOTALL)
        if m:
            if messages:
                _finalize_standard_message(messages[-1])
            role, ts, body = m.group(1), m.group(2), m.group(3)
            is_auto = "auto_reply" in role.lower()
            # A row-level “回复人” can contain both AI回复 and a real agent
            # name.  Classify only from this message's original role: never
            # promote “客服-张三” to AI merely because another message in the
            # same conversation was AI.
            is_ai = (not is_auto) and any(k in role for k in ["AI", "智能", "星助", "bot", "Bot"])
            is_user = "用户" in role or "user" in role.lower()
            messages.append({"role": role, "ts": ts, "content": body,
                              "is_ai": is_ai, "is_user": is_user, "is_auto": is_auto})
        elif messages:
            # Long messages may occupy multiple Excel text lines.  Keep every
            # continuation line with the prior message so MaaS receives the
            # full original content, then clean once the envelope is complete.
            messages[-1]["content"] = f"{messages[-1]['content']}\n{line}".strip()
    if messages:
        _finalize_standard_message(messages[-1])
    return messages


def extract_context(messages: list[dict], ai_idx: int, n_before: int = 2) -> str:
    """取 ai_idx 之前最多 n_before 条用户消息作为上下文"""
    ctx = []
    count = 0
    for j in range(ai_idx - 1, -1, -1):
        if messages[j].get("is_user"):
            ctx.insert(0, f"[用户][{messages[j]['ts']}]: {messages[j]['content'][:300]}")
            count += 1
            if count >= n_before:
                break
    return "\n".join(ctx)


_UCE_PUSH_ROLE_RE = re.compile(
    r"客服[\s\-–—－‐‑−~～]*uce[\s_\-–—－‐‑−]*push",
    re.IGNORECASE,
)


def _is_uce_push_role(role: str) -> bool:
    """True for the standard-channel system push identity, not a named agent."""
    text = str(role or "").strip()
    if not text:
        return False
    return bool(_UCE_PUSH_ROLE_RE.search(text))


_DISCORD_COMMAND_ROLES = frozenset({
    "command",
    "/command",
    "slash command",
    "slash-command",
    "slash_command",
    "application command",
})


def _normalize_role_key(role: str) -> str:
    return re.sub(r"\s+", " ", str(role or "").strip().lower())


def _is_discord_command_role(role: str) -> bool:
    """True for Discord slash-command identities, not a named human agent.

    Match the whole role, or the token left after stripping 客服/agent prefixes.
    Do not match merely because the role contains the word command.
    """
    text = str(role or "").strip()
    if not text:
        return False
    if _normalize_role_key(text) in _DISCORD_COMMAND_ROLES:
        return True
    return _normalize_role_key(_role_person_token(text)) in _DISCORD_COMMAND_ROLES


_AGENT_ROLE_PREFIX = re.compile(
    r"^(?:客服|人工客服|human_agent|operator|agent)[\s\-–—－:：/、]+",
    re.IGNORECASE,
)


def _role_person_token(role: str) -> str:
    value = str(role or "").strip()
    while True:
        nxt = _AGENT_ROLE_PREFIX.sub("", value, count=1).strip()
        if nxt == value:
            break
        value = nxt
    return value


def _looks_like_person_role(role: str) -> bool:
    """Excel sometimes puts an AI instruction into the 客服- role field."""
    token = _role_person_token(role)
    if not token or len(token) > 24:
        return False
    if any(mark in token for mark in ".?!。！？\n\r{}[]\""):
        return False
    if len(token.split()) > 3:
        return False
    return True


def _message_speaker(message: dict) -> str:
    """Map parser facts to the compact slice protocol; never infer from text."""
    if message.get("is_user"):
        return "player"
    if message.get("is_ai"):
        return "ai"
    if message.get("is_auto"):
        return "system"
    # System campaign pushes reuse the 客服- prefix. Classify them before the
    # generic agent markers so they never become human_agent.
    if _is_uce_push_role(message.get("role") or ""):
        return "system"
    if _is_discord_command_role(message.get("role") or ""):
        return "system"
    # A residual role is human only when the source explicitly identifies a
    #客服/agent.  Other unrecognised source values remain ``unknown`` instead
    # of being silently promoted to a human or AI speaker.
    raw_role = str(message.get("role") or "")
    role = raw_role.lower()
    if any(marker in role for marker in ("客服", "agent", "人工", "operator", "gm")):
        if not _looks_like_person_role(raw_role):
            return "ai"
        return "human_agent"
    return "unknown"


def _filter_standard_messages(messages: list[dict]) -> list[dict]:
    """Keep only messages that belong in a DC/VK/FB/LINE slice.

    Empty bodies, unknown speakers, and non-auto_reply system lines are
    dropped. Player messages stay as context when the slice also has a
    staff-side message (human_agent, ai, or auto_reply).
    """
    kept = []
    for message in messages or []:
        if not str(message.get("content") or "").strip():
            continue
        speaker = _message_speaker(message)
        if speaker == "unknown":
            continue
        if speaker == "system" and not message.get("is_auto"):
            continue
        kept.append(message)
    if not kept:
        return []
    if not any(
        message.get("is_auto") or _message_speaker(message) in {"human_agent", "ai"}
        for message in kept
    ):
        return []
    return kept


def _compact_slice(messages: list[dict], target_index: int, *, slice_id: str, channel: str, game: str, region: str) -> str:
    """Create the only User Prompt payload sent to MaaS.

    The rules live in the MaaS System Prompt.  An Excel row/segment is already
    a prepared slice, so all messages in that row are sent once.  ``target_index``
    remains in the signature for legacy callers but is intentionally ignored.
    """
    items = []
    for index in range(len(messages)):
        message = messages[index]
        items.append({
            "message_id": f"{slice_id}:m{index + 1}",
            "speaker": _message_speaker(message),
            "speaker_source": message.get("role") or None,
            "text": str(message.get("content") or ""),
            "created_at": message.get("ts") or None,
            "sequence": index + 1,
        })
    payload = {
        "schema_version": "1.0.0",
        "slice_id": slice_id,
        "channel": channel or None,
        "game": game or None,
        "region": region or None,
        "messages": items,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _full_transcript(messages: list[dict], session_uid: str) -> str:
    """Persist original messages so human-agent candidates remain traceable."""
    return json.dumps([
        {"message_id": f"{session_uid}:m{index + 1}", "speaker": _message_speaker(message),
         "speaker_source": message.get("role") or None, "text": str(message.get("content") or ""),
         "created_at": message.get("ts") or None}
        for index, message in enumerate(messages)
    ], ensure_ascii=False, separators=(",", ":"))


def guess_language(text: str) -> str:
    if not text:
        return "Unknown"
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    cyrillic = sum(1 for c in text if "\u0400" <= c <= "\u04ff")
    latin = sum(1 for c in text if c.isalpha() and ord(c) < 256)
    viet_markers = ["ợ", "ử", "ắ", "ể", "ị", "ọ", "ừ", "ứ", "ắ", "ẹ"]
    if cjk > len(text) * 0.1:
        return "Chinese"
    if cyrillic > 5:
        return "Russian"
    if any(m in text for m in viet_markers):
        return "Vietnamese"
    if latin > 5:
        return "English"
    return "Unknown"

def _mapping_language(text: str) -> str:
    """用于组合映射的语言标签，补充脚本和常见东南亚语言识别。"""
    t = str(text)
    if any('가' <= c <= '힣' for c in t): return "韩语"
    if any(('ぁ' <= c <= 'ゟ') or ('゠' <= c <= 'ヿ') for c in t): return "日语"
    if any('ก' <= c <= '๛' for c in t): return "泰语"
    if any(c in "ăâđêôơưĂÂĐÊÔƠƯạảấầẩẫậắằẳẵặẹẻẽếềểễệịỉĩọỏốồổỗộớờởỡợụủũứừửữựỳỵỷỹ" for c in t): return "越南语"
    if any('Ѐ' <= c <= 'ӿ' for c in t): return "俄语"
    if re.search(r"\b(saya|tidak|yang|dan|dengan|untuk|ini|itu|mohon|tolong)\b", t.lower()): return "印尼语"
    if re.search(r"\b(the|you|your|please|game|account|help|cannot|can't)\b", t.lower()): return "英语"
    return "英语"

def _force_mushroom_rush_to_sea_adventure(game, region=None):
    """Mushroom Rush 强制写成东南亚冒险。

    英文导出名 Mushroom Rush 对应标准游戏「冒险大作战」、标准地区「东南亚」。
    写死在解析层，不依赖 MappingConfig，才能命中 game_ai_configs。
    """
    if str(game or "").strip().casefold() == "mushroom rush":
        return "冒险大作战", "东南亚"
    return game, region


def _channel_match_rank(rule_channel, raw_channel):
    """Prefer exact channel, then prefix, then empty/any-channel rules."""
    rule = str(rule_channel or "").strip()
    incoming = str(raw_channel or "")
    prefix = incoming.split("-", 1)[0]
    if not rule:
        return 2
    if rule == incoming:
        return 0
    if rule == prefix:
        return 1
    return None


def _apply_mapping(raw_region, raw_channel, game, link, dialogue):
    try:
        from database import SessionLocal, MappingConfig
        import urllib.parse
        db = SessionLocal(); rows = db.query(MappingConfig).filter(MappingConfig.enabled == True).all(); db.close()
        page_id = urllib.parse.parse_qs(urllib.parse.urlparse(str(link)).query).get("page_id", [""])[0]
        language = _mapping_language(dialogue)
        norm_channel = raw_channel
        incoming_game = str(game or "").strip()
        for x in rows:
            if (x.match_field or "none") != "gameProductId":
                continue
            if x.raw_channel and x.raw_channel not in (raw_channel, str(raw_channel).split('-')[0]): continue
            if str(x.match_value or "").strip() != incoming_game: continue
            return x.target_channel or norm_channel, x.target_region, x.game
        ranked = []
        for index, x in enumerate(rows):
            match_field = x.match_field or "none"
            if match_field == "gameProductId":
                continue
            if x.raw_region and x.raw_region != raw_region: continue
            rank = _channel_match_rank(x.raw_channel, raw_channel)
            if rank is None:
                continue
            if x.game and x.game != game: continue
            if match_field == "page_id" and x.match_value != page_id: continue
            if match_field == "language" and x.match_value != language: continue
            ranked.append((rank, index, x))
        if ranked:
            x = min(ranked, key=lambda item: (item[0], item[1]))[2]
            return x.target_channel or norm_channel, x.target_region, None
    except Exception:
        pass
    return raw_channel, raw_region, None


# ── 主解析入口 ────────────────────────────────────────────────────────────────
def parse_excel_to_sessions(filepath: str, channel: str, shared_seen: set[str] | None = None, parse_stats: dict | None = None) -> tuple[list[dict], list[dict], str | None]:
    """
    返回 (sessions_list, kb_suggestions_list, error_message)
    sessions_list 每条: {
      channel, game, region, session_uid, session_link, user_name, reply_time,
      ai_messages: [{ msg_time, content, context, language }]
    }
    kb_suggestions_list: 来自人工客服的优质回复
    """
    try:
        sheets = pd.read_excel(filepath, sheet_name=None)
    except Exception as e:
        return [], [], f"无法读取文件: {e}"

    all_sessions = []
    all_kb_suggestions = []
    sheet_errors = []
    combined_m_stats = {"read_rows": 0, "filtered_no_agent": 0, "parse_failed": 0, "valid_messages": 0, "valid_sessions": 0, "warnings": 0, "warning_messages": []}
    non_empty = [(name, frame) for name, frame in sheets.items() if not frame.empty]
    # A workbook identified as M-backend must contain both named tabs.  An
    # empty tab is still a valid tab and simply contributes zero rows; only a
    # physically missing tab is an incomplete import.
    mbackend_present = {
        name for name, frame in sheets.items()
        if name in MBACKEND_SHEETS and is_mbackend_dataframe(frame, name)
    }
    if mbackend_present and mbackend_present != MBACKEND_SHEETS:
        missing_sheets = "、".join(sorted(MBACKEND_SHEETS - mbackend_present))
        return [], [], f"M 后台问题列表缺少工作表：{missing_sheets}"
    if non_empty and all(is_overseas_dataframe(frame) for _, frame in non_empty):
        # A workbook may contain multiple non-empty tabs; they still belong to
        # the same import and must share the session/_id de-duplication pass.
        merged = pd.concat([frame for _, frame in non_empty], ignore_index=True, sort=False)
        merged_sessions, merged_suggestions, merged_error = _parse_overseas(merged, shared_seen)
        return merged_sessions, merged_suggestions, merged_error

    for sheet_name, df in sheets.items():
        if df.empty:
            continue
        columns = {str(column).strip() for column in df.columns}
        if OVERSEAS_NEW_COLUMNS & columns and not is_overseas_dataframe(df):
            missing = sorted(OVERSEAS_NEW_COLUMNS - columns)
            if not (columns & OVERSEAS_TEXT_COLUMNS):
                missing.append("text 或 content")
            sheet_errors.append(f"工作表“{sheet_name}”：海外端内普客格式缺少关键字段: {missing}")
            continue
        sheet_channel = channel
        if channel != "VIP":
            detected = detect_channel_from_df(df)
            if detected != "UNKNOWN":
                sheet_channel = detected
        if is_mbackend_dataframe(df, sheet_name):
            sessions, kb_suggestions, error, _m_stats = _parse_mbackend(df, sheet_name)
            for key in ("read_rows", "filtered_no_agent", "parse_failed", "valid_messages", "valid_sessions", "warnings"):
                combined_m_stats[key] += int(_m_stats.get(key) or 0)
            combined_m_stats["warning_messages"].extend(_m_stats.get("warning_messages") or [])
        elif is_overseas_dataframe(df):
            sessions, kb_suggestions, error = _parse_overseas(df, shared_seen)
        elif sheet_channel == "VIP" or {"角色ID", "消息内容"}.issubset(df.columns):
            sessions, kb_suggestions, error = _parse_vip(df)
        else:
            sessions, kb_suggestions, error = _parse_standard(df, sheet_channel)
        if error:
            sheet_errors.append(f"工作表“{sheet_name}”：{error}")
            continue
        all_sessions.extend(sessions)
        all_kb_suggestions.extend(kb_suggestions)

    if all_sessions or all_kb_suggestions:
        if combined_m_stats["read_rows"]:
            for item in all_sessions:
                if item.get("_source_format") == "m_backend": item["_parse_stats"] = combined_m_stats
            if parse_stats is not None: parse_stats.update(combined_m_stats)
        return all_sessions, all_kb_suggestions, "; ".join(sheet_errors) if sheet_errors else None
    if sheet_errors:
        if parse_stats is not None: parse_stats.update(combined_m_stats)
        return [], [], "; ".join(sheet_errors)
    if parse_stats is not None: parse_stats.update(combined_m_stats)
    return [], [], "Excel 中没有可解析的非空工作表"


def _parse_vip(df: pd.DataFrame) -> tuple[list[dict], list[dict], str | None]:
    required = ["地区", "客服", "游戏", "角色ID", "角色名", "消息内容"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        return [], [], f"VIP文件缺少必填列: {missing}"

    sessions = []
    kb_suggestions = []
    for _, row in df.iterrows():
        game       = str(row.get("游戏", ""))
        role_id    = str(row.get("角色ID", ""))
        role_name  = str(row.get("角色名", ""))
        kefu_raw   = str(row.get("客服", ""))
        content    = str(row.get("消息内容", ""))

        msgs = parse_vip_messages(content)
        region = resolve_vip_region(kefu_raw, msgs)
        game, region = _force_mushroom_rush_to_sea_adventure(game, region)

        slice_id = f"{game}|{role_id}"
        slice_payload = json.loads(_compact_slice(msgs, 0, slice_id=slice_id, channel="VIP", game=game, region=region))
        ai_msgs = []
        for idx, msg in enumerate(msgs):
            if msg["is_ai"]:
                ts = None
                try:
                    ts = datetime.strptime(msg["ts"], "%Y-%m-%d %H:%M:%S")
                except Exception:
                    pass
                ai_msgs.append({
                    "msg_time": ts,
                    "content":  msg["content"],
                    "context": json.dumps(slice_payload, ensure_ascii=False, separators=(",", ":")),
                    "language": guess_language(msg["content"]),
                })

        # 提取人工客服KB建议
        kb_suggestions.extend(extract_human_kb_suggestions(msgs, game, region, "VIP"))

        if not msgs:
            continue

        last_ts = next((m["msg_time"] for m in reversed(ai_msgs) if m["msg_time"]), None)
        sessions.append({
            "channel":      "VIP",
            "game":         game,
            "region":       region,
            "session_uid":  f"{game}|{role_id}",
            "session_link": None,
            "user_name":    role_name,
            "reply_time":   last_ts,
            "ai_messages":  ai_msgs,
            "full_transcript": _full_transcript(msgs, f"{game}|{role_id}"),
            "slice_id": slice_id,
            "slice_payload": slice_payload,
        })
    return sessions, kb_suggestions, None


def _parse_standard(df: pd.DataFrame, channel: str) -> tuple[list[dict], list[dict], str | None]:
    required = ["对话记录"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        return [], [], f"{channel}文件缺少必填列: {missing}"

    def get_col(row, *candidates):
        for c in candidates:
            if c in row.index and pd.notna(row[c]):
                return str(row[c])
        return ""

    sessions = []
    kb_suggestions = []
    for _, row in df.iterrows():
        reply_person = get_col(row, "回复人", "客服")
        game     = get_col(row, "游戏")
        region   = get_col(row, "地区")
        link     = get_col(row, "会话链接")
        uid      = get_col(row, "用户id（三方渠道的）", "对话ID", "会话ID")
        uname    = get_col(row, "用户名（三方渠道的）", "用户名")
        dialogue = get_col(row, "对话记录", "内容", "消息内容")
        raw_channel = get_col(row, "客诉渠道", "咨询渠道") or channel
        mapped_channel, mapped_region, mapped_game = _apply_mapping(region, raw_channel, game, link, dialogue)
        row_channel = mapped_channel or channel
        if mapped_region is not None:
            region = mapped_region
        if mapped_game:
            game = mapped_game
        game, region = _force_mushroom_rush_to_sea_adventure(game, region)

        msgs = _filter_standard_messages(parse_standard_dialogue(dialogue, reply_person))
        if not msgs:
            continue

        # KB extraction uses the same filtered messages so empty/system/unknown
        # rows cannot produce dirty suggestions for slices that never ingest.
        kb_suggestions.extend(extract_human_kb_suggestions(msgs, game, region, row_channel))

        slice_id = uid or f"{row_channel}-{_}"
        slice_payload = json.loads(_compact_slice(msgs, 0, slice_id=slice_id, channel=row_channel, game=game, region=region))
        ai_msgs = []
        for idx, msg in enumerate(msgs):
            if msg["is_ai"]:
                ts = None
                try:
                    ts = datetime.strptime(msg["ts"].strip(), "%Y-%m-%d %H:%M:%S")
                except Exception:
                    pass
                ai_msgs.append({
                    "msg_time": ts,
                    "content":  msg["content"],
                    "context": json.dumps(slice_payload, ensure_ascii=False, separators=(",", ":")),
                    "language": guess_language(msg["content"]),
                })

        last_ts = next((m["msg_time"] for m in reversed(ai_msgs) if m["msg_time"]), None)
        sessions.append({
            "channel":      row_channel,
            "game":         game,
            "region":       region,
            "session_uid":  slice_id,
            "session_link": link,
            "user_name":    uname,
            "reply_time":   last_ts,
            "ai_messages":  ai_msgs,
            "full_transcript": _full_transcript(msgs, uid or f"{channel}-{_}"),
            "slice_id": slice_id,
            "slice_payload": slice_payload,
        })
    return sessions, kb_suggestions, None
