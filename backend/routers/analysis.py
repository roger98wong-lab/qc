"""Excel upload and QC batch routes."""
import asyncio, json, os, re, shutil, threading, time, traceback, uuid
from datetime import datetime
import pandas as pd
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from jose import JWTError, jwt
from pydantic import BaseModel, Field
from sqlalchemy.exc import DatabaseError, IntegrityError, OperationalError
from sqlalchemy import func
from sqlalchemy.orm import Session as DbSession
from auth import get_current_user, require_admin
import config as config_mod
from config import ALGORITHM, ANALYSIS_CONCURRENCY, MAAS_TIMEOUT_SECONDS, MAX_UPLOAD_MB, SECRET_KEY, UPLOAD_DIR
from database import (AiMessage, AnalysisBatch, KbSuggestion, QcIssue, QcSlice,
                      QcSliceKnowledgeSuggestion, QcSliceQualityIssue, Session,
                      Report, SessionLocal, UploadedFile, User, ReviewAssignment, get_db, utcnow,
                      sqlite_error_kind, sqlite_user_message)
from services.analyzer import analyze_slice
from services.excel_parser import detect_channel_from_df, parse_excel_to_sessions
from services.game_ai_config import should_analyze_from_snapshot, slice_creation_fields
from audit import record_audit

router = APIRouter(prefix="/api/analysis", tags=["分析"])
progress_state = {}
ACTIVE_BATCHES: set[int] = set()
START_LOCK = threading.Lock()
STALE_ANALYSIS_GRACE_SECONDS = 30
INTERRUPTED_ANALYSIS_MESSAGE = "分析任务因后端服务重启或进程终止而中断，MaaS 返回结果未落库；请重新上传分析。"
PARSE_CONCURRENCY = 2
PARSE_SEMAPHORE = threading.Semaphore(PARSE_CONCURRENCY)
TERMINAL_BATCH_STATUSES = ("completed", "partial", "failed", "done")
UPLOADABLE_BATCH_STATUSES = ("uploading", "paused", "pending", "parsing")
STARTABLE_BATCH_STATUSES = ("uploading", "pending", "parsing")
PROCESSED_SLICE_STATUSES = ("completed", "partial", "failed", "skipped")
PENDING_SLICE_STATUSES = ("pending", "processing")
ABORT_REASON = "abort"


def _terminal_batch_status(db, batch: AnalysisBatch) -> tuple[str, int, int]:
    """Return the durable batch status and terminal slice count."""
    failed = db.query(QcSlice).filter(
        QcSlice.batch_id == batch.id, QcSlice.analysis_status == "failed"
    ).count()
    successful = db.query(QcSlice).filter(
        QcSlice.batch_id == batch.id,
        QcSlice.analysis_status.in_(["completed", "partial", "skipped"]),
    ).count()
    partial = db.query(QcSlice).filter(
        QcSlice.batch_id == batch.id, QcSlice.analysis_status == "partial"
    ).count()
    terminal = failed + successful
    if failed and successful == 0:
        status = "failed"
    elif failed or partial:
        status = "partial"
    else:
        status = "completed"
    return status, terminal, failed


def _empty_slice_counts() -> dict[str, int]:
    return {"processed_count": 0, "pending_count": 0, "failed_count": 0, "runnable_count": 0}


def _slice_counts_from_status_map(status_counts: dict[str, int]) -> dict[str, int]:
    processed = sum(int(status_counts.get(status) or 0) for status in PROCESSED_SLICE_STATUSES)
    pending = sum(int(status_counts.get(status) or 0) for status in PENDING_SLICE_STATUSES)
    failed = int(status_counts.get("failed") or 0)
    runnable = int(status_counts.get("pending") or 0) + failed
    return {
        "processed_count": processed,
        "pending_count": pending,
        "failed_count": failed,
        "runnable_count": runnable,
    }


def _slice_counts(db, batch_id: int) -> dict[str, int]:
    rows = db.query(QcSlice.analysis_status, func.count(QcSlice.id)).filter(
        QcSlice.batch_id == batch_id,
    ).group_by(QcSlice.analysis_status).all()
    return _slice_counts_from_status_map({status: count for status, count in rows})


def _slice_counts_by_batch(db, batch_ids: list[int]) -> dict[int, dict[str, int]]:
    result = {batch_id: _empty_slice_counts() for batch_id in batch_ids}
    if not batch_ids:
        return result
    rows = db.query(
        QcSlice.batch_id, QcSlice.analysis_status, func.count(QcSlice.id)
    ).filter(QcSlice.batch_id.in_(batch_ids)).group_by(
        QcSlice.batch_id, QcSlice.analysis_status
    ).all()
    grouped: dict[int, dict[str, int]] = {}
    for batch_id, status, count in rows:
        grouped.setdefault(batch_id, {})[status] = count
    for batch_id, status_counts in grouped.items():
        result[batch_id] = _slice_counts_from_status_map(status_counts)
    return result


def _issue_counts_by_batch(db, batch_ids: list[int]) -> dict[int, int]:
    counts = {batch_id: 0 for batch_id in batch_ids}
    if not batch_ids:
        return counts
    legacy = db.query(QcIssue.batch_id, func.count(QcIssue.id)).filter(
        QcIssue.batch_id.in_(batch_ids)
    ).group_by(QcIssue.batch_id).all()
    for batch_id, count in legacy:
        counts[batch_id] = int(count or 0)
    slice_issues = db.query(QcSlice.batch_id, func.count(QcSliceQualityIssue.id)).join(
        QcSliceQualityIssue, QcSliceQualityIssue.slice_id == QcSlice.id
    ).filter(QcSlice.batch_id.in_(batch_ids)).group_by(QcSlice.batch_id).all()
    for batch_id, count in slice_issues:
        counts[batch_id] = counts.get(batch_id, 0) + int(count or 0)
    return counts


def _progress_payload(batch: AnalysisBatch, counts: dict[str, int] | None = None, error: str | None = None) -> dict:
    counts = counts or {}
    # processed_count=0 is valid while failed slices are reclaimed as processing.
    # Do not fall back to the stale analyzed_slices column in that case.
    processed = counts["processed_count"] if "processed_count" in counts else int(batch.analyzed_slices or 0)
    pending = counts["pending_count"] if "pending_count" in counts else 0
    return {
        "total": processed + pending,
        "done": processed,
        "processed_count": processed,
        "pending_count": pending,
        "failed_count": int(counts.get("failed_count") or 0),
        "runnable_count": int(counts.get("runnable_count") or 0),
        "status": batch.status,
        "error": error if error is not None else (batch.error_msg or ""),
    }


def _sync_progress(db, batch: AnalysisBatch, error: str | None = None) -> dict:
    counts = _slice_counts(db, batch.id)
    batch.analyzed_slices = counts["processed_count"]
    batch.analyzed_count = counts["processed_count"]
    batch.updated_at = utcnow()
    payload = _progress_payload(batch, counts, error)
    progress_state[batch.id] = payload
    return payload


def _is_terminal_status(status: str | None) -> bool:
    return status in TERMINAL_BATCH_STATUSES


def _is_abort_requested(batch: AnalysisBatch) -> bool:
    return (batch.control_reason or "") == ABORT_REASON


def _settle_aborted_batch(db, batch: AnalysisBatch, message: str | None = None) -> None:
    """Terminate a batch from current slice results. Pending slices stay pending."""
    status, terminal, failed = _terminal_batch_status(db, batch)
    batch.status = status
    batch.analyzed_slices = terminal
    batch.analyzed_count = terminal
    batch.control_reason = ABORT_REASON
    batch.updated_at = utcnow()
    if message:
        batch.error_msg = message
    elif failed:
        batch.error_msg = f"{failed} 个切片发生技术失败"
    db.commit()
    _sync_progress(db, batch)


