"""
系统设置 + 备份 路由（管理员用）
"""
import os
import json
import shutil
import subprocess
import sys
import threading
import re
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from datetime import datetime
import io
from fastapi.responses import Response
from sqlalchemy import case, or_, text, update
from database import (get_db, User, MappingConfig, GameAiConfig, QcSlice, AnalysisBatch,
                      QcSliceQualityIssue, QcSliceKnowledgeSuggestion, Session, utcnow,
                      AuditLog, QcIssue, KbSuggestion)
from services.game_ai_config import serialize_config as serialize_game_ai_config
from sqlalchemy.exc import IntegrityError
from auth import require_admin
from config import BACKUP_DIR, DB_PATH
from services.maas_client import is_maas_configured
from audit import (
    ACTION_LABELS, TARGET_TYPE_LABELS, AuditQueryError, audit_log_query,
    record_audit, resolve_audit_window, serialize_audit_logs, action_label,
    target_type_label,
)

router = APIRouter(prefix="/api/admin", tags=["管理"])


PENDING_MAPPING_PREFIX = "[待补充映射]"
STANDARD_CHANNELS = ("DC", "FB", "LINE", "VK")
DIGIT_GAME = re.compile(r"^\d+$")


def _norm_channel(value):
    text = str(value or "").strip()
    if not text:
        return ""
    head = text.split("-", 1)[0].strip()
    return head if head in STANDARD_CHANNELS else text


def _blank(value):
    text = str(value or "").strip()
    return text or None


def _pending_query(db):
    return db.query(MappingConfig).filter(
        MappingConfig.enabled.is_(False),
        MappingConfig.remark.isnot(None),
        MappingConfig.remark.startswith(PENDING_MAPPING_PREFIX),
    )


def _enabled_game_product_ids(db):
    rows = db.query(MappingConfig.match_value).filter(
        MappingConfig.enabled.is_(True),
        MappingConfig.raw_channel == "官网客服",
        MappingConfig.match_field == "gameProductId",
        MappingConfig.match_value.isnot(None),
        MappingConfig.match_value != "",
    ).all()
    return {str(row[0]).strip() for row in rows if str(row[0] or "").strip()}


def _standard_rule_covers(row, region, channel, game):
    if (row.match_field or "none") not in {"none", "", None}:
        return False
    if _blank(row.raw_channel) and _norm_channel(row.raw_channel) != channel:
        return False
    if _blank(row.game) and str(row.game).strip() != game:
        return False
    rule_region = _blank(row.raw_region)
    if rule_region and rule_region != region:
        return False
    if not _blank(row.raw_channel) or not _blank(row.game):
        return False
    return True


def _has_enabled_standard_rule(rules, region, channel, game):
    for row in rules:
        if not row.enabled:
            continue
        if _standard_rule_covers(row, region, channel, game):
            return True
    return False


def _collect_gaps(db, slices):
    enabled_ids = _enabled_game_product_ids(db)
    standard_rules = db.query(MappingConfig).filter(MappingConfig.enabled.is_(True)).all()
    product_gaps = {}
    standard_gaps = {}
    for slice_row in slices:
        channel = _norm_channel(slice_row.channel)
        game = str(slice_row.game or "").strip()
        region = str(slice_row.region or "").strip()
        if channel == "官网客服" and DIGIT_GAME.match(game) and game not in enabled_ids:
            item = product_gaps.setdefault(game, {
                "kind": "gameProductId",
                "match_value": game,
                "sample_count": 0,
                "sample_slice_id": slice_row.slice_id,
            })
            item["sample_count"] += 1
            continue
        if channel in STANDARD_CHANNELS and game and not DIGIT_GAME.match(game):
            if _has_enabled_standard_rule(standard_rules, region, channel, game):
                continue
            key = (region, channel, game)
            item = standard_gaps.setdefault(key, {
                "kind": "standard",
                "raw_region": region,
                "raw_channel": channel,
                "game": game,
                "sample_count": 0,
            })
            item["sample_count"] += 1
    items = list(product_gaps.values()) + list(standard_gaps.values())
    items.sort(key=lambda row: (-int(row.get("sample_count") or 0), row.get("kind") or "", row.get("match_value") or row.get("game") or ""))
    return items


def _collect_batch_gaps(db, batch_id: int):
    batch = db.query(AnalysisBatch).filter(AnalysisBatch.id == batch_id).first()
    if not batch:
        raise HTTPException(404, "批次不存在")
    slices = db.query(QcSlice).filter(QcSlice.batch_id == batch_id).all()
    return _collect_gaps(db, slices)


def _collect_all_gaps(db):
    slices = db.query(QcSlice).all()
    return _collect_gaps(db, slices)


def _find_existing_draft(db, **filters):
    query = db.query(MappingConfig)
    for key, value in filters.items():
        column = getattr(MappingConfig, key)
        if value is None or value == "":
            query = query.filter((column.is_(None)) | (column == ""))
        else:
            query = query.filter(column == value)
    return query.first()




