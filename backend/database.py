from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Boolean, Float, ForeignKey, UniqueConstraint
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from datetime import datetime, timezone, timedelta
from config import DB_PATH

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    # Upload parsing can take minutes for a large multipart request.  Never
    # let that transaction make short auth/read requests fail immediately.
    connect_args={"check_same_thread": False, "timeout": 30},
    pool_pre_ping=True,
)

from sqlalchemy import event
from sqlalchemy.exc import DatabaseError, OperationalError


@event.listens_for(engine, "connect")
def _sqlite_connection_pragmas(dbapi_connection, _connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def sqlite_error_kind(exc: BaseException) -> str | None:
    """Classify SQLite lock/corruption so API routes can return a stable message."""
    text = " ".join(str(part) for part in getattr(exc, "args", ()) if part).lower()
    orig = getattr(exc, "orig", None)
    if orig is not None:
        text = f"{text} {' '.join(str(part) for part in getattr(orig, 'args', ()) if part)}".lower()
    if "malformed" in text or "corrupt" in text or "disk image is malformed" in text:
        return "malformed"
    if "too many sql variables" in text or "too many terms in compound select" in text:
        return "too_many_vars"
    if "database is locked" in text or "database is busy" in text:
        return "locked"
    if isinstance(exc, OperationalError) and "locked" in text:
        return "locked"
    if isinstance(exc, DatabaseError) and "malformed" in text:
        return "malformed"
    return None


def sqlite_user_message(exc: BaseException, action: str = "操作") -> str:
    kind = sqlite_error_kind(exc)
    if kind == "locked":
        return f"{action}失败：数据库正被其他任务占用，请稍后重试。不要强制杀进程。"
    if kind == "malformed":
        return f"{action}失败：数据库文件已损坏，请停止写入并联系管理员恢复备份。"
    if kind == "too_many_vars":
        return f"{action}失败：批次数据量过大，请刷新页面后重试（服务端将按批次删除，不再一次绑定全部切片 ID）。"
    return f"{action}失败：{exc}"


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def utcnow():
    """Naive SQLite timestamps whose business meaning is Beijing time."""
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8))).replace(tzinfo=None)


# ── 用户 ──────────────────────────────────────────────────────────────────────
class User(Base):
    __tablename__ = "users"
    id         = Column(Integer, primary_key=True)
    username   = Column(String(80), unique=True, nullable=False)
    email      = Column(String(120), unique=True, nullable=False)
    hashed_pwd = Column(String(200), nullable=False)
    role       = Column(String(20), default="analyst")   # admin | analyst
    is_active  = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    last_login = Column(DateTime, nullable=True)
    disable_effective_at = Column(DateTime, nullable=True)
    must_change_password = Column(Boolean, default=False, nullable=False)


# ── 分析批次 ─────────────────────────────────────────────────────────────────
class AnalysisBatch(Base):
    """一次完整的上传+分析操作"""
    __tablename__ = "analysis_batches"
    id          = Column(Integer, primary_key=True)
    name        = Column(String(200))                     # 用户自命名，默认用时间
    source_task_id = Column(String(200), nullable=False, default="upload")
    source_region = Column(String(100), nullable=False, default="unknown")
    created_by  = Column(Integer, ForeignKey("users.id"))
    created_at  = Column(DateTime, default=utcnow)
    status      = Column(String(30), default="pending")   # pending|parsing|analyzing|done|failed
    total_ai_msgs   = Column(Integer, default=0)
    analyzed_count  = Column(Integer, default=0)
    # New slice-oriented analysis counters.  ``total_ai_msgs`` and
    # ``analyzed_count`` remain for the legacy UI/API, while new batches use
    # one Excel row/segment as the analysis unit.
    total_slices    = Column(Integer, default=0)
    analyzed_slices = Column(Integer, default=0)
    error_msg   = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    control_reason = Column(Text, nullable=True)
    expected_file_count = Column(Integer, default=0)
    upload_finalized_at = Column(DateTime, nullable=True)

    files   = relationship("UploadedFile", back_populates="batch")
    issues  = relationship("QcIssue",      back_populates="batch")
    reports = relationship("Report",       back_populates="batch")
    slices  = relationship("QcSlice",      back_populates="batch")


# ── 上传文件 ──────────────────────────────────────────────────────────────────
class UploadedFile(Base):
    __tablename__ = "uploaded_files"
    __table_args__ = (UniqueConstraint("batch_id", "client_file_id", name="uq_uploaded_files_batch_client"),)
    id         = Column(Integer, primary_key=True)
    batch_id   = Column(Integer, ForeignKey("analysis_batches.id"))
    filename   = Column(String(300))
    channel    = Column(String(50))    # DC|FB|LINE|VK|VIP
    row_count  = Column(Integer, default=0)
    ai_msg_count = Column(Integer, default=0)
    dedup_row_count = Column(Integer, default=0)
    valid_message_count = Column(Integer, default=0)
    session_count = Column(Integer, default=0)
    slice_count = Column(Integer, default=0)
    warning_count = Column(Integer, default=0)
    parse_warnings = Column(Text, nullable=True)
    filtered_no_agent_count = Column(Integer, default=0)
    parse_failed_count = Column(Integer, default=0)
    parse_error = Column(Text, nullable=True)
    client_file_id = Column(String(100), nullable=True)
    file_size = Column(Integer, default=0)
    stored_path = Column(String(1000), nullable=True)
    status = Column(String(30), default="parsing")
    error_stage = Column(String(30), nullable=True)
    upload_error = Column(Text, nullable=True)
    parse_started_at = Column(DateTime, nullable=True)
    parse_completed_at = Column(DateTime, nullable=True)
    attempt_count = Column(Integer, default=1)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    uploaded_at = Column(DateTime, default=utcnow)

    batch = relationship("AnalysisBatch", back_populates="files")