def _mark_interrupted_batch(db, batch: AnalysisBatch, message: str = INTERRUPTED_ANALYSIS_MESSAGE) -> bool:
    """Fail in-flight processing slices after a worker dies; leave pending intact.

    A dead analyzing worker must never be auto-completed: pause so the operator
    can resume remaining pending/failed slices. Abort still settles to a terminal status.
    """
    processing = db.query(QcSlice).filter(
        QcSlice.batch_id == batch.id,
        QcSlice.analysis_status == "processing",
    ).all()
    changed = False
    now = utcnow()
    for slice_row in processing:
        slice_row.analysis_status = "failed"
        slice_row.error_code = "worker_interrupted"
        slice_row.error_message = message
        slice_row.completed_at = now
        changed = True
    if _is_abort_requested(batch) or _is_terminal_status(batch.status):
        _settle_aborted_batch(db, batch, message if changed else None)
        return True
    if batch.status == "analyzing":
        batch.status = "paused"
        batch.error_msg = message
        batch.updated_at = utcnow()
        changed = True
    db.commit()
    _sync_progress(db, batch)
    return changed


def reconcile_stale_batches(db: DbSession | None = None, batch_id: int | None = None) -> int:
    """Reconcile batches left in-flight by a previous backend process.

    Timed-out processing slices are failed. A stuck analyzing row with a dead
    worker is paused (never auto-completed) so start/resume can continue later.
    Pass ``batch_id`` from operator actions; listing and progress stay read-only.
    """
    local = db or SessionLocal()
    owned = db is None
    changed = 0
    try:
        now = utcnow()
        query = local.query(AnalysisBatch).filter(
            AnalysisBatch.status.in_(["analyzing", "paused", "uploading", "pending", "parsing"])
        )
        if batch_id is not None:
            query = query.filter(AnalysisBatch.id == batch_id)
        batches = query.all()
        timeout = max(int(config_mod.MAAS_TIMEOUT_SECONDS), 1) + STALE_ANALYSIS_GRACE_SECONDS
        for batch in batches:
            if batch.id in ACTIVE_BATCHES:
                continue
            processing = local.query(QcSlice).filter(
                QcSlice.batch_id == batch.id,
                QcSlice.analysis_status == "processing",
            ).all()
            stale_processing = False
            for row in processing:
                age = (now - (row.started_at or batch.created_at or now)).total_seconds()
                # No live worker owns these claims. Release immediately so
                # resume can pick them up; do not wait for MaaS timeout.
                # After timeout, mark failed instead of leaving them processing.
                if age >= timeout:
                    row.analysis_status = "failed"
                    row.error_code = "worker_interrupted"
                    row.error_message = INTERRUPTED_ANALYSIS_MESSAGE
                    row.completed_at = now
                else:
                    row.analysis_status = "pending"
                    row.error_code = None
                    row.error_message = None
                    row.started_at = None
                    row.completed_at = None
                stale_processing = True
                changed += 1
            if _is_abort_requested(batch):
                _settle_aborted_batch(local, batch)
                changed += 1
                continue
            if batch.status == "analyzing":
                batch.status = "paused"
                batch.updated_at = now
                if stale_processing:
                    batch.error_msg = INTERRUPTED_ANALYSIS_MESSAGE
                changed += 1
            _sync_progress(local, batch)
        local.commit()
        return changed
    finally:
        if owned:
            local.close()

def serialize(batch, db, counts=None, issue_count=None):
    if counts is None:
        counts = _slice_counts(db, batch.id)
    if issue_count is None:
        issue_count = db.query(QcIssue).filter(QcIssue.batch_id == batch.id).count()
        issue_count += db.query(QcSliceQualityIssue).join(QcSlice).filter(QcSlice.batch_id == batch.id).count()
    return {"id":batch.id,"batch_id":batch.id,"name":batch.name,"status":batch.status,
            "total_ai_msgs":batch.total_ai_msgs or 0,"analyzed_count":counts["processed_count"],
            "total_slices":batch.total_slices or 0,"analyzed_slices":counts["processed_count"],
            "processed_count": counts["processed_count"],
            "pending_count": counts["pending_count"],
            "failed_count": counts["failed_count"],
            "runnable_count": counts["runnable_count"],
            "issue_count":issue_count,"error_msg":batch.error_msg,
            "updated_at": batch.updated_at.isoformat() if batch.updated_at else None,
            "created_at":batch.created_at.isoformat() if batch.created_at else ""}


class BatchInitRequest(BaseModel):
    name: str = ""
    expected_file_count: int = Field(ge=1, le=500)


class BatchControlRequest(BaseModel):
    reason: str = ""


def _owned_upload_batch(db: DbSession, batch_id: int, user: User) -> AnalysisBatch:
    batch = db.query(AnalysisBatch).filter(AnalysisBatch.id == batch_id).first()
    if not batch:
        raise HTTPException(404, "批次不存在")
    if batch.created_by != user.id:
        raise HTTPException(403, "无权操作该上传批次")
    return batch


def _serialize_upload_file(row: UploadedFile) -> dict:
    try:
        warnings = json.loads(row.parse_warnings or "[]")
    except (TypeError, ValueError):
        warnings = []
    return {
        "file_upload_id": row.id,
        "client_file_id": row.client_file_id,
        "filename": row.filename,
        "file_size": row.file_size or 0,
        "status": row.status or "parsed",
        "error_stage": row.error_stage,
        "error": (row.upload_error or row.parse_error) if row.status in {"upload_failed", "parse_failed"} else None,
        "upload_error": row.upload_error,
        "parse_error": row.parse_error,
        "attempt_count": row.attempt_count or 1,
        "channel": row.channel or "UNKNOWN",
        "row_count": row.row_count or 0,
        "dedup_row_count": row.dedup_row_count or 0,
        "valid_message_count": row.valid_message_count or 0,
        "session_count": row.session_count or 0,
        "slice_count": row.slice_count or 0,
        "warning_count": row.warning_count or 0,
        "parse_warnings": warnings,
        "filtered_no_agent_count": row.filtered_no_agent_count or 0,
        "parse_failed_count": row.parse_failed_count or 0,
        "parse_started_at": row.parse_started_at.isoformat() if row.parse_started_at else None,
        "parse_completed_at": row.parse_completed_at.isoformat() if row.parse_completed_at else None,
    }


def _upload_status_payload(db: DbSession, batch: AnalysisBatch) -> dict:
    files = db.query(UploadedFile).filter(UploadedFile.batch_id == batch.id).order_by(UploadedFile.id).all()
    statuses = [row.status or "parsed" for row in files]
    expected = batch.expected_file_count or len(files)
    summary = {
        "total": expected,
        "registered": len(files),
        "uploaded": sum(status in {"parsing", "parsed", "parse_failed"} for status in statuses),
        "uploading": 0,
        "upload_failed": statuses.count("upload_failed"),
        "parsing": statuses.count("parsing"),
        "parsed": statuses.count("parsed"),
        "parse_failed": statuses.count("parse_failed"),
    }
    counts = _slice_counts(db, batch.id)
    return {
        "batch_id": batch.id,
        "name": batch.name,
        "status": batch.status,
        "expected_file_count": expected,
        "upload_finalized_at": batch.upload_finalized_at.isoformat() if batch.upload_finalized_at else None,
        "processed_count": counts["processed_count"],
        "pending_count": counts["pending_count"],
        "failed_count": counts["failed_count"],
        "runnable_count": counts["runnable_count"],
        "summary": summary,
        "files": [_serialize_upload_file(row) for row in files],
    }


def _refresh_open_batch_status(db: DbSession, batch: AnalysisBatch) -> None:
    """File parsing is background work and must not hijack analyzing/paused/terminal."""
    if _is_terminal_status(batch.status) or batch.status in {"analyzing", "paused"}:
        return
    if batch.status in {"uploading", "pending", "parsing"}:
        batch.status = "uploading"
        batch.updated_at = utcnow()


def _discard_partial_parse(db: DbSession, file_id: int) -> None:
    """Drop rows already committed for a file that later failed mid-parse."""
    db.query(AiMessage).filter(AiMessage.uploaded_file_id == file_id).delete(synchronize_session=False)
    db.query(QcSlice).filter(QcSlice.uploaded_file_id == file_id).delete(synchronize_session=False)
    db.query(Session).filter(Session.uploaded_file_id == file_id).delete(synchronize_session=False)