def _admin_slice_dict(slice_row, session, issues, knowledge):
    def parse(value, fallback):
        try: return json.loads(value) if value else fallback
        except Exception: return fallback
    return {
        "id": slice_row.id, "slice_id": slice_row.slice_id, "batch_id": slice_row.batch_id,
        "channel": slice_row.channel, "game": slice_row.game, "region": slice_row.region,
        "analysis_status": slice_row.analysis_status, "quality_has_issue": slice_row.quality_has_issue,
        "quality_issue_count": slice_row.quality_issue_count or 0,
        "knowledge_decision": slice_row.knowledge_decision,
        "knowledge_is_candidate": slice_row.knowledge_is_candidate,
        "messages": parse(slice_row.messages_json, []),
        "issues": [{"id": x.id, "issue_id": x.issue_id, "issue_type": x.issue_type, "severity": x.severity,
                    "confidence": x.confidence, "ai_message_ids": parse(x.ai_message_ids, []),
                    "evidence_message_ids": parse(x.evidence_message_ids, []),
                    "player_question": parse(x.player_question_json, None), "ai_answer": parse(x.ai_answer_json, None),
                    "reason": x.reason, "suggestion": x.suggestion, "revised_reply": x.revised_reply,
                    "revised_reply_zh_cn": x.revised_reply_zh_cn, "needs_manual_review": x.needs_manual_review,
                    "manual_review_reason": x.manual_review_reason, "is_primary": x.is_primary} for x in issues],
        "knowledge_suggestion": ({"id": knowledge.id, "answer_source": knowledge.answer_source,
                    "decision": knowledge.decision, "confidence": knowledge.confidence, "category": knowledge.category,
                    "question_message_ids": parse(knowledge.question_message_ids, []), "answer_message_ids": parse(knowledge.answer_message_ids, []),
                    "evidence_message_ids": parse(knowledge.evidence_message_ids, []), "title": knowledge.title,
                    "standard_questions": parse(knowledge.standard_questions, []), "standard_answer": knowledge.standard_answer,
                    "applicable_scope": parse(knowledge.applicable_scope, None),
                    "reason": knowledge.reason, "reject_reason": knowledge.reject_reason,
                    "needs_manual_review": knowledge.needs_manual_review, "manual_review_reason": knowledge.manual_review_reason} if knowledge else None),
        "analysis_version": slice_row.analysis_version, "prompt_version": slice_row.prompt_version,
        "maas_request_id": slice_row.maas_request_id, "raw_response": parse(slice_row.raw_response_json, None),
        "warnings": parse(slice_row.warnings_json, []), "errors": parse(slice_row.errors_json, []),
        "error_code": slice_row.error_code, "error_message": slice_row.error_message,
        "started_at": slice_row.started_at.isoformat() if slice_row.started_at else None,
        "completed_at": slice_row.completed_at.isoformat() if slice_row.completed_at else None,
        "session_uid": session.session_uid if session else None, "session_link": session.session_link if session else None,
    }



def _audit_window_or_400(start: datetime | None, end: datetime | None):
    try:
        return resolve_audit_window(start, end)
    except AuditQueryError as exc:
        raise HTTPException(400, str(exc)) from exc


def _display(value):
    text = "" if value is None else str(value).strip()
    return text or "—"


@router.get("/audit-logs/options", summary="操作日志筛选项")
def audit_log_options(admin: User = Depends(require_admin), db=Depends(get_db)):
    start, end = resolve_audit_window(None, None)
    rows = db.query(AuditLog.action, AuditLog.target_type).filter(
        AuditLog.created_at >= start, AuditLog.created_at <= end,
    ).distinct().all()
    actions = {key: ACTION_LABELS[key] for key in ACTION_LABELS}
    target_types = {key: TARGET_TYPE_LABELS[key] for key in TARGET_TYPE_LABELS}
    for action, target_type in rows:
        if action and action not in actions:
            actions[action] = action_label(action)
        if target_type and target_type not in target_types:
            target_types[target_type] = target_type_label(target_type)
    operators = [
        {"id": user.id, "username": user.username, "role": user.role}
        for user in db.query(User).order_by(User.id).all()
    ]
    return {
        "actions": [{"value": key, "label": actions[key]} for key in sorted(actions, key=lambda item: actions[item])],
        "target_types": [{"value": key, "label": target_types[key]} for key in sorted(target_types, key=lambda item: target_types[item])],
        "operators": operators,
    }


@router.get("/audit-logs", summary="查询操作日志")
def list_audit_logs(start: datetime | None = None, end: datetime | None = None,
                    operator_id: int | None = None, action: str | None = None,
                    target_type: str | None = None, page: int = 1, page_size: int = 20,
                    admin: User = Depends(require_admin), db=Depends(get_db)):
    start, end = _audit_window_or_400(start, end)
    safe_page = max(1, page or 1)
    safe_page_size = 50 if page_size == 50 else 20
    query = audit_log_query(db, start, end, operator_id, action, target_type)
    total = query.count()
    rows = query.offset((safe_page - 1) * safe_page_size).limit(safe_page_size).all()
    return {"items": serialize_audit_logs(db, rows), "total": total, "page": safe_page, "page_size": safe_page_size}