class MappingConfig(Base):
    __tablename__ = "mapping_configs"
    id = Column(Integer, primary_key=True)
    raw_region = Column(String(100), nullable=True)
    raw_channel = Column(String(100), nullable=True)
    game = Column(String(100), nullable=True)
    match_field = Column(String(30), default="none")
    match_value = Column(String(200), nullable=True)
    target_channel = Column(String(50), nullable=False)
    target_region = Column(String(100), nullable=True)
    enabled = Column(Boolean, default=True)
    remark = Column(String(300), nullable=True)
    created_at = Column(DateTime, default=utcnow)


class GameAiConfig(Base):
    """Per standard-game + standard-region switch for AI/MaaS analysis.

    Kept independent from MappingConfig so region/channel matching rules stay
    unchanged. Unmatched slices default to analysis off.
    """
    __tablename__ = "game_ai_configs"
    __table_args__ = (
        UniqueConstraint("game", "region", name="uq_game_ai_configs_game_region"),
    )

    id = Column(Integer, primary_key=True)
    game = Column(String(100), nullable=False)
    region = Column(String(100), nullable=False)
    analysis_enabled = Column(Boolean, nullable=False, default=False)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class SystemDictionary(Base):
    """Administrator-maintained, business-facing option values.

    Values are deliberately stored as strings on existing review records.  A
    dictionary item may be disabled, but historical records keep their value
    without any destructive rewrite of MaaS or human-review fields.
    """
    __tablename__ = "system_dictionaries"
    __table_args__ = (UniqueConstraint("group_key", "value", name="uq_system_dictionary_group_value"),)

    id = Column(Integer, primary_key=True)
    group_key = Column(String(64), nullable=False, index=True)
    value = Column(String(200), nullable=False)
    display_name = Column(String(200), nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)
    enabled = Column(Boolean, nullable=False, default=True, index=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    updated_by = Column(Integer, ForeignKey("users.id"), nullable=True)


# ── 会话（从Excel解析后标准化存储）────────────────────────────────────────────
class Session(Base):
    __tablename__ = "sessions"
    id          = Column(Integer, primary_key=True)
    batch_id    = Column(Integer, ForeignKey("analysis_batches.id"))
    uploaded_file_id = Column(Integer, ForeignKey("uploaded_files.id"), nullable=True)
    # Keep the ORM aligned with the restored database schema.  Older code
    # called these values session_uid/session_link; properties below preserve
    # that read API without inserting non-existent columns.
    event_id     = Column(String(500), nullable=False, default="")
    channel     = Column(String(100))
    game        = Column(String(200))
    region      = Column(String(100))
    conversation_key = Column(Text, nullable=True)
    started_at  = Column(DateTime, nullable=True)
    full_transcript = Column(Text, default="")
    source_url  = Column(String(1000), default="")
    source_sheet = Column(String(80), nullable=True)
    raw_channel = Column(String(100), nullable=True)
    raw_region = Column(String(100), nullable=True)
    source_reply_agents_json = Column(Text, nullable=True)
    raw_metadata_json = Column(Text, nullable=True)
    parse_warnings_json = Column(Text, nullable=True)

    @property
    def session_uid(self):
        return self.event_id

    @property
    def session_link(self):
        return self.source_url

    @property
    def reply_time(self):
        return self.started_at

    ai_messages = relationship("AiMessage", back_populates="session")


# ── AI消息（每条待质检的AI回复）──────────────────────────────────────────────
class AiMessage(Base):
    __tablename__ = "ai_messages"
    id          = Column(Integer, primary_key=True)
    session_id  = Column(Integer, ForeignKey("sessions.id"))
    batch_id    = Column(Integer, ForeignKey("analysis_batches.id"))
    uploaded_file_id = Column(Integer, ForeignKey("uploaded_files.id"), nullable=True)
    msg_time    = Column(DateTime, nullable=True)
    content     = Column(Text)
    context     = Column(Text)   # 前2轮用户发言，JSON格式
    language    = Column(String(50), nullable=True)
    analyzed    = Column(Boolean, default=False)
    has_issue   = Column(Boolean, nullable=True)

    session  = relationship("Session",   back_populates="ai_messages")
    issues   = relationship("QcIssue",   back_populates="ai_message")


# ── 质检问题 ──────────────────────────────────────────────────────────────────
class QcIssue(Base):
    __tablename__ = "qc_issues"
    id               = Column(Integer, primary_key=True)
    batch_id         = Column(Integer, ForeignKey("analysis_batches.id"))
    ai_message_id    = Column(Integer, ForeignKey("ai_messages.id"))
    issue_type       = Column(String(200))
    severity         = Column(String(30))   # 严重|中级|一般|需人工复核
    session_uid      = Column(String(300))
    session_link     = Column(Text, nullable=True)
    reply_time       = Column(String(50), nullable=True)
    language         = Column(String(50), nullable=True)
    game             = Column(String(100), nullable=True)
    region           = Column(String(100), nullable=True)
    channel          = Column(String(50), nullable=True)
    ai_sentence_orig = Column(Text)
    ai_sentence_cn   = Column(Text)
    context          = Column(Text)
    reason           = Column(Text)
    suggestion       = Column(Text)
    revised_reply    = Column(Text)
    revised_reply_cn = Column(Text)
    priority         = Column(String(10), default="P2")
    status           = Column(String(30), default="pending")
    assignee         = Column(String(100), nullable=True)
    updated_at       = Column(DateTime, default=utcnow, onupdate=utcnow)
    created_at       = Column(DateTime, default=utcnow)

    batch      = relationship("AnalysisBatch", back_populates="issues")
    ai_message = relationship("AiMessage",     back_populates="issues")


# ── 新版切片分析结果 ─────────────────────────────────────────────────────────
class QcSlice(Base):
    """One Excel row/segment and its single MaaS analysis result."""
    __tablename__ = "qc_slices"
    id                  = Column(Integer, primary_key=True)
    batch_id            = Column(Integer, ForeignKey("analysis_batches.id"), nullable=False)
    uploaded_file_id    = Column(Integer, ForeignKey("uploaded_files.id"), nullable=True)
    session_id          = Column(Integer, ForeignKey("sessions.id"), nullable=True)
    slice_id            = Column(String(300), nullable=False, index=True)
    channel             = Column(String(100), nullable=True)
    game                = Column(String(200), nullable=True)
    region              = Column(String(100), nullable=True)
    messages_json       = Column(Text, nullable=False, default="[]")
    analysis_status     = Column(String(30), nullable=False, default="pending")
    quality_has_issue   = Column(Boolean, nullable=True)
    quality_issue_count = Column(Integer, default=0)
    knowledge_decision = Column(String(40), nullable=True)
    knowledge_is_candidate = Column(Boolean, nullable=True)
    analysis_version    = Column(String(100), nullable=True)
    prompt_version      = Column(String(100), nullable=True)
    maas_request_id     = Column(String(200), nullable=True)
    raw_response_json   = Column(Text, nullable=True)
    warnings_json       = Column(Text, nullable=False, default="[]")
    errors_json         = Column(Text, nullable=False, default="[]")
    error_code          = Column(String(100), nullable=True)
    error_message       = Column(Text, nullable=True)
    started_at          = Column(DateTime, nullable=True)
    completed_at        = Column(DateTime, nullable=True)
    created_at          = Column(DateTime, default=utcnow)
    source_format       = Column(String(40), nullable=True)
    source_sheet        = Column(String(80), nullable=True)
    source_problem_id   = Column(String(200), nullable=True)
    skip_reason         = Column(String(80), nullable=True)
    ai_config_id        = Column(Integer, nullable=True)
    ai_config_snapshot_json = Column(Text, nullable=True)
    analysis_enabled_snapshot = Column(Boolean, nullable=True)

    batch = relationship("AnalysisBatch", back_populates="slices")
    quality_issues = relationship("QcSliceQualityIssue", back_populates="slice", cascade="all, delete-orphan")
    knowledge_suggestion = relationship("QcSliceKnowledgeSuggestion", back_populates="slice", uselist=False, cascade="all, delete-orphan")


class QcSliceQualityIssue(Base):
    __tablename__ = "qc_slice_quality_issues"
    id                    = Column(Integer, primary_key=True)
    slice_id              = Column(Integer, ForeignKey("qc_slices.id"), nullable=False, index=True)
    issue_id              = Column(String(200), nullable=True)
    issue_type            = Column(String(100), nullable=False)
    severity              = Column(String(30), nullable=False)
    confidence           = Column(Float, nullable=True)
    ai_message_ids       = Column(Text, nullable=False, default="[]")
    evidence_message_ids = Column(Text, nullable=False, default="[]")
    player_question_json = Column(Text, nullable=True)
    ai_answer_json       = Column(Text, nullable=True)
    reason               = Column(Text, nullable=True)
    suggestion           = Column(Text, nullable=True)
    revised_reply        = Column(Text, nullable=True)
    revised_reply_zh_cn  = Column(Text, nullable=True)
    needs_manual_review  = Column(Boolean, default=False)
    manual_review_reason = Column(Text, nullable=True)
    is_primary            = Column(Boolean, default=False)
    # Human-review fields are separate from the MaaS analysis result.
    human_tags_json       = Column(Text, nullable=False, default="[]")
    human_ai_score        = Column(Integer, nullable=True)
    human_issue_type      = Column(String(100), nullable=True)
    human_severity        = Column(String(30), nullable=True)
    human_processing_destination = Column(String(100), nullable=True)
    human_review_status   = Column(String(30), nullable=False, default="pending_review")
    human_review_comment  = Column(Text, nullable=True)
    human_updated_by      = Column(Integer, nullable=True)
    human_updated_at      = Column(DateTime, nullable=True)
    # Reviewer-entered QA is deliberately kept on the quality issue.  It is
    # never copied into QcSliceKnowledgeSuggestion, whose row is the immutable
    # MaaS suggestion for a slice.
    qa_source             = Column(String(30), nullable=True)
    # The only editable QA payload.  It intentionally has no MaaS fields and
    # allows each question to carry its own answer.
    human_qa_pairs        = Column(Text, nullable=True)
    human_standard_questions = Column(Text, nullable=False, default="[]")
    human_standard_answer = Column(Text, nullable=True)
    # Reviewer-authored knowledge metadata for a quality issue.  These fields
    # mirror the knowledge-suggestion review form and never alter MaaS fields.
    human_knowledge_base = Column(String(50), nullable=True)
    human_category       = Column(String(100), nullable=True)
    human_applicable_scope = Column(Text, nullable=True)
    knowledge_pool_status = Column(String(40), nullable=True, index=True)
    knowledge_pool_created_at = Column(DateTime, nullable=True)
    knowledge_pool_created_by = Column(Integer, nullable=True)
    knowledge_pool_organized_by = Column(Integer, nullable=True)
    knowledge_pool_organized_at = Column(DateTime, nullable=True)
    knowledge_pool_exported_by = Column(Integer, nullable=True)
    knowledge_pool_exported_at = Column(DateTime, nullable=True)
    knowledge_pool_jiuzhang_uploaded_by = Column(Integer, nullable=True)
    knowledge_pool_jiuzhang_uploaded_at = Column(DateTime, nullable=True)
    created_at           = Column(DateTime, default=utcnow)
    slice = relationship("QcSlice", back_populates="quality_issues")


class QcSliceKnowledgeSuggestion(Base):
    __tablename__ = "qc_slice_knowledge_suggestions"
    id                    = Column(Integer, primary_key=True)
    slice_id              = Column(Integer, ForeignKey("qc_slices.id"), nullable=False, unique=True, index=True)
    answer_source         = Column(String(30), nullable=False, default="human_agent")
    decision              = Column(String(40), nullable=False)
    confidence            = Column(Float, nullable=True)
    category              = Column(String(50), nullable=True)
    question_message_ids  = Column(Text, nullable=False, default="[]")
    answer_message_ids    = Column(Text, nullable=False, default="[]")
    evidence_message_ids  = Column(Text, nullable=False, default="[]")
    title                 = Column(String(300), nullable=True)
    standard_questions    = Column(Text, nullable=False, default="[]")
    standard_answer       = Column(Text, nullable=True)
    applicable_scope      = Column(Text, nullable=True)
    # Compatibility-only storage for databases created before keyword fields
    # were removed from the product contract. The field is never exposed,
    # edited, filtered, or exported; the empty JSON default is required by
    # the legacy SQLite column's NOT NULL constraint.
    keywords             = Column(Text, nullable=False, default="[]", server_default="[]")
    reason                = Column(Text, nullable=True)
    reject_reason         = Column(Text, nullable=True)
    needs_manual_review   = Column(Boolean, default=False)
    manual_review_reason  = Column(Text, nullable=True)
    # Human-review output and downstream knowledge-pool state.  These fields
    # are deliberately separate from the MaaS decision/raw JSON so exports
    # never overwrite the original analysis result.
    processing_status     = Column(String(40), nullable=False, default="pending_entry", index=True)
    organized_by          = Column(Integer, nullable=True)
    organized_at          = Column(DateTime, nullable=True)
    exported_by           = Column(Integer, nullable=True)
    exported_at           = Column(DateTime, nullable=True)
    jiuzhang_uploaded_by  = Column(Integer, nullable=True)
    jiuzhang_uploaded_at  = Column(DateTime, nullable=True)
    # Human-edited QA fields never overwrite standard_questions/standard_answer
    # or raw_response_json. The pool uses these values when present.
    human_tags_json          = Column(Text, nullable=False, default="[]")
    human_knowledge_base     = Column(String(50), nullable=True)
    # The only editable QA payload.  MaaS standard_questions/standard_answer
    # remain immutable analysis output.
    human_qa_pairs           = Column(Text, nullable=True)
    human_standard_questions = Column(Text, nullable=False, default="[]")
    human_standard_answer    = Column(Text, nullable=True)
    human_category           = Column(String(100), nullable=True)
    human_applicable_scope   = Column(Text, nullable=True)
    human_review_status      = Column(String(30), nullable=False, default="pending_review")
    human_review_comment     = Column(Text, nullable=True)
    human_updated_by         = Column(Integer, nullable=True)
    human_updated_at         = Column(DateTime, nullable=True)
    created_at            = Column(DateTime, default=utcnow)
    slice = relationship("QcSlice", back_populates="knowledge_suggestion")


class KnowledgeSuggestionStatusLog(Base):
    """Append-only history for knowledge-pool processing status changes."""
    __tablename__ = "knowledge_suggestion_status_logs"
    id                       = Column(Integer, primary_key=True)
    knowledge_suggestion_id  = Column(Integer, ForeignKey("qc_slice_knowledge_suggestions.id"), nullable=False, index=True)
    from_status              = Column(String(40), nullable=True)
    to_status                = Column(String(40), nullable=False)
    operator_id              = Column(Integer, ForeignKey("users.id"), nullable=False)
    operator_name            = Column(String(80), nullable=False)
    reason                   = Column(Text, nullable=True)
    created_at               = Column(DateTime, default=utcnow)


class QAPoolEntry(Base):
    """One independently actionable QA pair in the pending-publication pool.

    The source rows keep the editable JSON.  This table deliberately owns only
    per-pair lifecycle state and audit metadata, so it never overwrites MaaS or
    legacy source fields.
    """
    __tablename__ = "qa_pool_entries"
    id = Column(Integer, primary_key=True)
    pool_id = Column(String(160), nullable=False, unique=True, index=True)
    qa_source = Column(String(30), nullable=False, index=True)
    source_item_type = Column(String(40), nullable=False, index=True)
    source_item_id = Column(Integer, nullable=False, index=True)
    qa_index = Column(Integer, nullable=False)
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    processing_status = Column(String(40), nullable=False, default="pending_entry", index=True)
    source_pair_active = Column(Boolean, nullable=False, default=True, index=True)
    created_by = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, nullable=True)
    organized_by = Column(Integer, nullable=True)
    organized_at = Column(DateTime, nullable=True)
    exported_by = Column(Integer, nullable=True)
    exported_at = Column(DateTime, nullable=True)
    jiuzhang_uploaded_by = Column(Integer, nullable=True)
    jiuzhang_uploaded_at = Column(DateTime, nullable=True)