def _set_file_failure(db: DbSession, file_id: int, message: str, stage: str = "parse") -> UploadedFile:
    db.rollback()
    row = db.query(UploadedFile).filter(UploadedFile.id == file_id).first()
    if not row:
        raise HTTPException(404, "上传文件记录不存在")
    if stage == "parse":
        _discard_partial_parse(db, file_id)
    row.status = "upload_failed" if stage == "upload" else "parse_failed"
    row.error_stage = stage
    if stage == "upload":
        row.upload_error = message
    else:
        row.parse_error = message
        row.parse_completed_at = utcnow()
    row.updated_at = utcnow()
    batch = db.query(AnalysisBatch).filter(AnalysisBatch.id == row.batch_id).first()
    if batch:
        _refresh_open_batch_status(db, batch)
    db.commit()
    db.refresh(row)
    return row


def _parse_registered_file(db: DbSession, batch: AnalysisBatch, uploaded: UploadedFile) -> UploadedFile:
    """Parse without holding a write lock, then persist in 200-row commits."""
    batch_id = batch.id
    uploaded_id = uploaded.id
    target = uploaded.stored_path or ""
    # ``refresh`` and attribute reads may have started a read transaction.
    # Close it before pandas/openpyxl performs potentially slow filesystem IO.
    db.rollback()
    file_parse_stats: dict = {}
    try:
        sheets = pd.read_excel(target, sheet_name=None)
        frames = [frame for frame in sheets.values() if not frame.empty]
        channel = detect_channel_from_df(frames[0]) if frames else "UNKNOWN"
        rows = sum(len(frame) for frame in frames)
        sessions, _suggestions, parse_error = parse_excel_to_sessions(
            target, channel, set(), file_parse_stats
        )
    except Exception as exc:
        return _set_file_failure(db, uploaded_id, str(exc) or "文件解析失败")

    if not sessions:
        return _set_file_failure(db, uploaded_id, parse_error or "未识别到可质检的会话或切片")

    try:
        # Refresh after the slow parse. No SQLite write transaction was held
        # while pandas/openpyxl read the workbook.
        db.rollback()
        uploaded = db.query(UploadedFile).filter(UploadedFile.id == uploaded_id).first()
        batch = db.query(AnalysisBatch).filter(AnalysisBatch.id == batch_id).first()
        if not uploaded or not batch:
            raise RuntimeError("批次或上传文件记录不存在")
        if uploaded.status != "parsing":
            raise RuntimeError("文件状态已变化，请刷新后重试")

        parse_stats = file_parse_stats or (
            next((item.get("_parse_stats") for item in sessions if isinstance(item.get("_parse_stats"), dict)), {}) or {}
        )
        message_count = 0
        PARSE_FLUSH_SIZE = 200
        pending_in_txn = 0
        for item in sessions:
            session = Session(
                batch_id=batch.id, uploaded_file_id=uploaded.id,
                event_id=item.get("session_uid") or item.get("conversation_key") or "",
                channel=item.get("channel"), game=item.get("game"), region=item.get("region"),
                conversation_key=item.get("conversation_key") or item.get("session_uid"),
                started_at=item.get("reply_time"), source_url=item.get("session_link") or "",
                full_transcript=item.get("full_transcript") or "", source_sheet=item.get("_source_sheet"),
                raw_channel=item.get("_raw_channel"), raw_region=item.get("_raw_region"),
                source_reply_agents_json=json.dumps(item.get("_reply_agents") or [], ensure_ascii=False),
                raw_metadata_json=item.get("full_transcript") or "",
                parse_warnings_json=json.dumps(item.get("_parse_warnings") or [], ensure_ascii=False),
            )
            db.add(session)
            db.flush()
            slice_payload = item.get("slice_payload")
            if not isinstance(slice_payload, dict):
                try:
                    slice_payload = json.loads((item.get("ai_messages") or [{}])[0].get("context", "{}"))
                except Exception:
                    slice_payload = {"messages": []}
            canonical_slice_id = (
                f"mbackend:{batch.id}:{item.get('session_uid')}"
                if item.get("_source_format") == "m_backend"
                else item.get("slice_id")
                if item.get("_source_format") == "overseas_in_app"
                else f"b{batch.id}-f{uploaded.id}-s{session.id}"
            )
            normalized_messages = []
            for sequence, message_item in enumerate(slice_payload.get("messages") or [], 1):
                normalized = dict(message_item)
                normalized["message_id"] = f"{canonical_slice_id}-m{sequence}"
                normalized["sequence"] = sequence
                normalized_messages.append(normalized)
            db.add(QcSlice(
                batch_id=batch.id, uploaded_file_id=uploaded.id, session_id=session.id,
                slice_id=canonical_slice_id, channel=item.get("channel"), game=item.get("game"),
                region=item.get("region"),
                messages_json=json.dumps(normalized_messages, ensure_ascii=False, separators=(",", ":")),
                source_format=item.get("_source_format"),
                source_sheet=item.get("_source_sheet"), source_problem_id=item.get("session_uid"),
                warnings_json=json.dumps(item.get("_parse_warnings") or [], ensure_ascii=False),
                **slice_creation_fields(db, item.get("game"), item.get("region")),
            ))
            for msg in item.get("ai_messages", []):
                db.add(AiMessage(
                    session_id=session.id, batch_id=batch.id, uploaded_file_id=uploaded.id,
                    msg_time=msg.get("msg_time"), content=msg.get("content", ""),
                    context=msg.get("context", ""), language=msg.get("language"),
                ))
                message_count += 1
            pending_in_txn += 1
            if pending_in_txn >= PARSE_FLUSH_SIZE:
                db.commit()
                pending_in_txn = 0
                uploaded = db.query(UploadedFile).filter(UploadedFile.id == uploaded_id).first()
                batch = db.query(AnalysisBatch).filter(AnalysisBatch.id == batch_id).first()
                if not uploaded or not batch:
                    raise RuntimeError("批次或上传文件记录不存在")

        uploaded.channel = channel
        uploaded.row_count = rows
        uploaded.ai_msg_count = message_count
        uploaded.dedup_row_count = int(parse_stats.get("dedup_rows") or 0)
        uploaded.valid_message_count = int(parse_stats.get("valid_messages") or 0)
        uploaded.session_count = len({str(item.get("session_uid") or "").split(":part-")[0] for item in sessions})
        uploaded.slice_count = len(sessions)
        uploaded.warning_count = int(parse_stats.get("warnings") or 0)
        uploaded.parse_warnings = json.dumps(
            parse_stats.get("warning_messages") or next((item.get("_parse_warnings") for item in sessions if item.get("_parse_warnings")), []),
            ensure_ascii=False,
        )
        uploaded.filtered_no_agent_count = int(parse_stats.get("filtered_no_agent") or 0)
        uploaded.parse_failed_count = int(parse_stats.get("parse_failed") or 0)
        uploaded.parse_error = parse_error
        uploaded.status = "parsed"
        uploaded.error_stage = None
        uploaded.parse_completed_at = utcnow()
        uploaded.updated_at = utcnow()
        batch.total_ai_msgs = (batch.total_ai_msgs or 0) + message_count
        batch.total_slices = (batch.total_slices or 0) + len(sessions)
        _refresh_open_batch_status(db, batch)
        db.commit()
        db.refresh(uploaded)
        _sync_progress(db, batch)
        return uploaded
    except (OperationalError, DatabaseError) as exc:
        kind = sqlite_error_kind(exc)
        if kind == "malformed":
            db.rollback()
            raise HTTPException(500, sqlite_user_message(exc, "解析入库")) from exc
        _set_file_failure(db, uploaded_id, "数据库繁忙，请稍后重试解析")
        raise HTTPException(503, sqlite_user_message(exc, "解析入库")) from exc
    except Exception as exc:
        return _set_file_failure(db, uploaded_id, str(exc) or "解析结果入库失败")