@router.get("/audit-logs/export", summary="导出操作日志")
def export_audit_logs(start: datetime | None = None, end: datetime | None = None,
                      operator_id: int | None = None, action: str | None = None,
                      target_type: str | None = None,
                      admin: User = Depends(require_admin), db=Depends(get_db)):
    start, end = _audit_window_or_400(start, end)
    rows = audit_log_query(db, start, end, operator_id, action, target_type).all()
    items = serialize_audit_logs(db, rows)
    import pandas as pd
    table = [{
        "时间": _display(item["created_at"]),
        "操作人": _display(item["operator_name"]),
        "动作": _display(item["action_label"] or item["action"]),
        "对象": _display(item["target_label"]),
        "改前": _display(item["from_value"]),
        "改后": _display(item["to_value"]),
        "原因": _display(item["reason"]),
    } for item in items]
    buf = io.BytesIO()
    pd.DataFrame(table, columns=["时间", "操作人", "动作", "对象", "改前", "改后", "原因"]).to_excel(buf, index=False, engine="openpyxl")
    buf.seek(0)
    record_audit(db, "export_logs", "audit_logs", None, user=admin, reason="导出操作日志")
    db.commit()
    fname = f"操作日志_{utcnow():%Y%m%d_%H%M%S}.xlsx"
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.get("/audit/slices", summary="管理员查看全部切片分析")
def audit_slices(batch_id: int | None = None, analysis_status: str | None = None,
                 knowledge_decision: str | None = None, channel: str | None = None,
                 game: str | None = None, region: str | None = None,
                 admin: User = Depends(require_admin), db=Depends(get_db)):
    q = db.query(QcSlice, Session).outerjoin(Session, QcSlice.session_id == Session.id)
    if batch_id: q = q.filter(QcSlice.batch_id == batch_id)
    if analysis_status: q = q.filter(QcSlice.analysis_status == analysis_status)
    if knowledge_decision: q = q.filter(QcSlice.knowledge_decision == knowledge_decision)
    if channel: q = q.filter(QcSlice.channel == channel)
    if game: q = q.filter(QcSlice.game.contains(game))
    if region: q = q.filter(QcSlice.region.contains(region))
    rows = q.order_by(QcSlice.created_at.desc()).limit(500).all()
    result = []
    for slice_row, session in rows:
        issue_count = db.query(QcSliceQualityIssue).filter(QcSliceQualityIssue.slice_id == slice_row.id).count()
        result.append({"id": slice_row.id, "slice_id": slice_row.slice_id, "batch_id": slice_row.batch_id,
                       "channel": slice_row.channel, "game": slice_row.game, "region": slice_row.region,
                       "message_count": len(json.loads(slice_row.messages_json or "[]")),
                       "analysis_status": slice_row.analysis_status, "quality_has_issue": slice_row.quality_has_issue,
                       "quality_issue_count": issue_count, "knowledge_decision": slice_row.knowledge_decision,
                       "knowledge_is_candidate": slice_row.knowledge_is_candidate,
                       "created_at": slice_row.created_at.isoformat() if slice_row.created_at else ""})
    return result


@router.get("/audit/slices/{slice_id}", summary="管理员查看切片详情")
def audit_slice_detail(slice_id: int, admin: User = Depends(require_admin), db=Depends(get_db)):
    row = db.query(QcSlice).filter(QcSlice.id == slice_id).first()
    if not row: raise HTTPException(404, "切片不存在")
    session = db.query(Session).filter(Session.id == row.session_id).first() if row.session_id else None
    issues = db.query(QcSliceQualityIssue).filter(QcSliceQualityIssue.slice_id == row.id).order_by(QcSliceQualityIssue.confidence.desc()).all()
    knowledge = db.query(QcSliceKnowledgeSuggestion).filter(QcSliceKnowledgeSuggestion.slice_id == row.id).first()
    return _admin_slice_dict(row, session, issues, knowledge)


def _env_path() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")


def _read_env_file() -> dict[str, str]:
    path = _env_path()
    data: dict[str, str] = {}
    if not os.path.exists(path):
        return data
    with open(path, encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def _write_env_file(updates: dict[str, str]) -> dict[str, str]:
    existing = _read_env_file()
    existing.update(updates)
    with open(_env_path(), "w", encoding="utf-8", newline="\n") as handle:
        for key, value in existing.items():
            handle.write(f"{key}={value}\n")
    return existing


def _apply_runtime_config(env_map: dict[str, str]) -> None:
    """Keep the running process in sync with backend/.env after a save."""
    import config as config_mod
    if "MAAS_BASE_URL" in env_map:
        os.environ["MAAS_BASE_URL"] = env_map["MAAS_BASE_URL"]
        config_mod.MAAS_BASE_URL = env_map["MAAS_BASE_URL"]
    if "MAAS_APP_KEY" in env_map:
        os.environ["MAAS_APP_KEY"] = env_map["MAAS_APP_KEY"]
        config_mod.MAAS_APP_KEY = env_map["MAAS_APP_KEY"]
    if "ANALYSIS_CONCURRENCY" in env_map:
        os.environ["ANALYSIS_CONCURRENCY"] = env_map["ANALYSIS_CONCURRENCY"]
        config_mod.ANALYSIS_CONCURRENCY = int(env_map["ANALYSIS_CONCURRENCY"])


def _config_payload(env_map: dict[str, str] | None = None) -> dict:
    import config as config_mod
    env_map = env_map or _read_env_file()
    base_url = env_map.get("MAAS_BASE_URL") or config_mod.MAAS_BASE_URL or ""
    app_key = env_map.get("MAAS_APP_KEY") or config_mod.MAAS_APP_KEY or ""
    concurrency_raw = env_map.get("ANALYSIS_CONCURRENCY") or str(config_mod.ANALYSIS_CONCURRENCY or 5)
    try:
        concurrency = int(concurrency_raw)
    except (TypeError, ValueError):
        concurrency = config_mod.ANALYSIS_CONCURRENCY or 5
    return {
        "maas_configured": bool(base_url.strip() and app_key.strip()),
        "maas_base_url": base_url,
        "maas_key_set": bool(app_key.strip()),
        "concurrency": concurrency,
        "env_path": _env_path(),
        "db_path": os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(__file__)), "qc.db")),
    }