class QAPoolStatusLog(Base):
    """Append-only, per-QA-pair lifecycle audit log."""
    __tablename__ = "qa_pool_status_logs"
    id = Column(Integer, primary_key=True)
    pool_entry_id = Column(Integer, ForeignKey("qa_pool_entries.id"), nullable=False, index=True)
    action = Column(String(60), nullable=False)
    from_status = Column(String(40), nullable=True)
    to_status = Column(String(40), nullable=True)
    operator_id = Column(Integer, nullable=True)
    operator_name = Column(String(80), nullable=True)
    reason = Column(Text, nullable=True)
    operator_role = Column(String(20), nullable=True)
    target_type = Column(String(40), nullable=True)
    target_id = Column(String(160), nullable=True)
    from_value = Column(Text, nullable=True)
    to_value = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)


class ReviewAssignment(Base):
    """A review task assigned to one reviewer for one analyzed item.

    ``item_id`` intentionally has no database foreign key because the item can
    be either a QcSliceQualityIssue or a QcSliceKnowledgeSuggestion.  The API
    validates the referenced row and batch before creating an assignment.
    """
    __tablename__ = "review_assignments"
    id = Column(Integer, primary_key=True)
    batch_id = Column(Integer, ForeignKey("analysis_batches.id"), nullable=False, index=True)
    item_type = Column(String(40), nullable=False, index=True)  # quality_issue | knowledge_suggestion
    item_id = Column(Integer, nullable=False, index=True)
    assignee_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    status = Column(String(30), nullable=False, default="pending", index=True)
    assigned_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    assigned_at = Column(DateTime, default=utcnow)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    due_at = Column(DateTime, nullable=True)
    review_comment = Column(Text, nullable=True)
    review_decision = Column(String(40), nullable=True)
    returned_reason = Column(Text, nullable=True)
    claim_source = Column(String(30), nullable=True)
    claimed_at = Column(DateTime, nullable=True)
    # Persistent processing lock metadata.  A lock has no expiry; it remains
    # held until completion, explicit release, cancellation or reassignment.