@router.post("/batch/init")
def init_upload_batch(payload: BatchInitRequest, user: User = Depends(require_admin), db: DbSession = Depends(get_db)):
    try:
        batch = AnalysisBatch(
            name=payload.name.strip() or f"质检_{utcnow():%Y%m%d_%H%M%S}",
            created_by=user.id, status="uploading", source_task_id="upload", source_region="unknown",
            expected_file_count=payload.expected_file_count,
        )
        db.add(batch)
        db.commit()
        db.refresh(batch)
        return _upload_status_payload(db, batch)
    except OperationalError as exc:
        db.rollback()
        raise HTTPException(503, "数据库繁忙，暂时无法创建批次，请稍后重试") from exc


@router.post("/batch/{batch_id}/files")
def upload_batch_file(
    batch_id: int,
    background_tasks: BackgroundTasks,
    client_file_id: str = Form(...), file: UploadFile = File(...),
    user: User = Depends(require_admin), db: DbSession = Depends(get_db),
):
    batch = _owned_upload_batch(db, batch_id, user)
    if batch.status == "analyzing" or _is_abort_requested(batch):
        raise HTTPException(409, "分析中或已终止，不能追加文件")
    if _is_terminal_status(batch.status) or batch.status not in UPLOADABLE_BATCH_STATUSES:
        raise HTTPException(409, "该批次已终止或已完成，不能继续追加文件")
    key = client_file_id.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", key):
        raise HTTPException(400, "client_file_id 无效")
    existing = db.query(UploadedFile).filter(
        UploadedFile.batch_id == batch.id, UploadedFile.client_file_id == key
    ).first()
    if existing and existing.status in {"parsed", "parsing", "parse_failed"}:
        if existing.status == "parsed":
            return _serialize_upload_file(existing)
        detail = "文件正在解析" if existing.status == "parsing" else "文件解析失败，请使用重试解析接口"
        raise HTTPException(409, detail)

    filename = os.path.basename(file.filename or "upload.xlsx")
    if not filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(400, "仅支持 .xlsx 或 .xls 文件")
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    target = os.path.join(UPLOAD_DIR, f"{batch.id}_{key}_{filename}")
    max_bytes = MAX_UPLOAD_MB * 1024 * 1024
    size = 0
    try:
        with open(target, "wb") as handle:
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError(f"文件超过 {MAX_UPLOAD_MB}MB 限制")
                handle.write(chunk)
    except Exception as exc:
        if os.path.exists(target):
            os.remove(target)
        raise HTTPException(413 if isinstance(exc, ValueError) else 400, str(exc) or "文件上传失败") from exc

    try:
        if existing:
            uploaded = existing
            uploaded.filename = filename
            uploaded.file_size = size
            uploaded.stored_path = target
            uploaded.status = "parsing"
            uploaded.error_stage = None
            uploaded.upload_error = None
            uploaded.parse_error = None
            uploaded.attempt_count = (uploaded.attempt_count or 1) + 1
        else:
            uploaded = UploadedFile(
                batch_id=batch.id, filename=filename, client_file_id=key, file_size=size,
                stored_path=target, status="parsing", parse_started_at=utcnow(), attempt_count=1,
            )
            db.add(uploaded)
        uploaded.parse_started_at = utcnow()
        uploaded.updated_at = utcnow()
        if batch.status in {"pending", "parsing"}:
            batch.status = "uploading"
        db.commit()
        db.refresh(uploaded)
    except IntegrityError as exc:
        db.rollback()
        duplicate = db.query(UploadedFile).filter(
            UploadedFile.batch_id == batch.id, UploadedFile.client_file_id == key
        ).first()
        if duplicate and duplicate.status == "parsed":
            return _serialize_upload_file(duplicate)
        raise HTTPException(409, "该文件请求已提交，请刷新批次状态") from exc
    except OperationalError as exc:
        db.rollback()
        raise HTTPException(503, "数据库繁忙，文件已传输但暂未登记，请稍后重试") from exc
    background_tasks.add_task(_parse_file_job, batch.id, uploaded.id)
    return _serialize_upload_file(uploaded)


@router.get("/batch/{batch_id}/upload-status")
def get_upload_status(batch_id: int, user: User = Depends(require_admin), db: DbSession = Depends(get_db)):
    return _upload_status_payload(db, _owned_upload_batch(db, batch_id, user))


@router.post("/batch/{batch_id}/files/{file_id}/retry-parse")
def retry_file_parse(
    batch_id: int, file_id: int,
    background_tasks: BackgroundTasks,
    user: User = Depends(require_admin), db: DbSession = Depends(get_db),
):
    batch = _owned_upload_batch(db, batch_id, user)
    if batch.status == "analyzing":
        raise HTTPException(409, "分析中不能重试解析，请先暂停")
    if _is_terminal_status(batch.status) or batch.status not in UPLOADABLE_BATCH_STATUSES:
        raise HTTPException(409, "该批次已终止或已完成，不能重试解析")
    uploaded = db.query(UploadedFile).filter(
        UploadedFile.id == file_id, UploadedFile.batch_id == batch.id
    ).first()
    if not uploaded:
        raise HTTPException(404, "上传文件记录不存在")
    if uploaded.status == "parsed":
        return _serialize_upload_file(uploaded)
    if uploaded.status != "parse_failed":
        raise HTTPException(409, "当前文件状态不能重试解析")
    if not uploaded.stored_path or not os.path.isfile(uploaded.stored_path):
        raise HTTPException(409, "服务端原文件不存在，请重新选择文件上传")
    _discard_partial_parse(db, uploaded.id)
    uploaded.status = "parsing"
    uploaded.error_stage = None
    uploaded.parse_error = None
    uploaded.parse_started_at = utcnow()
    uploaded.parse_completed_at = None
    uploaded.attempt_count = (uploaded.attempt_count or 1) + 1
    if batch.status in {"pending", "parsing"}:
        batch.status = "uploading"
    db.commit()
    db.refresh(uploaded)
    background_tasks.add_task(_parse_file_job, batch.id, uploaded.id)
    return _serialize_upload_file(uploaded)


@router.post("/batch/{batch_id}/finalize-upload")
def finalize_upload(batch_id: int, user: User = Depends(require_admin), db: DbSession = Depends(get_db)):
    """Kept for compatibility. Finalize no longer locks append or auto-starts analysis."""
    batch = _owned_upload_batch(db, batch_id, user)
    if _is_terminal_status(batch.status):
        raise HTTPException(409, "该批次已终止或已完成")
    return _upload_status_payload(db, batch)