@router.get("/config", summary="查看系统配置")
def get_config(admin: User = Depends(require_admin)):
    return _config_payload()


@router.put("/config", summary="更新系统配置")
def update_config(data: dict, admin: User = Depends(require_admin)):
    """Persist editable MaaS settings to backend/.env and apply them in-process."""
    base_url = str(data.get("maas_base_url") or "").strip()
    app_key = str(data.get("maas_app_key") or "").strip()
    updates: dict[str, str] = {}
    if base_url:
        updates["MAAS_BASE_URL"] = base_url
    if app_key:
        updates["MAAS_APP_KEY"] = app_key
    if "concurrency" in data and data.get("concurrency") is not None and data.get("concurrency") != "":
        try:
            concurrency = int(data.get("concurrency"))
        except (TypeError, ValueError):
            raise HTTPException(400, "并发数必须是整数")
        if concurrency < 1 or concurrency > 100:
            raise HTTPException(400, "并发数范围为 1-100")
        updates["ANALYSIS_CONCURRENCY"] = str(concurrency)
    if not updates:
        raise HTTPException(400, "没有可保存的配置变更")
    env_map = _write_env_file(updates)
    _apply_runtime_config(env_map)
    payload = _config_payload(env_map)
    payload["message"] = "配置已保存并立即生效"
    payload["restart_required"] = False
    return payload

def _restart_env():
    """Drop in-memory MaaS settings so the next process reads backend/.env."""
    env = os.environ.copy()
    for key in ("MAAS_BASE_URL", "MAAS_APP_KEY", "ANALYSIS_CONCURRENCY", "MAAS_TIMEOUT_SECONDS"):
        env.pop(key, None)
    return env


@router.post("/restart", summary="重启前后端")
def restart_services(admin: User = Depends(require_admin)):
    backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    main_file = os.path.join(backend_dir, "main.py")
    if not os.path.exists(main_file):
        raise HTTPException(500, "未找到后端启动文件")
    # Spawn a detached helper that outlives this process, then start main.py
    # from a clean env. CREATE_NO_WINDOW keeps the restart off a console.
    helper = (
        "import os,subprocess,sys,time;"
        "time.sleep(2);"
        "env=os.environ.copy();"
        "[env.pop(k,None) for k in "
        "('MAAS_BASE_URL','MAAS_APP_KEY','ANALYSIS_CONCURRENCY','MAAS_TIMEOUT_SECONDS')];"
        "subprocess.Popen([sys.executable,'main.py'],cwd=sys.argv[1],env=env,"
        "creationflags=0x00000008|0x00000200,close_fds=True)"
    )
    subprocess.Popen(
        [sys.executable, "-c", helper, backend_dir],
        env=_restart_env(),
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
        close_fds=True,
    )
    # Give FastAPI time to flush the JSON response before this process dies,
    # otherwise the admin page never receives a success payload or toast.
    threading.Timer(1.5, lambda: os._exit(0)).start()
    return {"message": "正在重启后端和 Web 服务，请稍候刷新页面"}


@router.post("/backup", summary="手动备份数据库")
def backup_db(admin: User = Depends(require_admin)):
    ts = utcnow().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(BACKUP_DIR, f"qc_backup_{ts}.db")
    try:
        shutil.copy2(DB_PATH, dest)
        return {"message": f"备份成功: {dest}"}
    except Exception as e:
        raise HTTPException(500, f"备份失败: {e}")


@router.get("/backups", summary="备份文件列表")
def list_backups(admin: User = Depends(require_admin)):
    files = sorted(os.listdir(BACKUP_DIR), reverse=True)
    result = []
    for f in files:
        if f.endswith(".db"):
            fp = os.path.join(BACKUP_DIR, f)
            result.append({"filename": f, "size_kb": round(os.path.getsize(fp) / 1024, 1),
                            "modified_at": datetime.fromtimestamp(os.path.getmtime(fp)).isoformat()})
    return result


def _is_pending_mapping(row):
    return (not bool(row.enabled)) and str(row.remark or "").startswith(PENDING_MAPPING_PREFIX)


def _can_enable_mapping(row):
    if (row.match_field or "none") == "gameProductId" and (row.raw_channel or "") == "官网客服":
        game_name = str(row.game or "").strip()
        if not game_name or DIGIT_GAME.match(game_name):
            return False, "官网客服游戏产品 ID 映射启用前必须填写游戏名，且不能仍是数字 ID"
    return True, ""


def _apply_mapping_enabled(row, enabled: bool):
    if bool(row.enabled) is bool(enabled):
        return "skipped", "已是启用状态" if enabled else "已是停用状态"
    if enabled:
        if _is_pending_mapping(row):
            return "skipped", "请先编辑补全后再启用"
        ok, reason = _can_enable_mapping(row)
        if not ok:
            return "failed", reason
        row.enabled = True
        return "enabled", ""
    row.enabled = False
    return "disabled", ""