class ReviewAssignmentLog(Base):
    """Append-only audit trail for assignment and review actions."""
    __tablename__ = "review_assignment_logs"
    id = Column(Integer, primary_key=True)
    assignment_id = Column(Integer, ForeignKey("review_assignments.id"), nullable=False, index=True)
    action = Column(String(40), nullable=False)
    operator_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    from_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    to_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    note = Column(Text, nullable=True)
    operator_role = Column(String(20), nullable=True)
    target_type = Column(String(40), nullable=True)
    target_id = Column(String(160), nullable=True)
    from_value = Column(Text, nullable=True)
    to_value = Column(Text, nullable=True)
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)


class ReviewProcessingLog(Base):
    """Append-only audit trail for manual review edits and status changes."""
    __tablename__ = "review_processing_logs"
    id = Column(Integer, primary_key=True)
    item_type = Column(String(40), nullable=False, index=True)
    item_id = Column(Integer, nullable=False, index=True)
    from_status = Column(String(30), nullable=True)
    to_status = Column(String(30), nullable=True)
    action = Column(String(40), nullable=False)
    operator_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    operator_name = Column(String(80), nullable=False)
    reason = Column(Text, nullable=True)
    operator_role = Column(String(20), nullable=True)
    target_type = Column(String(40), nullable=True)
    target_id = Column(String(160), nullable=True)
    from_value = Column(Text, nullable=True)
    to_value = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)