@router.post("/batch")
def create_batch(name: str=Form(""), files: list[UploadFile]=File(...), user: User=Depends(require_admin), db: DbSession=Depends(get_db)):
    if not files: raise HTTPException(400, "请至少上传一个 Excel 文件")
    os.makedirs(UPLOAD_DIR,exist_ok=True)
    batch=AnalysisBatch(name=name.strip() or f"质检_{utcnow():%Y%m%d_%H%M%S}",created_by=user.id,status="parsing",source_task_id="upload",source_region="unknown")
    db.add(batch); db.flush()
    # Persist the batch marker before slow pandas/openpyxl work. This keeps a
    # large multipart upload from holding one SQLite write transaction while
    # the first file is parsed, so login/read requests remain responsive.
    db.commit(); db.refresh(batch)
    total=0; errors=[]; overseas_seen_keys: set[str] = set()
    for upload in files:
        filename=os.path.basename(upload.filename or "upload.xlsx")
        if not filename.lower().endswith((".xlsx",".xls")): errors.append(f"{filename}: 仅支持 Excel 文件"); continue
        target=os.path.join(UPLOAD_DIR,f"{batch.id}_{utcnow():%H%M%S%f}_{filename}")
        with open(target,"wb") as handle: shutil.copyfileobj(upload.file,handle)
        file_parse_stats = {}
        try:
            sheets=pd.read_excel(target,sheet_name=None); frames=[x for x in sheets.values() if not x.empty]
            channel=detect_channel_from_df(frames[0]) if frames else "UNKNOWN"; rows=sum(len(x) for x in frames)
            sessions,suggestions,error=parse_excel_to_sessions(target,channel,overseas_seen_keys,file_parse_stats)
        except Exception as exc: channel,rows,sessions,suggestions,error="UNKNOWN",0,[],[],str(exc)
        parse_stats = file_parse_stats if 'file_parse_stats' in locals() else (next((item.get("_parse_stats") for item in sessions if isinstance(item.get("_parse_stats"), dict)), {}) or {})
        uploaded=UploadedFile(
            batch_id=batch.id, filename=filename, client_file_id=None,
            stored_path=target, file_size=os.path.getsize(target) if os.path.exists(target) else 0,
            status="parsing", channel=channel, row_count=rows,
            ai_msg_count=sum(len(x.get("ai_messages",[])) for x in sessions), parse_error=error,
            dedup_row_count=int(parse_stats.get("dedup_rows") or 0),
            valid_message_count=int(parse_stats.get("valid_messages") or 0),
            session_count=len({str(item.get("session_uid") or "").split(":part-")[0] for item in sessions}),
            slice_count=len(sessions), warning_count=int(parse_stats.get("warnings") or 0),
            parse_warnings=json.dumps(parse_stats.get("warning_messages") or next((item.get("_parse_warnings") for item in sessions if item.get("_parse_warnings")), []), ensure_ascii=False),
            filtered_no_agent_count=int(parse_stats.get("filtered_no_agent") or 0), parse_failed_count=int(parse_stats.get("parse_failed") or 0),
        )
        db.add(uploaded); db.flush()
        if error: errors.append(f"{filename}: {error}")
        for item in sessions:
            session=Session(
                batch_id=batch.id,
                event_id=item.get("session_uid") or item.get("conversation_key") or "",
                channel=item.get("channel"),
                game=item.get("game"),
                region=item.get("region"),
                conversation_key=item.get("conversation_key") or item.get("session_uid"),
                started_at=item.get("reply_time"),
                source_url=item.get("session_link") or "",
                full_transcript=item.get("full_transcript") or "",
                source_sheet=item.get("_source_sheet"), raw_channel=item.get("_raw_channel"),
                raw_region=item.get("_raw_region"), source_reply_agents_json=json.dumps(item.get("_reply_agents") or [], ensure_ascii=False),
                raw_metadata_json=item.get("full_transcript") or "", parse_warnings_json=json.dumps(item.get("_parse_warnings") or [], ensure_ascii=False),
            )
            db.add(session); db.flush()
            # Legacy rows remain one prepared slice. Overseas in-app exports
            # arrive as session-based parts; both paths use the same compact
            # MaaS payload after parsing.
            slice_payload = item.get("slice_payload")
            if not isinstance(slice_payload, dict):
                try:
                    slice_payload = json.loads((item.get("ai_messages") or [{}])[0].get("context", "{}"))
                except Exception:
                    slice_payload = {"schema_version": "1.0.0", "slice_id": item.get("session_uid") or str(session.id),
                                     "channel": item.get("channel"), "game": item.get("game"), "region": item.get("region"), "messages": []}
            slice_payload.pop("language", None)
            canonical_slice_id = (f"mbackend:{batch.id}:{item.get('session_uid')}" if item.get("_source_format") == "m_backend" else item.get("slice_id") if item.get("_source_format") == "overseas_in_app" else f"b{batch.id}-f{uploaded.id}-s{session.id}")
            normalized_messages = []
            for sequence, message in enumerate(slice_payload.get("messages") or [], 1):
                normalized = dict(message)
                normalized["message_id"] = f"{canonical_slice_id}-m{sequence}"
                normalized["sequence"] = sequence
                normalized_messages.append(normalized)
            db.add(QcSlice(batch_id=batch.id, session_id=session.id,
                           slice_id=canonical_slice_id,
                           channel=item.get("channel"), game=item.get("game"), region=item.get("region"),
                           messages_json=json.dumps(normalized_messages, ensure_ascii=False, separators=(",", ":")),
                           source_format=item.get("_source_format"),
                           source_sheet=item.get("_source_sheet"), source_problem_id=item.get("session_uid"),
                           warnings_json=json.dumps(item.get("_parse_warnings") or [], ensure_ascii=False),
                           **slice_creation_fields(db, item.get("game"), item.get("region"))))
            for msg in item.get("ai_messages",[]):
                db.add(AiMessage(session_id=session.id,batch_id=batch.id,msg_time=msg.get("msg_time"),content=msg.get("content",""),context=msg.get("context",""),language=msg.get("language"))); total+=1
        # The parser's legacy keyword suggestions are intentionally ignored
        # for new batches. Knowledge suggestions now come only from MaaS and
        # may only reference human_agent messages.
        batch.total_slices = (batch.total_slices or 0) + len(sessions)
        uploaded.status = "parsed" if sessions else "parse_failed"
        uploaded.error_stage = None if sessions else "parse"
        uploaded.parse_completed_at = utcnow()
        # Release the per-file write lock before parsing the next upload. A
        # 48-file stress test must not hold one giant SQLite transaction and
        # starve login/health/history requests for the whole duration.
        db.commit()
    batch.total_ai_msgs = total
    batch.status = "pending" if batch.total_slices else "failed"
    batch.error_msg = "; ".join(errors) if errors else (None if batch.total_slices else "未识别到可质检的切片")
    db.commit(); db.refresh(batch)
    result = serialize(batch,db)
    result["files"] = [{
        "filename": row.filename, "channel": row.channel, "row_count": row.row_count or 0,
        "dedup_row_count": row.dedup_row_count or 0, "valid_message_count": row.valid_message_count or 0,
        "session_count": row.session_count or 0, "slice_count": row.slice_count or 0,
        "warning_count": row.warning_count or 0, "filtered_no_agent_count": row.filtered_no_agent_count or 0,
        "parse_failed_count": row.parse_failed_count or 0, "parse_warnings": json.loads(row.parse_warnings or "[]"), "parse_error": row.parse_error,
    } for row in db.query(UploadedFile).filter(UploadedFile.batch_id == batch.id).all()]
    return result

def _require_batch(db: DbSession, batch_id: int) -> AnalysisBatch:
    batch = db.query(AnalysisBatch).filter(AnalysisBatch.id == batch_id).first()
    if not batch:
        raise HTTPException(404, "批次不存在")
    return batch


def _launch_worker(background_tasks: BackgroundTasks, batch: AnalysisBatch, db: DbSession):
    if batch.id in ACTIVE_BATCHES:
        raise HTTPException(409, "批次正在分析中")
    ACTIVE_BATCHES.add(batch.id)
    _sync_progress(db, batch, "")
    background_tasks.add_task(run_sync, batch.id)
    return {"message": "质检已启动", "batch_id": batch.id, **serialize(batch, db)}


@router.post("/batch/{batch_id}/start")
def start_batch(
    batch_id: int,
    background_tasks: BackgroundTasks,
    _: User = Depends(require_admin),
    db: DbSession = Depends(get_db),
):
    reconcile_stale_batches(db, batch_id)
    db.expire_all()
    batch = _require_batch(db, batch_id)
    if batch_id in ACTIVE_BATCHES or batch.status == "analyzing":
        raise HTTPException(409, "批次正在分析中")
    if batch.status == "paused":
        raise HTTPException(409, "批次已暂停，请使用重启")
    if _is_terminal_status(batch.status) or _is_abort_requested(batch):
        raise HTTPException(409, "批次已终止或已完成，不能再开始")
    counts = _slice_counts(db, batch_id)
    if counts["runnable_count"] < 1:
        raise HTTPException(400, "没有可分析切片")

    claimed = db.query(AnalysisBatch).filter(
        AnalysisBatch.id == batch_id,
        AnalysisBatch.status.in_(STARTABLE_BATCH_STATUSES),
    ).update({
        AnalysisBatch.status: "analyzing",
        AnalysisBatch.error_msg: None,
        AnalysisBatch.control_reason: None,
        AnalysisBatch.updated_at: utcnow(),
    }, synchronize_session=False)
    if claimed != 1:
        raise HTTPException(409, "批次已被其他任务启动，请刷新后重试")
    db.expire_all()
    batch = _require_batch(db, batch_id)
    db.commit()
    return _launch_worker(background_tasks, batch, db)