@router.get("/mappings")
def list_mappings(raw_region: str | None = None, raw_channel: str | None = None,
                  game: str | None = None, match_field: str | None = None,
                  match_value: str | None = None, target_channel: str | None = None,
                  target_region: str | None = None, enabled: bool | None = None,
                  pending: bool | None = None,
                  page: int | None = None, page_size: int | None = None,
                  db=Depends(get_db), admin: User = Depends(require_admin)):
    """Filter mapping rules without changing their ID-based match priority.

    Calls without pagination keep the legacy list response. Paginated calls
    return ``items`` plus paging metadata for the management table.
    """
    query = db.query(MappingConfig)
    exact_filters = (
        (MappingConfig.raw_region, raw_region),
        (MappingConfig.raw_channel, raw_channel),
        (MappingConfig.match_field, match_field),
        (MappingConfig.target_channel, target_channel),
        (MappingConfig.target_region, target_region),
    )
    for column, value in exact_filters:
        if value is not None and value != "":
            query = query.filter(column == value)
    if game:
        query = query.filter(MappingConfig.game.contains(game))
    if match_value:
        query = query.filter(MappingConfig.match_value.contains(match_value))
    if enabled is not None:
        query = query.filter(MappingConfig.enabled == enabled)
    if pending:
        query = query.filter(
            MappingConfig.enabled.is_(False),
            MappingConfig.remark.isnot(None),
            MappingConfig.remark.startswith(PENDING_MAPPING_PREFIX),
        )

    def serialize(row):
        return {"id": row.id, "raw_region": row.raw_region or "", "raw_channel": row.raw_channel or "",
                "game": row.game or "", "match_field": row.match_field or "none",
                "match_value": row.match_value or "", "target_channel": row.target_channel,
                "target_region": row.target_region or "", "enabled": row.enabled,
                "remark": row.remark or ""}

    ordered = query.order_by(MappingConfig.id)
    if page is None and page_size is None:
        return [serialize(row) for row in ordered.all()]
    safe_page = max(1, page or 1)
    safe_page_size = min(100, max(1, page_size or 20))
    total = query.count()
    rows = ordered.offset((safe_page - 1) * safe_page_size).limit(safe_page_size).all()
    return {"items": [serialize(row) for row in rows], "total": total,
            "page": safe_page, "page_size": safe_page_size}


@router.get("/mappings/options")
def mapping_options(db=Depends(get_db), admin: User = Depends(require_admin)):
    def values(column):
        rows = db.query(column).filter(column.isnot(None), column != "").distinct().order_by(column).all()
        return [row[0] for row in rows]

    match_fields = values(MappingConfig.match_field)
    for field in ("none", "page_id", "language", "gameProductId"):
        if field not in match_fields:
            match_fields.append(field)
    return {
        "raw_regions": values(MappingConfig.raw_region),
        "raw_channels": values(MappingConfig.raw_channel),
        "games": values(MappingConfig.game),
        "match_fields": match_fields,
        "target_channels": values(MappingConfig.target_channel),
        "target_regions": values(MappingConfig.target_region),
        "statuses": [{"value": True, "label": "启用"}, {"value": False, "label": "停用"}],
    }


@router.get("/mappings/gaps")
def mapping_gaps(batch_id: int = Query(..., ge=1), db=Depends(get_db), admin: User = Depends(require_admin)):
    items = _collect_batch_gaps(db, batch_id)
    return {"batch_id": batch_id, "items": items, "total": len(items)}


@router.get("/mappings/pending-count")
def mapping_pending_count(db=Depends(get_db), admin: User = Depends(require_admin)):
    return {"count": _pending_query(db).count()}


@router.post("/mappings/drafts")
def create_mapping_drafts(data: dict, db=Depends(get_db), admin: User = Depends(require_admin)):
    if data.get("scope") == "all":
        gaps = _collect_all_gaps(db)
    else:
        try:
            batch_id = int(data.get("batch_id"))
        except (TypeError, ValueError):
            raise HTTPException(400, "batch_id required")
        gaps = _collect_batch_gaps(db, batch_id)
    created = 0
    skipped = 0
    for gap in gaps:
        if gap["kind"] == "gameProductId":
            exists = _find_existing_draft(
                db,
                raw_channel="官网客服",
                match_field="gameProductId",
                match_value=gap["match_value"],
            )
            if exists:
                skipped += 1
                if exists.enabled is False and not str(exists.remark or "").startswith(PENDING_MAPPING_PREFIX):
                    exists.remark = f"{PENDING_MAPPING_PREFIX} 海外普客 gameProductId"
                continue
            db.add(MappingConfig(
                raw_region=None,
                raw_channel="官网客服",
                game=None,
                match_field="gameProductId",
                match_value=gap["match_value"],
                target_channel="官网客服",
                target_region=None,
                enabled=False,
                remark=f"{PENDING_MAPPING_PREFIX} 海外普客 gameProductId",
            ))
            created += 1
            continue
        exists = _find_existing_draft(
            db,
            raw_region=gap["raw_region"] or None,
            raw_channel=gap["raw_channel"],
            game=gap["game"],
            match_field="none",
        )
        if exists:
            skipped += 1
            if exists.enabled is False and not str(exists.remark or "").startswith(PENDING_MAPPING_PREFIX):
                exists.remark = f"{PENDING_MAPPING_PREFIX} 标准渠道"
            continue
        db.add(MappingConfig(
            raw_region=gap["raw_region"] or None,
            raw_channel=gap["raw_channel"],
            game=gap["game"],
            match_field="none",
            match_value=None,
            target_channel=gap["raw_channel"],
            target_region=None,
            enabled=False,
            remark=f"{PENDING_MAPPING_PREFIX} 标准渠道",
        ))
        created += 1
    db.commit()
    return {"created": created, "skipped": skipped, "total": len(gaps), "pending_count": _pending_query(db).count()}