class AuditLog(Base):
    """Security-relevant audit events without credentials or tokens."""
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True)
    operator_id = Column(Integer, nullable=True, index=True)
    operator_role = Column(String(20), nullable=True)
    action = Column(String(80), nullable=False, index=True)
    target_type = Column(String(40), nullable=False, index=True)
    target_id = Column(String(160), nullable=True, index=True)
    from_value = Column(Text, nullable=True)
    to_value = Column(Text, nullable=True)
    reason = Column(Text, nullable=True)
    request_id = Column(String(100), nullable=True, index=True)
    created_at = Column(DateTime, default=utcnow)


# ── 知识库好案例 ──────────────────────────────────────────────────────────────
class KbSuggestion(Base):
    __tablename__ = "kb_suggestions"
    id          = Column(Integer, primary_key=True)
    batch_id    = Column(Integer, ForeignKey("analysis_batches.id"))
    game        = Column(String(100))
    region      = Column(String(100))
    channel     = Column(String(50))
    topic       = Column(String(200))
    question    = Column(Text)
    question_en = Column(Text, nullable=True)
    answer      = Column(Text)
    answer_en   = Column(Text, nullable=True)
    agent_name  = Column(String(100), nullable=True)
    satisfied_expr = Column(String(200), nullable=True)
    created_at  = Column(DateTime, default=utcnow)


# ── 报告 ──────────────────────────────────────────────────────────────────────
class Report(Base):
    __tablename__ = "reports"
    id          = Column(Integer, primary_key=True)
    batch_id    = Column(Integer, ForeignKey("analysis_batches.id"))
    name        = Column(String(300))
    html_path   = Column(String(500), nullable=True)
    created_by  = Column(Integer, ForeignKey("users.id"))
    created_at  = Column(DateTime, default=utcnow)
    filter_json = Column(Text, nullable=True)   # 生成时的筛选条件JSON

    batch = relationship("AnalysisBatch", back_populates="reports")


def configure_sqlite():
    """Enable WAL once per process so readers are not blocked by writers."""
    from sqlalchemy import text
    with engine.begin() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))
        conn.execute(text("PRAGMA synchronous=NORMAL"))
        conn.execute(text("PRAGMA busy_timeout=30000"))