@router.post("/batch/{batch_id}/pause")
def pause_batch(batch_id: int, _: User = Depends(require_admin), db: DbSession = Depends(get_db)):
    batch = _require_batch(db, batch_id)
    batch = _require_batch(db, batch_id)
    if _is_terminal_status(batch.status) or _is_abort_requested(batch):
        raise HTTPException(409, "批次已终止或已完成，不能暂停")
    if batch.status == "paused":
        return serialize(batch, db)
    claimed = db.query(AnalysisBatch).filter(
        AnalysisBatch.id == batch_id,
        AnalysisBatch.status == "analyzing",
    ).update({
        AnalysisBatch.status: "paused",
        AnalysisBatch.updated_at: utcnow(),
    }, synchronize_session=False)
    if claimed != 1:
        raise HTTPException(409, "当前状态不能暂停")
    db.expire_all()
    batch = _require_batch(db, batch_id)
    db.commit()
    _sync_progress(db, batch)
    return serialize(batch, db)


@router.post("/batch/{batch_id}/resume")
def resume_batch(
    batch_id: int,
    background_tasks: BackgroundTasks,
    _: User = Depends(require_admin),
    db: DbSession = Depends(get_db),
):
    batch = _require_batch(db, batch_id)
    if batch_id in ACTIVE_BATCHES:
        raise HTTPException(409, "批次正在分析中")
    if _is_terminal_status(batch.status) or _is_abort_requested(batch):
        raise HTTPException(409, "批次已终止或已完成，不能重启")
    if batch.status == "analyzing":
        # A previous worker died but left analyzing in the DB. Reconcile to
        # paused so the operator can resume remaining pending/failed slices.
        reconcile_stale_batches(db, batch_id)
        db.expire_all()
        batch = _require_batch(db, batch_id)
        if batch_id in ACTIVE_BATCHES:
            raise HTTPException(409, "批次正在分析中")
        if batch.status == "analyzing":
            raise HTTPException(409, "批次正在分析中")
    if batch.status != "paused":
        raise HTTPException(409, "只有暂停中的批次可以重启")
    counts = _slice_counts(db, batch_id)
    if counts["runnable_count"] < 1:
        raise HTTPException(400, "没有可分析切片")
    claimed = db.query(AnalysisBatch).filter(
        AnalysisBatch.id == batch_id,
        AnalysisBatch.status == "paused",
    ).update({
        AnalysisBatch.status: "analyzing",
        AnalysisBatch.error_msg: None,
        AnalysisBatch.updated_at: utcnow(),
    }, synchronize_session=False)
    if claimed != 1:
        raise HTTPException(409, "批次已被其他任务启动，请刷新后重试")
    db.expire_all()
    batch = _require_batch(db, batch_id)
    db.commit()
    return _launch_worker(background_tasks, batch, db)


@router.post("/batch/{batch_id}/abort")
def abort_batch(batch_id: int, _: User = Depends(require_admin), db: DbSession = Depends(get_db)):
    batch = _require_batch(db, batch_id)
    if _is_terminal_status(batch.status):
        raise HTTPException(409, "批次已终止或已完成")
    if batch.status == "analyzing" and batch_id in ACTIVE_BATCHES:
        claimed = db.query(AnalysisBatch).filter(
            AnalysisBatch.id == batch_id,
            AnalysisBatch.status == "analyzing",
        ).update({
            AnalysisBatch.control_reason: ABORT_REASON,
            AnalysisBatch.updated_at: utcnow(),
        }, synchronize_session=False)
        if claimed != 1:
            raise HTTPException(409, "终止请求冲突，请刷新后重试")
        db.commit()
        db.expire_all()
        batch = _require_batch(db, batch_id)
        _sync_progress(db, batch)
        return serialize(batch, db)
    claimed = db.query(AnalysisBatch).filter(
        AnalysisBatch.id == batch_id,
        AnalysisBatch.status.notin_(list(TERMINAL_BATCH_STATUSES)),
    ).update({
        AnalysisBatch.control_reason: ABORT_REASON,
        AnalysisBatch.updated_at: utcnow(),
    }, synchronize_session=False)
    if claimed != 1:
        raise HTTPException(409, "终止请求冲突，请刷新后重试")
    db.expire_all()
    batch = _require_batch(db, batch_id)
    _settle_aborted_batch(db, batch)
    db.expire_all()
    batch = _require_batch(db, batch_id)
    return serialize(batch, db)


def validate_token(token: str, db: DbSession):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        uid = int(payload.get("sub"))
    except (JWTError, TypeError, ValueError):
        raise HTTPException(401, "登录已过期")
    user = db.query(User).filter(User.id == uid, User.is_active.is_(True)).first()
    if not user:
        raise HTTPException(401, "登录已过期")
    return user

@router.get("/batch/{batch_id}/progress")
def progress(batch_id:int,token:str,db:DbSession=Depends(get_db)):
    user = validate_token(token, db)
    if user.role != "admin":
        raise HTTPException(403, "需要管理员权限")
    if not db.query(AnalysisBatch).filter(AnalysisBatch.id == batch_id).first():
        raise HTTPException(404, "批次不存在")
    async def stream():
        while True:
            # Always count from the database. A cached progress_state can lag
            # behind upload-status by a full concurrency window (typically 15
            # failed slices reclaimed as processing), which makes the cards jump.
            local=SessionLocal()
            try:
                b=local.query(AnalysisBatch).filter(AnalysisBatch.id==batch_id).first()
                state = _progress_payload(b, _slice_counts(local, batch_id)) if b else {"status": "failed", "error": "\u6279\u6b21\u4e0d\u5b58\u5728", "processed_count": 0, "pending_count": 0, "done": 0, "total": 0}
            finally:
                local.close()
            yield f"data: {json.dumps(state,ensure_ascii=False)}\n\n"
            if state.get("status") in TERMINAL_BATCH_STATUSES: break
            await asyncio.sleep(1)
    return StreamingResponse(stream(),media_type="text/event-stream")

@router.get("/batches")
def batches(user:User=Depends(get_current_user),db:DbSession=Depends(get_db)):
    # Listing must stay read-only. Reconciling stale workers here contended with
    # live analysis writes and made History/Upload fail to load.
    rows = db.query(AnalysisBatch).order_by(AnalysisBatch.created_at.desc()).limit(100).all()
    batch_ids = [row.id for row in rows]
    counts = _slice_counts_by_batch(db, batch_ids)
    issues = _issue_counts_by_batch(db, batch_ids)
    return [serialize(row, db, counts.get(row.id), issues.get(row.id, 0)) for row in rows]