MAPPING_FIELDS = [
    "raw_region", "raw_channel", "game", "match_field", "match_value",
    "target_channel", "target_region", "enabled", "remark",
]
PROTECTED_CHANNELS = ("DC", "FB", "LINE", "VK", "M后台")


def _is_game_product_field(value) -> bool:
    return str(value or "none").strip() == "gameProductId"


def _prepare_mapping_fields(data: dict, existing=None) -> dict:
    prepared = {}
    for key in MAPPING_FIELDS:
        if key in data:
            prepared[key] = data[key]
    match_field = prepared.get("match_field", getattr(existing, "match_field", None))
    if _is_game_product_field(match_field):
        prepared["raw_region"] = None
    return prepared


def _channel_follows_rule(column, raw_channel):
    text = str(raw_channel or "").strip()
    if not text:
        return True
    return or_(column == text, column.startswith(f"{text}-"))


def _channel_rewrite_expr(column, target_channel):
    target = str(target_channel or "").strip()
    if not target:
        return column
    protected = [column == name for name in PROTECTED_CHANNELS]
    protected.extend(column.startswith(f"{name}-") for name in PROTECTED_CHANNELS)
    return case(
        (column == "官网客服", column),
        (or_(*protected), column),
        else_=target,
    )


def _collect_enabled_game_product_rules(db, match_values=None):
    query = db.query(MappingConfig).filter(
        MappingConfig.enabled.is_(True),
        MappingConfig.match_field == "gameProductId",
        MappingConfig.match_value.isnot(None),
        MappingConfig.match_value != "",
        MappingConfig.game.isnot(None),
        MappingConfig.game != "",
    )
    requested = None
    if match_values is not None:
        requested = []
        seen = set()
        for item in match_values:
            value = str(item or "").strip()
            if value and value not in seen:
                seen.add(value)
                requested.append(value)
        if not requested:
            return []
        query = query.filter(MappingConfig.match_value.in_(requested))
    rows = query.order_by(MappingConfig.id).all()
    chosen = {}
    for row in rows:
        product_id = str(row.match_value or "").strip()
        if not product_id or product_id in chosen:
            continue
        chosen[product_id] = row
    if requested is not None:
        return [chosen[value] for value in requested if value in chosen]
    return list(chosen.values())


def _count_and_update_game_region(db, model, product_id, new_game, new_region, channel_clause, channel_expr=None):
    filters = [model.game == product_id]
    if channel_clause is not True:
        filters.append(channel_clause)
    query = db.query(model).filter(*filters)
    scanned = query.count()
    if not scanned:
        return 0, 0, 0, 0
    new_region_text = "" if new_region is None else str(new_region)
    games_changed = scanned if str(product_id) != str(new_game or "") else 0
    region_rows = query.with_entities(model.region).all()
    regions_changed = 0
    for (current,) in region_rows:
        current_text = "" if current is None else str(current)
        if current_text != new_region_text:
            regions_changed += 1
    values = {"game": new_game, "region": new_region}
    if channel_expr is not None:
        values["channel"] = channel_expr
    result = db.execute(update(model).where(*filters).values(**values))
    updated = result.rowcount if result.rowcount is not None and result.rowcount >= 0 else scanned
    return scanned, updated, games_changed, regions_changed


def apply_enabled_game_product_mappings(db, match_values=None):
    rules = _collect_enabled_game_product_rules(db, match_values)
    summary = {
        "match_values": [str(row.match_value).strip() for row in rules],
        "slices_scanned": 0,
        "slices_updated": 0,
        "sessions_updated": 0,
        "issues_updated": 0,
        "kb_updated": 0,
        "games_changed": 0,
        "regions_changed": 0,
    }
    if not rules:
        return summary
    db.execute(text("PRAGMA busy_timeout=60000"))
    for row in rules:
        product_id = str(row.match_value).strip()
        new_game = str(row.game or "").strip()
        new_region = row.target_region
        slice_channel = _channel_follows_rule(QcSlice.channel, row.raw_channel)
        session_channel = _channel_follows_rule(Session.channel, row.raw_channel)
        issue_channel = _channel_follows_rule(QcIssue.channel, row.raw_channel)
        kb_channel = _channel_follows_rule(KbSuggestion.channel, row.raw_channel)
        slice_channel_expr = _channel_rewrite_expr(QcSlice.channel, row.target_channel)
        session_channel_expr = _channel_rewrite_expr(Session.channel, row.target_channel)
        issue_channel_expr = _channel_rewrite_expr(QcIssue.channel, row.target_channel)
        kb_channel_expr = _channel_rewrite_expr(KbSuggestion.channel, row.target_channel)
        scanned, updated, games_changed, regions_changed = _count_and_update_game_region(
            db, QcSlice, product_id, new_game, new_region, slice_channel, slice_channel_expr,
        )
        summary["slices_scanned"] += scanned
        summary["slices_updated"] += updated
        summary["games_changed"] += games_changed
        summary["regions_changed"] += regions_changed
        _, sessions_updated, _, _ = _count_and_update_game_region(
            db, Session, product_id, new_game, new_region, session_channel, session_channel_expr,
        )
        summary["sessions_updated"] += sessions_updated
        _, issues_updated, _, _ = _count_and_update_game_region(
            db, QcIssue, product_id, new_game, new_region, issue_channel, issue_channel_expr,
        )
        summary["issues_updated"] += issues_updated
        _, kb_updated, _, _ = _count_and_update_game_region(
            db, KbSuggestion, product_id, new_game, new_region, kb_channel, kb_channel_expr,
        )
        summary["kb_updated"] += kb_updated
    return summary