def init_db():
    from sqlalchemy import inspect, text
    configure_sqlite()
    new_dictionary_table = "system_dictionaries" not in inspect(engine).get_table_names()
    Base.metadata.create_all(bind=engine)
    seed_mapping_configs()
    seed_game_ai_configs()
    if new_dictionary_table:
        seed_system_dictionaries()
    inspector = inspect(engine)
    if "users" in inspector.get_table_names():
        columns = {c["name"] for c in inspector.get_columns("users")}
        with engine.begin() as conn:
            for name, definition in {
                "updated_at": "DATETIME",
                "disable_effective_at": "DATETIME",
                "must_change_password": "BOOLEAN DEFAULT 0",
            }.items():
                if name not in columns:
                    conn.execute(text(f"ALTER TABLE users ADD COLUMN {name} {definition}"))
    if "qc_issues" in inspector.get_table_names():
        columns = {c["name"] for c in inspector.get_columns("qc_issues")}
        additions = {"priority": "VARCHAR(10)", "status": "VARCHAR(30)", "assignee": "VARCHAR(100)", "updated_at": "DATETIME"}
        with engine.begin() as conn:
            for name, definition in additions.items():
                if name not in columns: conn.execute(text(f"ALTER TABLE qc_issues ADD COLUMN {name} {definition}"))
            conn.execute(text("UPDATE qc_issues SET priority = CASE severity WHEN '严重' THEN 'P0' WHEN '中级' THEN 'P1' WHEN '一般' THEN 'P2' ELSE 'P3' END WHERE priority IS NULL OR priority = ''"))
            conn.execute(text("UPDATE qc_issues SET status = 'pending' WHERE status IS NULL OR status = ''"))
    # Columns added after the original workspace database was restored.
    if "analysis_batches" in inspector.get_table_names():
        columns = {c["name"] for c in inspector.get_columns("analysis_batches")}
        with engine.begin() as conn:
            for name, definition in {
                "total_slices": "INTEGER", "analyzed_slices": "INTEGER",
                "expected_file_count": "INTEGER DEFAULT 0", "upload_finalized_at": "DATETIME",
                "updated_at": "DATETIME", "control_reason": "TEXT",
            }.items():
                if name not in columns:
                    conn.execute(text(f"ALTER TABLE analysis_batches ADD COLUMN {name} {definition}"))
    if "sessions" in inspector.get_table_names():
        columns = {c["name"] for c in inspector.get_columns("sessions")}
        additions = {
            "source_sheet": "VARCHAR(80)", "raw_channel": "VARCHAR(100)", "raw_region": "VARCHAR(100)",
            "source_reply_agents_json": "TEXT", "raw_metadata_json": "TEXT", "parse_warnings_json": "TEXT",
            "uploaded_file_id": "INTEGER",
        }
        with engine.begin() as conn:
            for name, definition in additions.items():
                if name not in columns: conn.execute(text(f"ALTER TABLE sessions ADD COLUMN {name} {definition}"))
    if "qc_slices" in inspector.get_table_names():
        columns = {c["name"] for c in inspector.get_columns("qc_slices")}
        additions = {
            "source_format": "VARCHAR(40)", "source_sheet": "VARCHAR(80)", "source_problem_id": "VARCHAR(200)",
            "uploaded_file_id": "INTEGER", "skip_reason": "VARCHAR(80)", "ai_config_id": "INTEGER",
            "ai_config_snapshot_json": "TEXT", "analysis_enabled_snapshot": "BOOLEAN",
        }
        with engine.begin() as conn:
            for name, definition in additions.items():
                if name not in columns: conn.execute(text(f"ALTER TABLE qc_slices ADD COLUMN {name} {definition}"))
    if "uploaded_files" in inspector.get_table_names():
        columns = {c["name"] for c in inspector.get_columns("uploaded_files")}
        additions = {
            "dedup_row_count": "INTEGER DEFAULT 0",
            "valid_message_count": "INTEGER DEFAULT 0",
            "session_count": "INTEGER DEFAULT 0",
            "slice_count": "INTEGER DEFAULT 0",
            "warning_count": "INTEGER DEFAULT 0",
            "parse_warnings": "TEXT",
            "filtered_no_agent_count": "INTEGER DEFAULT 0",
            "parse_failed_count": "INTEGER DEFAULT 0",
            "client_file_id": "VARCHAR(100)",
            "file_size": "INTEGER DEFAULT 0",
            "stored_path": "VARCHAR(1000)",
            "status": "VARCHAR(30) DEFAULT 'parsed'",
            "error_stage": "VARCHAR(30)",
            "upload_error": "TEXT",
            "parse_started_at": "DATETIME",
            "parse_completed_at": "DATETIME",
            "attempt_count": "INTEGER DEFAULT 1",
            "updated_at": "DATETIME",
        }
        with engine.begin() as conn:
            for name, definition in additions.items():
                if name not in columns:
                    conn.execute(text(f"ALTER TABLE uploaded_files ADD COLUMN {name} {definition}"))
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_uploaded_files_batch_client ON uploaded_files(batch_id, client_file_id)"))
    if "ai_messages" in inspector.get_table_names():
        columns = {c["name"] for c in inspector.get_columns("ai_messages")}
        if "uploaded_file_id" not in columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE ai_messages ADD COLUMN uploaded_file_id INTEGER"))
    if "audit_logs" in inspector.get_table_names():
        columns = {c["name"] for c in inspector.get_columns("audit_logs")}
        if "request_id" not in columns:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE audit_logs ADD COLUMN request_id VARCHAR(100)"))
    if "qc_slice_knowledge_suggestions" in inspector.get_table_names():
        columns = {c["name"] for c in inspector.get_columns("qc_slice_knowledge_suggestions")}
        additions = {
            "processing_status": "VARCHAR(40) DEFAULT 'pending_entry'",
            "organized_by": "INTEGER",
            "organized_at": "DATETIME",
            "exported_by": "INTEGER",
            "exported_at": "DATETIME",
            "jiuzhang_uploaded_by": "INTEGER",
            "jiuzhang_uploaded_at": "DATETIME",
        }
        with engine.begin() as conn:
            for name, definition in additions.items():
                if name not in columns:
                    conn.execute(text(f"ALTER TABLE qc_slice_knowledge_suggestions ADD COLUMN {name} {definition}"))
    for table_name, additions in {
        "review_assignments": {"claim_source": "VARCHAR(30)", "claimed_at": "DATETIME"},
        "review_assignment_logs": {"operator_role": "VARCHAR(20)", "target_type": "VARCHAR(40)", "target_id": "VARCHAR(160)", "from_value": "TEXT", "to_value": "TEXT", "reason": "TEXT"},
        "review_processing_logs": {"operator_role": "VARCHAR(20)", "target_type": "VARCHAR(40)", "target_id": "VARCHAR(160)", "from_value": "TEXT", "to_value": "TEXT", "reason": "TEXT"},
        "qa_pool_status_logs": {"operator_role": "VARCHAR(20)", "target_type": "VARCHAR(40)", "target_id": "VARCHAR(160)", "from_value": "TEXT", "to_value": "TEXT", "reason": "TEXT"},
    }.items():
        if table_name in inspector.get_table_names():
            columns = {column["name"] for column in inspector.get_columns(table_name)}
            with engine.begin() as conn:
                for name, definition in additions.items():
                    if name not in columns:
                        conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {name} {definition}"))
    if "qc_slice_quality_issues" in inspector.get_table_names():
        columns = {c["name"] for c in inspector.get_columns("qc_slice_quality_issues")}
        additions = {
            "human_tags_json": "TEXT DEFAULT '[]'",
            "human_ai_score": "INTEGER",
            "human_issue_type": "VARCHAR(100)",
            "human_severity": "VARCHAR(30)",
            "human_processing_destination": "VARCHAR(100)",
            "human_review_status": "VARCHAR(30) DEFAULT 'pending_review'",
            "human_review_comment": "TEXT",
            "human_updated_by": "INTEGER",
            "human_updated_at": "DATETIME",
            "qa_source": "VARCHAR(30)",
            "human_qa_pairs": "TEXT",
            "human_standard_questions": "TEXT DEFAULT '[]'",
            "human_standard_answer": "TEXT",
            "human_knowledge_base": "VARCHAR(50)",
            "human_category": "VARCHAR(100)",
            "human_applicable_scope": "TEXT",
            "knowledge_pool_status": "VARCHAR(40)",
            "knowledge_pool_created_at": "DATETIME",
            "knowledge_pool_created_by": "INTEGER",
            "knowledge_pool_organized_by": "INTEGER",
            "knowledge_pool_organized_at": "DATETIME",
            "knowledge_pool_exported_by": "INTEGER",
            "knowledge_pool_exported_at": "DATETIME",
            "knowledge_pool_jiuzhang_uploaded_by": "INTEGER",
            "knowledge_pool_jiuzhang_uploaded_at": "DATETIME",
        }
        with engine.begin() as conn:
            for name, definition in additions.items():
                if name not in columns:
                    conn.execute(text(f"ALTER TABLE qc_slice_quality_issues ADD COLUMN {name} {definition}"))
    if "qc_slice_knowledge_suggestions" in inspector.get_table_names():
        columns = {c["name"] for c in inspector.get_columns("qc_slice_knowledge_suggestions")}
        additions = {
            "human_tags_json": "TEXT DEFAULT '[]'",
            "human_knowledge_base": "VARCHAR(50)",
            "human_qa_pairs": "TEXT",
            "human_standard_questions": "TEXT DEFAULT '[]'",
            "human_standard_answer": "TEXT",
            "human_category": "VARCHAR(100)",
            "human_applicable_scope": "TEXT",
            "human_review_status": "VARCHAR(30) DEFAULT 'pending_review'",
            "human_review_comment": "TEXT",
            "human_updated_by": "INTEGER",
            "human_updated_at": "DATETIME",
        }
        with engine.begin() as conn:
            for name, definition in additions.items():
                if name not in columns:
                    conn.execute(text(f"ALTER TABLE qc_slice_knowledge_suggestions ADD COLUMN {name} {definition}"))
    with engine.begin() as conn:
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_qc_slices_batch_id ON qc_slices(batch_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_sessions_batch_id ON sessions(batch_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_ai_messages_batch_id ON ai_messages(batch_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_uploaded_files_batch_id ON uploaded_files(batch_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_qc_issues_batch_id ON qc_issues(batch_id)"))


def seed_system_dictionaries():
    """Insert missing initial values without changing existing admin choices."""
    from services.dictionaries import DICTIONARY_SEEDS

    db = SessionLocal()
    try:
        for group_key, items in DICTIONARY_SEEDS.items():
            existing = {
                row.value for row in db.query(SystemDictionary.value).filter(
                    SystemDictionary.group_key == group_key,
                ).all()
            }
            for sort_order, value, display_name in items:
                if value not in existing:
                    db.add(SystemDictionary(
                        group_key=group_key,
                        value=value,
                        display_name=display_name,
                        sort_order=sort_order,
                        enabled=True,
                    ))
        db.commit()
    finally:
        db.close()

OVERSEAS_GAME_PRODUCT_MAPPINGS = (
    # match_value, standard game name, region parsed from the compound label
    ("1758005880721", "小小英雄", "日本"),
    ("1751018108611", "主宰世界", "欧美"),
    ("1706161503201", "冒险大作战", "欧美"),
    ("1750648830191", "狩猎使命二推", "港台"),
    ("1700471055791", "狩猎使命", "港台"),
    ("1702448491931", "冒险大作战", "东南亚"),
    ("1756086227971", "妖怪金手指", "港台"),
    ("1703497406541", "冒险大作战", "港台"),
    ("1708239332411", "明日特攻队", "欧美"),
    ("1709980673581", "热血神剑", "港台"),
    ("1719214173731", "少年名将", "东南亚"),
    ("1722863954321", "指尖无双", "港台"),
    ("1723708234451", "指尖无双", "日本"),
    ("1731309411351", "主宰世界", "港台"),
    ("1741052224841", "巨神军师", "东南亚"),
    ("1741779964991", "主宰世界", "日本"),
    ("1741860167481", "明日特攻队2", "欧美"),
    ("1742279879621", "主宰世界", "东南亚"),
    ("1757584260541", "曙光重临", "全球"),
    ("1757937035771", "勇者联盟", "港台"),
    ("1766728349971", "妖怪金手指", "东南亚"),
    ("1768555353861", "妖怪金手指", "日本"),
)


def seed_overseas_game_product_mappings(db):
    """Upsert overseas-pop gameProductId rules into mapping_configs.

    ``game`` stores the standard catalog name; ``target_region`` is the
    region prefix taken from the compound source label.
    """
    for match_value, game_name, target_region in OVERSEAS_GAME_PRODUCT_MAPPINGS:
        exists = db.query(MappingConfig).filter(
            MappingConfig.raw_channel == "官网客服",
            MappingConfig.match_field == "gameProductId",
            MappingConfig.match_value == match_value,
        ).first()
        if exists:
            exists.game = game_name
            exists.target_channel = "官网客服"
            exists.target_region = target_region
            exists.enabled = True
            if not exists.remark:
                exists.remark = "海外普客 gameProductId"
            continue
        db.add(MappingConfig(
            raw_region=None,
            raw_channel="官网客服",
            game=game_name,
            match_field="gameProductId",
            match_value=match_value,
            target_channel="官网客服",
            target_region=target_region,
            enabled=True,
            remark="海外普客 gameProductId",
        ))


def seed_mapping_configs():
    db = SessionLocal()
    try:
        # These explicit source-region rules are used by the M-backend parser
        # without language inference. Existing installations retain all prior
        # mapping rows and receive only missing configuration entries.
        mbackend_defaults = [("东南亚", "东南亚"), ("日本", "日本"), ("台湾香港", "港台"), ("港台", "港台"), ("欧美", "欧美")]
        for raw_region, target_region in mbackend_defaults:
            exists = db.query(MappingConfig).filter(
                MappingConfig.raw_region == raw_region, MappingConfig.raw_channel == "M后台",
                MappingConfig.match_field == "none", MappingConfig.game.is_(None),
            ).first()
            if not exists:
                db.add(MappingConfig(raw_region=raw_region, raw_channel="M后台", target_channel="官网客服", target_region=target_region, remark="M后台问题列表显式地区映射"))
        seed_overseas_game_product_mappings(db)
        db.commit()
        if db.query(MappingConfig).count(): return
        rows = []
        def add(**kw): rows.append(MappingConfig(**kw))
        for raw, ch, reg in [("东南亚","DC","东南亚"),("东南亚","FB","东南亚"),("俄语区","VK","欧美"),("台湾香港","FB","港台"),("台湾香港","LINE","港台"),("台湾香港","M后台","港台"),("日本","LINE","日本"),("日本","M后台","日本")]:
            add(raw_region=raw,target_channel=ch,target_region=reg)
        groups={"DC":(["主宰世界","冒险大作战","勇者联盟","妖怪金手指","小小英雄","巨神军师","明日特攻队2","曙光重临"],"欧美"),"FB":(["冒险大作战","奇迹之剑","巨神军师","明日特攻队2","热血神剑","狩猎使命","闪电突击"],"欧美"),"LINE":(["主宰世界","指尖像素城","指尖无双"],"日本"),"VK":(["主宰世界","冒险大作战","勇者联盟","明日特攻队2","热血神剑"],"欧美"),"M后台":(["明日特攻队2","曙光重临","小小英雄"],"欧美")}
        for ch,(games,reg) in groups.items():
            for game in games: add(raw_region="英语区",raw_channel=ch,game=game,target_channel=ch,target_region=reg)
        for pid,reg in [("100538892569633","东南亚"),("100689465896399","东南亚"),("103637815594197","东南亚"),("107311905223117","东南亚"),("104641912231933","欧美")]: add(raw_region="英语区",raw_channel="FB",game="狩猎使命",match_field="page_id",match_value=pid,target_channel="FB",target_region=reg)
        langs={"主宰世界":[("韩语","韩国"),("日语","日本"),("俄语","欧美"),("英语","欧美")],"妖怪金手指":[("韩语","韩国"),("越南语","东南亚"),("泰语","东南亚"),("印尼语","东南亚")],"勇者联盟":[("韩语","韩国"),("俄语","欧美"),("英语","欧美")],"明日特攻队":[("韩语","韩国"),("俄语","欧美"),("英语","欧美")]}
        for game,pairs in langs.items():
            for lang,reg in pairs: add(raw_region="英语区",raw_channel="M后台",game=game,match_field="language",match_value=lang,target_channel="M后台",target_region=reg)
        add(raw_channel="VIP",target_channel="VIP后台",target_region=None,remark="VIP后台不作为地区")
        db.add_all(rows); db.commit()
    finally: db.close()


def seed_game_ai_configs():
    """Seed AI analysis switches after mapping configs exist."""
    from services.game_ai_config import seed_game_ai_configs as _seed
    _seed()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