def _delete_batch_rows(db: DbSession, batch_id: int) -> None:
    """Delete a batch with SQL subqueries. Never bind thousands of IDs.

    SQLite rejects more than ~999 bound parameters, so ``IN (slice_id, ...)``
    on a 7000-slice overseas file fails even when the database is healthy.
    Large TEXT columns are deleted in chunks so one request cannot freeze the UI.
    """
    from sqlalchemy import text
    params = {"bid": batch_id, "chunk": 400}

    def run(sql, extra=None):
        result = db.execute(text(sql), {**params, **(extra or {})})
        db.commit()
        return result.rowcount or 0

    def run_chunks(sql):
        while run(sql):
            pass

    run("PRAGMA busy_timeout=60000")
    run("DELETE FROM review_assignment_logs WHERE assignment_id IN (SELECT id FROM review_assignments WHERE batch_id = :bid)")
    run("DELETE FROM review_assignments WHERE batch_id = :bid")
    run("DELETE FROM review_processing_logs WHERE item_type = 'quality_issue' AND item_id IN (SELECT qi.id FROM qc_slice_quality_issues qi JOIN qc_slices s ON s.id = qi.slice_id WHERE s.batch_id = :bid)")
    run("DELETE FROM review_processing_logs WHERE item_type = 'knowledge_suggestion' AND item_id IN (SELECT ks.id FROM qc_slice_knowledge_suggestions ks JOIN qc_slices s ON s.id = ks.slice_id WHERE s.batch_id = :bid)")
    run("DELETE FROM knowledge_suggestion_status_logs WHERE knowledge_suggestion_id IN (SELECT ks.id FROM qc_slice_knowledge_suggestions ks JOIN qc_slices s ON s.id = ks.slice_id WHERE s.batch_id = :bid)")
    run("DELETE FROM qa_pool_status_logs WHERE pool_entry_id IN (SELECT e.id FROM qa_pool_entries e WHERE (e.source_item_type = 'quality_issue' AND e.source_item_id IN (SELECT qi.id FROM qc_slice_quality_issues qi JOIN qc_slices s ON s.id = qi.slice_id WHERE s.batch_id = :bid)) OR (e.source_item_type = 'knowledge_suggestion' AND e.source_item_id IN (SELECT ks.id FROM qc_slice_knowledge_suggestions ks JOIN qc_slices s ON s.id = ks.slice_id WHERE s.batch_id = :bid)))")
    run("DELETE FROM qa_pool_entries WHERE (source_item_type = 'quality_issue' AND source_item_id IN (SELECT qi.id FROM qc_slice_quality_issues qi JOIN qc_slices s ON s.id = qi.slice_id WHERE s.batch_id = :bid)) OR (source_item_type = 'knowledge_suggestion' AND source_item_id IN (SELECT ks.id FROM qc_slice_knowledge_suggestions ks JOIN qc_slices s ON s.id = ks.slice_id WHERE s.batch_id = :bid))")
    run("DELETE FROM qc_slice_quality_issues WHERE slice_id IN (SELECT id FROM qc_slices WHERE batch_id = :bid)")
    run("DELETE FROM qc_slice_knowledge_suggestions WHERE slice_id IN (SELECT id FROM qc_slices WHERE batch_id = :bid)")
    run("DELETE FROM qc_issues WHERE batch_id = :bid")
    run("DELETE FROM kb_suggestions WHERE batch_id = :bid")
    run_chunks("DELETE FROM ai_messages WHERE id IN (SELECT id FROM ai_messages WHERE batch_id = :bid LIMIT :chunk)")
    run_chunks("DELETE FROM qc_slices WHERE id IN (SELECT id FROM qc_slices WHERE batch_id = :bid LIMIT :chunk)")
    run_chunks("DELETE FROM sessions WHERE id IN (SELECT id FROM sessions WHERE batch_id = :bid LIMIT :chunk)")
    run("DELETE FROM reports WHERE batch_id = :bid")
    run("DELETE FROM uploaded_files WHERE batch_id = :bid")
    run("DELETE FROM analysis_batches WHERE id = :bid")


@router.delete("/batch/{batch_id}")
def delete_batch(batch_id: int, _: User = Depends(require_admin), db: DbSession = Depends(get_db)):
    """Delete one analysis batch and all data owned by that batch.

    The history page already sends DELETE to this URL. The route was missing
    from the recovered workspace router, so FastAPI returned 405.
    """
    batch = db.query(AnalysisBatch).filter(AnalysisBatch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在，可能已被删除")
    if batch.status == "analyzing" or batch_id in ACTIVE_BATCHES:
        raise HTTPException(status_code=409, detail="批次正在分析中，请先暂停或等分析结束后再删")

    try:
        _delete_batch_rows(db, batch_id)
        db.commit()
    except (OperationalError, DatabaseError) as exc:
        db.rollback()
        kind = sqlite_error_kind(exc)
        status = 503 if kind == "locked" else 500
        raise HTTPException(status_code=status, detail=sqlite_user_message(exc, "删除批次")) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"删除批次失败：{exc}")

    # Delete only this router's runtime upload artifacts; never touch the
    # legacy/corrupt uploads directory.
    for entry in os.scandir(UPLOAD_DIR) if os.path.isdir(UPLOAD_DIR) else []:
        if entry.is_file() and (entry.name.startswith(f"{batch_id}_") or entry.name.startswith(f"batch-{batch_id}-")):
            try:
                os.remove(entry.path)
            except OSError:
                pass
    return {"message": "批次及其关联数据已删除", "batch_id": batch_id}
def _parse_file_job(batch_id: int, file_id: int) -> None:
    PARSE_SEMAPHORE.acquire()
    db = SessionLocal()
    try:
        uploaded = db.query(UploadedFile).filter(UploadedFile.id == file_id, UploadedFile.batch_id == batch_id).first()
        batch = db.query(AnalysisBatch).filter(AnalysisBatch.id == batch_id).first()
        if not uploaded or not batch or uploaded.status != "parsing":
            return
        _parse_registered_file(db, batch, uploaded)
    except Exception:
        traceback.print_exc()
    finally:
        db.close()
        PARSE_SEMAPHORE.release()


def _claim_slices(db, batch_id: int, eligible_ids: set[int], limit: int) -> list[int]:
    """Claim pending/failed slices that existed when this analyzing session started.

    Slices parsed after start stay pending until pause + resume. Failed slices from
    this same session are already claimed and will not be auto-retried here.
    """
    if limit <= 0 or not eligible_ids:
        return []
    rows = (
        db.query(QcSlice)
        .filter(
            QcSlice.batch_id == batch_id,
            QcSlice.id.in_(eligible_ids),
            QcSlice.analysis_status.in_(["pending", "failed"]),
        )
        .order_by(QcSlice.id)
        .limit(limit)
        .all()
    )
    claimed = []
    now = utcnow()
    for row in rows:
        row.analysis_status = "processing"
        row.started_at = now
        row.error_code = None
        row.error_message = None
        claimed.append(row.id)
    if claimed:
        db.commit()
        batch = db.get(AnalysisBatch, batch_id)
        if batch:
            _sync_progress(db, batch)
            db.commit()
    return claimed