@router.post("/mappings")
def create_mapping(data: dict, db=Depends(get_db), admin: User = Depends(require_admin)):
    payload = _prepare_mapping_fields(data)
    x = MappingConfig(**payload)
    if not x.target_channel: raise HTTPException(400, "target_channel required")
    db.add(x); db.commit(); db.refresh(x); return {"id": x.id}

@router.patch("/mappings/{mapping_id}")
def update_mapping(mapping_id: int, data: dict, db=Depends(get_db), admin: User = Depends(require_admin)):
    x = db.query(MappingConfig).filter(MappingConfig.id == mapping_id).first()
    if not x: raise HTTPException(404, "mapping not found")
    payload = _prepare_mapping_fields(data, x)
    for k, value in payload.items():
        setattr(x, k, value)
    if bool(x.enabled):
        still_pending = str(x.remark or "").startswith(PENDING_MAPPING_PREFIX)
        if still_pending:
            raise HTTPException(400, "请先编辑补全后再启用")
        ok, reason = _can_enable_mapping(x)
        if not ok:
            raise HTTPException(400, reason)
    db.commit(); return {"message": "updated"}


@router.post("/mappings/apply-game-product-ids")
def apply_game_product_ids(data: dict | None = Body(default=None), db=Depends(get_db), admin: User = Depends(require_admin)):
    payload = data or {}
    match_values = payload.get("match_values")
    if match_values is not None and not isinstance(match_values, list):
        raise HTTPException(400, "match_values 必须是字符串数组")
    try:
        summary = apply_enabled_game_product_mappings(db, match_values)
        record_audit(
            db,
            "apply_game_product_mappings",
            "mapping_config",
            None,
            user=admin,
            reason=(
                f"产品ID {len(summary['match_values'])} 个，"
                f"切片扫描 {summary['slices_scanned']}，切片更新 {summary['slices_updated']}，"
                f"会话 {summary['sessions_updated']}，问题 {summary['issues_updated']}，"
                f"知识库 {summary['kb_updated']}，"
                f"游戏名 {summary['games_changed']}，地区 {summary['regions_changed']}"
            ),
        )
        db.commit()
        return summary
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(500, f"回写失败: {exc}") from exc


@router.post("/mappings/batch-enabled")
def batch_set_mapping_enabled(data: dict, db=Depends(get_db), admin: User = Depends(require_admin)):
    ids = data.get("ids")
    enabled = data.get("enabled")
    if not isinstance(ids, list) or not ids:
        raise HTTPException(400, "ids 必须是非空数组")
    if not isinstance(enabled, bool):
        raise HTTPException(400, "enabled 必须是布尔值")
    normalized = []
    seen = set()
    for item in ids:
        try:
            mapping_id = int(item)
        except (TypeError, ValueError):
            raise HTTPException(400, "ids 必须是整数数组")
        if mapping_id not in seen:
            seen.add(mapping_id)
            normalized.append(mapping_id)
    results = []
    success_count = skipped_count = failed_count = 0
    for mapping_id in normalized:
        row = db.query(MappingConfig).filter(MappingConfig.id == mapping_id).first()
        if not row:
            failed_count += 1
            results.append({"id": mapping_id, "success": False, "skipped": False, "reason": "映射不存在"})
            continue
        status, reason = _apply_mapping_enabled(row, enabled)
        if status == "skipped":
            skipped_count += 1
            results.append({"id": mapping_id, "success": False, "skipped": True, "reason": reason})
        elif status in {"enabled", "disabled"}:
            success_count += 1
            results.append({"id": mapping_id, "success": True, "skipped": False, "status": status})
        else:
            failed_count += 1
            results.append({"id": mapping_id, "success": False, "skipped": False, "reason": reason})
    db.commit()
    return {
        "success_count": success_count,
        "skipped_count": skipped_count,
        "failed_count": failed_count,
        "results": results,
    }

@router.delete("/mappings/{mapping_id}")
def delete_mapping(mapping_id: int, db=Depends(get_db), admin: User = Depends(require_admin)):
    x = db.query(MappingConfig).filter(MappingConfig.id == mapping_id).first()
    if not x: raise HTTPException(404, "mapping not found")
    db.delete(x); db.commit(); return {"message": "deleted"}



def _norm_ai_text(value) -> str:
    return str(value or "").strip()


def _bool_or_400(value, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if value in (0, 1, "0", "1", "true", "false", "True", "False"):
        return value in (True, 1, "1", "true", "True")
    raise HTTPException(400, f"{field} 必须是布尔值")


def _prepare_game_ai_fields(data: dict, existing=None) -> dict:
    payload = {}
    game = data.get("game", getattr(existing, "game", None))
    region = data.get("region", getattr(existing, "region", None))
    game = _norm_ai_text(game)
    region = _norm_ai_text(region)
    if not game:
        raise HTTPException(400, "标准游戏不能为空")
    if not region:
        raise HTTPException(400, "标准地区不能为空")
    payload["game"] = game
    payload["region"] = region
    if existing is None or "analysis_enabled" in data:
        payload["analysis_enabled"] = _bool_or_400(data.get("analysis_enabled", False), "analysis_enabled") if existing is None else _bool_or_400(data["analysis_enabled"], "analysis_enabled")
    if existing is None or "enabled" in data:
        default_enabled = True if existing is None else existing.enabled
        payload["enabled"] = _bool_or_400(data.get("enabled", default_enabled), "enabled")
    return payload


@router.get("/game-ai-configs")
def list_game_ai_configs(game: str | None = None, region: str | None = None,
                         analysis_enabled: bool | None = None, enabled: bool | None = None,
                         page: int | None = None, page_size: int | None = None,
                         db=Depends(get_db), admin: User = Depends(require_admin)):
    """List AI analysis switches. Saving never rewrites historical slices."""
    query = db.query(GameAiConfig)
    if game:
        query = query.filter(GameAiConfig.game.contains(game.strip()))
    if region:
        query = query.filter(GameAiConfig.region == region.strip())
    if analysis_enabled is not None:
        query = query.filter(GameAiConfig.analysis_enabled == analysis_enabled)
    if enabled is not None:
        query = query.filter(GameAiConfig.enabled == enabled)
    ordered = query.order_by(GameAiConfig.region, GameAiConfig.game, GameAiConfig.id)

    def serialize(row):
        return serialize_game_ai_config(row)

    if page is None and page_size is None:
        return [serialize(row) for row in ordered.all()]
    safe_page = max(1, page or 1)
    safe_page_size = min(100, max(1, page_size or 20))
    total = query.count()
    rows = ordered.offset((safe_page - 1) * safe_page_size).limit(safe_page_size).all()
    return {"items": [serialize(row) for row in rows], "total": total,
            "page": safe_page, "page_size": safe_page_size}


@router.get("/game-ai-configs/options")
def game_ai_config_options(db=Depends(get_db), admin: User = Depends(require_admin)):
    def values(column):
        rows = db.query(column).filter(column.isnot(None), column != "").distinct().order_by(column).all()
        return [row[0] for row in rows]
    games = sorted(set(values(GameAiConfig.game) + values(MappingConfig.game)))
    regions = sorted(set(values(GameAiConfig.region) + values(MappingConfig.target_region)))
    return {
        "games": games,
        "regions": regions,
        "analysis_statuses": [{"value": True, "label": "开启"}, {"value": False, "label": "关闭"}],
        "statuses": [{"value": True, "label": "启用"}, {"value": False, "label": "停用"}],
    }


@router.post("/game-ai-configs")
def create_game_ai_config(data: dict, db=Depends(get_db), admin: User = Depends(require_admin)):
    payload = _prepare_game_ai_fields(data)
    row = GameAiConfig(**payload)
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "同一标准游戏 + 标准地区只能有一条 AI 配置")
    record_audit(db, "created", "game_ai_config", row.id, user=admin,
                 to_value=f"{row.game}/{row.region} analysis={row.analysis_enabled} enabled={row.enabled}",
                 reason="新增游戏 AI 分析配置，不影响历史切片")
    db.commit(); db.refresh(row)
    return serialize_game_ai_config(row)


@router.put("/game-ai-configs/{config_id}")
def update_game_ai_config(config_id: int, data: dict, db=Depends(get_db), admin: User = Depends(require_admin)):
    row = db.query(GameAiConfig).filter(GameAiConfig.id == config_id).first()
    if not row:
        raise HTTPException(404, "AI 配置不存在")
    before = f"{row.game}/{row.region} analysis={row.analysis_enabled} enabled={row.enabled}"
    payload = _prepare_game_ai_fields(data, row)
    for key, value in payload.items():
        setattr(row, key, value)
    row.updated_at = utcnow()
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "同一标准游戏 + 标准地区只能有一条 AI 配置")
    after = f"{row.game}/{row.region} analysis={row.analysis_enabled} enabled={row.enabled}"
    record_audit(db, "updated", "game_ai_config", row.id, user=admin,
                 from_value=before, to_value=after, reason="修改游戏 AI 分析配置，不重跑历史分析")
    db.commit(); db.refresh(row)
    return serialize_game_ai_config(row)


@router.delete("/game-ai-configs/{config_id}")
def delete_game_ai_config(config_id: int, db=Depends(get_db), admin: User = Depends(require_admin)):
    row = db.query(GameAiConfig).filter(GameAiConfig.id == config_id).first()
    if not row:
        raise HTTPException(404, "AI 配置不存在")
    before = f"{row.game}/{row.region} analysis={row.analysis_enabled} enabled={row.enabled}"
    db.delete(row)
    record_audit(db, "deleted", "game_ai_config", config_id, user=admin,
                 from_value=before, reason="删除游戏 AI 分析配置，不修改历史切片")
    db.commit()
    return {"message": "deleted"}