def _persist_slice_result(db, batch: AnalysisBatch, slice_row: QcSlice, result, error) -> None:
    if error:
        slice_row.analysis_status = "failed"
        slice_row.error_code = getattr(error, "code", "maas_request_failed")
        slice_row.error_message = str(error)[:2000]
        raw = getattr(error, "raw_response", None)
        metadata = getattr(error, "response_metadata", None) or {}
        if metadata.get("request_id"):
            slice_row.maas_request_id = str(metadata["request_id"])
        if raw is not None or metadata:
            slice_row.raw_response_json = json.dumps(
                {"raw_response": (raw or "")[:10000], "response_metadata": metadata},
                ensure_ascii=False,
            )
        slice_row.completed_at = utcnow()
        _sync_progress(db, batch)
        db.commit()
        return

    raw_response = result.pop("_raw_response", None)
    response_metadata = result.pop("_response_metadata", None) or {}
    slice_row.raw_response_json = raw_response or json.dumps(result, ensure_ascii=False)
    if isinstance(result.get("messages"), list) and result["messages"]:
        slice_row.messages_json = json.dumps(result["messages"], ensure_ascii=False, separators=(",", ":"))
    slice_row.analysis_status = result.get("analysis_status", "completed")
    slice_row.warnings_json = json.dumps(result.get("warnings") or [], ensure_ascii=False)
    slice_row.errors_json = json.dumps(result.get("errors") or [], ensure_ascii=False)
    slice_row.analysis_version = result.get("analysis_version")
    slice_row.prompt_version = result.get("prompt_version")
    slice_row.maas_request_id = result.get("request_id") or response_metadata.get("request_id")
    if slice_row.analysis_status == "failed":
        slice_row.error_code = "maas_analysis_failed"
        slice_row.error_message = json.dumps(result.get("errors") or ["MaaS 返回 failed"], ensure_ascii=False)[:2000]
        slice_row.completed_at = utcnow()
        _sync_progress(db, batch)
        db.commit()
        return
    quality = result.get("quality_check") or {}
    issues = quality.get("issues") or []
    is_m_backend = slice_row.source_format == "m_backend"
    slice_row.quality_has_issue = False if is_m_backend else bool(quality.get("has_issue"))
    slice_row.quality_issue_count = 0 if is_m_backend else len(issues)
    issue_rows = [] if is_m_backend else issues[:3]
    for index, issue in enumerate(issue_rows):
        db.add(QcSliceQualityIssue(
            slice_id=slice_row.id,
            issue_id=issue.get("issue_id") or f"{slice_row.slice_id}-issue-{index + 1}",
            issue_type=issue.get("issue_type"), severity=issue.get("severity"),
            confidence=issue.get("confidence"),
            ai_message_ids=json.dumps(issue.get("ai_message_ids") or [], ensure_ascii=False),
            evidence_message_ids=json.dumps(issue.get("evidence_message_ids") or [], ensure_ascii=False),
            player_question_json=json.dumps(issue.get("player_question"), ensure_ascii=False) if issue.get("player_question") is not None else None,
            ai_answer_json=json.dumps(issue.get("ai_answer"), ensure_ascii=False) if issue.get("ai_answer") is not None else None,
            reason=issue.get("reason"), suggestion=issue.get("suggestion"),
            revised_reply=issue.get("revised_reply"), revised_reply_zh_cn=issue.get("revised_reply_zh_cn"),
            needs_manual_review=bool(issue.get("needs_manual_review")),
            manual_review_reason=issue.get("manual_review_reason"), is_primary=index == 0,
        ))
    handoff = result.get("human_handoff")
    slice_row.human_handoff_json = json.dumps(handoff, ensure_ascii=False) if isinstance(handoff, dict) else None
    knowledge = result.get("knowledge_suggestion") or {}
    if knowledge:
        decision = knowledge.get("decision") or "no_human_answer"
        slice_row.knowledge_decision = decision
        slice_row.knowledge_is_candidate = decision in {
            "candidate_ready", "candidate_needs_enrichment", "candidate_pending_feedback",
        }
        terms = result.get("term_suggestions") or {"has_terms": False, "terms": []}
        db.add(QcSliceKnowledgeSuggestion(
            slice_id=slice_row.id, answer_source=knowledge.get("answer_source") or "human_agent",
            decision=decision, confidence=knowledge.get("confidence"),
            category=knowledge.get("category"),
            validation_status=knowledge.get("validation_status"),
            question_message_ids=json.dumps(knowledge.get("question_message_ids") or [], ensure_ascii=False),
            answer_message_ids=json.dumps(knowledge.get("answer_message_ids") or [], ensure_ascii=False),
            feedback_message_ids=json.dumps(knowledge.get("feedback_message_ids") or [], ensure_ascii=False),
            evidence_message_ids=json.dumps(knowledge.get("evidence_message_ids") or [], ensure_ascii=False),
            term_suggestions_json=json.dumps(terms, ensure_ascii=False),
            title=knowledge.get("title"), standard_questions=json.dumps(knowledge.get("standard_questions") or [], ensure_ascii=False),
            standard_answer=knowledge.get("standard_answer"), applicable_scope=json.dumps(knowledge.get("applicable_scope"), ensure_ascii=False) if knowledge.get("applicable_scope") is not None else None,
            keywords=json.dumps(knowledge.get("keywords") or [], ensure_ascii=False),
            reason=knowledge.get("reason"),
            reject_reason=knowledge.get("reject_reason"), needs_manual_review=bool(knowledge.get("needs_manual_review")),
            manual_review_reason=knowledge.get("manual_review_reason"),
        ))
    slice_row.error_code = None
    slice_row.error_message = None
    slice_row.completed_at = utcnow()
    _sync_progress(db, batch)
    db.commit()


def run_sync(batch_id):
    ACTIVE_BATCHES.add(batch_id)
    try:
        asyncio.run(run_analysis(batch_id))
    finally:
        ACTIVE_BATCHES.discard(batch_id)


async def run_analysis(batch_id):
    db = SessionLocal()
    batch = db.query(AnalysisBatch).filter(AnalysisBatch.id == batch_id).first()
    if not batch:
        db.close()
        return
    try:
        # Snapshot eligible slices at analyzing-session start. Newly parsed
        # slices from in-flight parsers stay pending until pause + resume.
        eligible_rows = db.query(QcSlice.id).filter(
            QcSlice.batch_id == batch_id,
            QcSlice.analysis_status.in_(["pending", "failed"]),
        ).all()
        eligible_ids = {row[0] for row in eligible_rows}
        in_flight: set[asyncio.Task] = set()
        # Admin configuration changes are applied in-process. Read the module
        # dynamically instead of the scalar imported during process startup.
        slot_limit = max(int(config_mod.ANALYSIS_CONCURRENCY), 1)

        async def analyze_one(slice_db_id: int):
            # Do not keep a SQLAlchemy connection checked out while waiting on
            # MaaS. With ANALYSIS_CONCURRENCY=15, one Session per in-flight
            # request exhausts SQLite's default pool (5 + 10 overflow); the
            # requests then appear frozen and their results cannot be saved.
            # Read the immutable input, close the session, call MaaS, and use
            # a fresh short-lived session only for persistence.
            local = SessionLocal()
            try:
                slice_row = local.get(QcSlice, slice_db_id)
                if not slice_row:
                    return
                if not should_analyze_from_snapshot(slice_row):
                    return
                messages_json = slice_row.messages_json or "[]"
                slice_id = slice_row.slice_id
                channel = slice_row.channel
                game = slice_row.game
                region = slice_row.region
                local.close()
                local = None
                try:
                    messages = json.loads(messages_json)
                    payload = {
                        "schema_version": "1.0.0", "slice_id": slice_id,
                        "channel": channel, "game": game,
                        "region": region, "messages": messages,
                    }
                    result = await analyze_slice(payload)
                    error = None
                except Exception as exc:
                    result, error = None, exc
                local = SessionLocal()
                slice_row = local.get(QcSlice, slice_db_id)
                batch_row = local.get(AnalysisBatch, batch_id)
                if slice_row and batch_row:
                    _persist_slice_result(local, batch_row, slice_row, result, error)
            except Exception:
                traceback.print_exc()
            finally:
                if local is not None:
                    local.close()

        while True:
            db.expire_all()
            batch = db.get(AnalysisBatch, batch_id)
            if not batch:
                break
            stop_claiming = batch.status != "analyzing" or _is_abort_requested(batch)
            if stop_claiming:
                break
            capacity = slot_limit - len(in_flight)
            claimed = _claim_slices(db, batch_id, eligible_ids, capacity)
            if not claimed:
                if in_flight:
                    done, in_flight = await asyncio.wait(in_flight, timeout=0.4, return_when=asyncio.FIRST_COMPLETED)
                    continue
                db.expire_all()
                batch = db.get(AnalysisBatch, batch_id)
                if batch and batch.status == "analyzing" and not _is_abort_requested(batch):
                    db.query(AnalysisBatch).filter(
                        AnalysisBatch.id == batch_id,
                        AnalysisBatch.status == "analyzing",
                    ).update({
                        AnalysisBatch.status: "paused",
                        AnalysisBatch.updated_at: utcnow(),
                    }, synchronize_session=False)
                    db.commit()
                    db.expire_all()
                    batch = db.get(AnalysisBatch, batch_id)
                    if batch:
                        _sync_progress(db, batch)
                break
            for slice_db_id in claimed:
                eligible_ids.discard(slice_db_id)
                in_flight.add(asyncio.create_task(analyze_one(slice_db_id)))
            if in_flight:
                done, in_flight = await asyncio.wait(in_flight, timeout=0.2, return_when=asyncio.FIRST_COMPLETED)

        if in_flight:
            await asyncio.gather(*in_flight, return_exceptions=True)

        db.expire_all()
        batch = db.get(AnalysisBatch, batch_id)
        if batch and _is_abort_requested(batch):
            _settle_aborted_batch(db, batch)
        elif batch:
            _sync_progress(db, batch)
    except Exception as exc:
        try:
            db.rollback()
            _mark_interrupted_batch(db, batch, f"分析任务异常中断：{str(exc)[:500]}")
        except Exception:
            db.rollback()
            batch.status = "paused"
            batch.error_msg = traceback.format_exc()[-2000:]
            db.commit()
            _sync_progress(db, batch, batch.error_msg)
    except (asyncio.CancelledError, KeyboardInterrupt):
        try:
            _mark_interrupted_batch(db, batch, INTERRUPTED_ANALYSIS_MESSAGE)
        finally:
            raise
    finally:
        db.close()
