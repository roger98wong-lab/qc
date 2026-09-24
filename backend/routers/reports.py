"""
报告查询 + 筛选 + 导出 路由
"""
import os
import json
from types import SimpleNamespace
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, FileResponse, Response
from sqlalchemy import func
from sqlalchemy.orm import Session as DbSession
from typing import Optional

from database import (get_db, User, QcIssue, KbSuggestion, AnalysisBatch, Report,
                      QcSlice, QcSliceQualityIssue, QcSliceKnowledgeSuggestion, Session, ReviewAssignment, utcnow)
from auth import get_current_user, require_admin
from audit import record_audit
from services.report_generator import build_html_report
from routers.workbench import _slice_language

router = APIRouter(prefix="/api/reports", tags=["报告"])

SEVERITY_ORDER = {"严重": 0, "中级": 1, "一般": 2, "需人工复核": 3}

def _latest_assignments(db):
    result = {}
    for row in db.query(ReviewAssignment).order_by(ReviewAssignment.id.desc()).all():
        result.setdefault((row.item_type, row.item_id), row)
    return result

def _can_view_new(db, item_type, item_id, user, assignments=None):
    # Read access is intentionally broader than edit ownership.  The review
    # processing endpoint is the object-level write boundary.
    return user.role in {"admin", "analyst"}

def _legacy_owned(row, user):
    return user.role == "admin" or str(getattr(row, "assignee", "") or "") == user.username

def _admin_only(user):
    if user.role != "admin": raise HTTPException(403, "需要管理员权限")

def _json_list(value, fallback=None):
    try:
        parsed = json.loads(value) if value else fallback
        return parsed if isinstance(parsed, list) else (fallback if fallback is not None else [])
    except (TypeError, json.JSONDecodeError):
        return fallback if fallback is not None else []

@router.get("/issues", summary="获取质检问题列表（支持筛选）")
def list_issues(
    batch_id: Optional[int] = Query(None),
    severity: Optional[str] = Query(None),
    issue_type: Optional[str] = Query(None),
    channel: Optional[str] = Query(None),
    game: Optional[str] = Query(None),
    region: Optional[str] = Query(None),
    priority: Optional[str] = Query(None), status: Optional[str] = Query(None), assignee: Optional[str] = Query(None), search: Optional[str] = Query(None),
    start_time: Optional[str] = Query(None), end_time: Optional[str] = Query(None),
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500),
    db: DbSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = db.query(QcIssue)
    if batch_id:
        q = q.filter(QcIssue.batch_id == batch_id)
    if severity:
        q = q.filter(QcIssue.severity == severity)
    if issue_type:
        q = q.filter(QcIssue.issue_type.contains(issue_type))
    if channel:
        q = q.filter(QcIssue.channel == channel)
    if game:
        q = q.filter(QcIssue.game.contains(game))
    if region: q = q.filter(QcIssue.region.contains(region))
    if priority: q = q.filter(QcIssue.priority == priority)
    if status: q = q.filter(QcIssue.status == status)
    if assignee: q = q.filter(QcIssue.assignee == assignee)
    if start_time: q = q.filter(QcIssue.created_at >= start_time)
    if end_time: q = q.filter(QcIssue.created_at <= end_time)
    if search:
        from sqlalchemy import or_
        term = f"%{search}%"
        q = q.filter(or_(QcIssue.session_uid.ilike(term), QcIssue.game.ilike(term), QcIssue.issue_type.ilike(term), QcIssue.ai_sentence_orig.ilike(term), QcIssue.reason.ilike(term)))
    issues = q.order_by(QcIssue.created_at.desc()).all()
    # New slice issues are merged into the same response contract.  The
    # highest-confidence issue is marked primary by the analysis writer; the
    # list intentionally exposes one row per primary issue while details can
    # load every issue for that slice.
    nq = db.query(QcSliceQualityIssue, QcSlice, Session).join(
        QcSlice, QcSliceQualityIssue.slice_id == QcSlice.id
    ).outerjoin(Session, QcSlice.session_id == Session.id)
    if batch_id: nq = nq.filter(QcSlice.batch_id == batch_id)
    if severity: nq = nq.filter(QcSliceQualityIssue.severity == severity)
    if issue_type: nq = nq.filter(QcSliceQualityIssue.issue_type.contains(issue_type))
    if channel: nq = nq.filter(QcSlice.channel == channel)
    if game: nq = nq.filter(QcSlice.game.contains(game))
    if region: nq = nq.filter(QcSlice.region.contains(region))
    new_issues = nq.order_by(QcSliceQualityIssue.created_at.desc()).all()
    issues_by_slice = {}
    for candidate_issue, candidate_slice, _candidate_session in new_issues:
        issues_by_slice.setdefault(candidate_slice.id, []).append(candidate_issue)
    assignments = _latest_assignments(db)
    result = []
    for i in issues:
        result.append({
            "id": i.id, "batch_id": i.batch_id,
            "priority": i.priority or "P2", "status": i.status or "pending", "assignee": i.assignee,
            "issue_type": i.issue_type, "severity": i.severity,
            "session_uid": i.session_uid, "session_link": i.session_link,
            "reply_time": i.reply_time, "language": i.language,
            "game": i.game, "region": i.region, "channel": i.channel,
            "ai_sentence_orig": i.ai_sentence_orig, "ai_sentence_cn": i.ai_sentence_cn,
            "context": i.context, "reason": i.reason,
            "suggestion": i.suggestion, "revised_reply": i.revised_reply,
            "revised_reply_cn": i.revised_reply_cn,
            "confidence": None,
            "slice_id": None,
        })
    for issue, slice_row, session in new_issues:
        if not issue.is_primary:
            continue
        if not _can_view_new(db, "quality_issue", issue.id, current_user, assignments):
            continue
        question = json.loads(issue.player_question_json or "{}") if issue.player_question_json else {}
        answer = json.loads(issue.ai_answer_json or "{}") if issue.ai_answer_json else {}
        messages = _json_list(slice_row.messages_json, [])
        all_issues = []
        for related in sorted(issues_by_slice.get(slice_row.id, []), key=lambda item: item.confidence or 0, reverse=True):
            all_issues.append({
                "id": related.id, "issue_id": related.issue_id, "issue_type": related.issue_type,
                "severity": related.severity, "confidence": related.confidence,
                "ai_message_ids": _json_list(related.ai_message_ids, []),
                "evidence_message_ids": _json_list(related.evidence_message_ids, []),
                "player_question": json.loads(related.player_question_json) if related.player_question_json else None,
                "ai_answer": json.loads(related.ai_answer_json) if related.ai_answer_json else None,
                "reason": related.reason or "", "suggestion": related.suggestion or "",
                "revised_reply": related.revised_reply, "revised_reply_zh_cn": related.revised_reply_zh_cn,
                "needs_manual_review": bool(related.needs_manual_review),
                "manual_review_reason": related.manual_review_reason,
            })
        result.append({
            "id": issue.id, "batch_id": slice_row.batch_id,
            "priority": "P0" if issue.severity == "严重" else "P1" if issue.severity == "中级" else "P2",
            "status": "pending", "assignee": None, "issue_type": issue.issue_type,
            "severity": issue.severity, "session_uid": session.session_uid if session else slice_row.slice_id,
            "session_link": session.session_link if session else None,
            "reply_time": session.reply_time if session else None, "language": _slice_language(messages),
            "game": slice_row.game, "region": slice_row.region, "channel": slice_row.channel,
            "ai_sentence_orig": answer.get("original", ""), "ai_sentence_cn": answer.get("zh_cn", ""),
            "context": slice_row.messages_json, "messages": messages, "all_issues": all_issues,
            "reason": issue.reason or "",
            "suggestion": issue.suggestion or "", "revised_reply": issue.revised_reply or "",
            "revised_reply_cn": issue.revised_reply_zh_cn or "", "confidence": issue.confidence,
            "slice_id": slice_row.slice_id,
        })
    result.sort(key=lambda x: x.get("confidence") if x.get("confidence") is not None else -1, reverse=True)
    total = len(result)
    paged = result[(page - 1) * page_size: page * page_size]
    if page != 1 or page_size != 50: return {"items": paged, "total": total, "page": page, "page_size": page_size, "pages": (total + page_size - 1) // page_size}
    return paged


def _legacy_issue_base(db, batch_id: Optional[int]):
    query = db.query(QcIssue)
    if batch_id:
        query = query.filter(QcIssue.batch_id == batch_id)
    return query


def _primary_slice_issue_base(db, batch_id: Optional[int]):
    query = db.query(QcSliceQualityIssue).join(QcSlice, QcSliceQualityIssue.slice_id == QcSlice.id).filter(
        QcSliceQualityIssue.is_primary.is_(True),
    )
    if batch_id:
        query = query.filter(QcSlice.batch_id == batch_id)
    return query


def _add_counts(counter, rows):
    for value, count in rows:
        if value not in (None, ""):
            counter[value] += int(count or 0)


def _distinct_values(query, column):
    return {value for value, in query.with_entities(column).filter(column.isnot(None), column != "").distinct().all() if value}


@router.get("/stats", summary="统计概览")
def get_stats(
    batch_id: Optional[int] = Query(None),
    db: DbSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from collections import Counter
    if current_user.role not in {"admin", "analyst"}:
        raise HTTPException(403, "需要质检员或管理员权限")

    legacy = _legacy_issue_base(db, batch_id)
    slice_q = _primary_slice_issue_base(db, batch_id)
    severity_counts = Counter()
    type_counts = Counter()
    _add_counts(severity_counts, legacy.with_entities(QcIssue.severity, func.count(QcIssue.id)).group_by(QcIssue.severity).all())
    _add_counts(severity_counts, slice_q.with_entities(QcSliceQualityIssue.severity, func.count(QcSliceQualityIssue.id)).group_by(QcSliceQualityIssue.severity).all())
    _add_counts(type_counts, legacy.with_entities(QcIssue.issue_type, func.count(QcIssue.id)).group_by(QcIssue.issue_type).all())
    _add_counts(type_counts, slice_q.with_entities(QcSliceQualityIssue.issue_type, func.count(QcSliceQualityIssue.id)).group_by(QcSliceQualityIssue.issue_type).all())

    legacy_total = int(legacy.with_entities(func.count(QcIssue.id)).scalar() or 0)
    slice_total = int(slice_q.with_entities(func.count(QcSliceQualityIssue.id)).scalar() or 0)
    sessions = _distinct_values(legacy, QcIssue.session_uid) | _distinct_values(
        db.query(QcSlice).join(QcSliceQualityIssue, QcSliceQualityIssue.slice_id == QcSlice.id).filter(
            QcSliceQualityIssue.is_primary.is_(True),
            *((QcSlice.batch_id == batch_id,) if batch_id else ()),
        ),
        QcSlice.slice_id,
    )
    channels = _distinct_values(legacy, QcIssue.channel) | _distinct_values(
        db.query(QcSlice).join(QcSliceQualityIssue, QcSliceQualityIssue.slice_id == QcSlice.id).filter(
            QcSliceQualityIssue.is_primary.is_(True),
            *((QcSlice.batch_id == batch_id,) if batch_id else ()),
        ),
        QcSlice.channel,
    )
    games = _distinct_values(legacy, QcIssue.game) | _distinct_values(
        db.query(QcSlice).join(QcSliceQualityIssue, QcSliceQualityIssue.slice_id == QcSlice.id).filter(
            QcSliceQualityIssue.is_primary.is_(True),
            *((QcSlice.batch_id == batch_id,) if batch_id else ()),
        ),
        QcSlice.game,
    )

    total_ai_msgs = 0
    if batch_id:
        b = db.query(AnalysisBatch.total_slices, AnalysisBatch.total_ai_msgs).filter(AnalysisBatch.id == batch_id).first()
        if b:
            total_ai_msgs = b.total_slices or b.total_ai_msgs or 0

    return {
        "total_issues": legacy_total + slice_total,
        "total_ai_msgs": total_ai_msgs,
        "affected_sessions": len(sessions),
        "severe": severity_counts.get("严重", 0),
        "medium": severity_counts.get("中级", 0),
        "general": severity_counts.get("一般", 0),
        "review": severity_counts.get("需人工复核", 0),
        "top_issues": [k for k, _ in type_counts.most_common(10)],
        "type_distribution": dict(type_counts.most_common(20)),
        "channels": list(channels),
        "games": list(games),
    }


@router.get("/filter-options", summary="获取筛选选项")
def filter_options(
    batch_id: Optional[int] = Query(None),
    db: DbSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role not in {"admin", "analyst"}:
        raise HTTPException(403, "需要质检员或管理员权限")
    legacy = _legacy_issue_base(db, batch_id)
    slice_rows = db.query(QcSlice).join(QcSliceQualityIssue, QcSliceQualityIssue.slice_id == QcSlice.id)
    if batch_id:
        slice_rows = slice_rows.filter(QcSlice.batch_id == batch_id)
    slice_issues = db.query(QcSliceQualityIssue).join(QcSlice, QcSliceQualityIssue.slice_id == QcSlice.id)
    if batch_id:
        slice_issues = slice_issues.filter(QcSlice.batch_id == batch_id)
    severities = _distinct_values(legacy, QcIssue.severity) | _distinct_values(slice_issues, QcSliceQualityIssue.severity)
    issue_types = _distinct_values(legacy, QcIssue.issue_type) | _distinct_values(slice_issues, QcSliceQualityIssue.issue_type)
    channels = _distinct_values(legacy, QcIssue.channel) | _distinct_values(slice_rows, QcSlice.channel)
    games = _distinct_values(legacy, QcIssue.game) | _distinct_values(slice_rows, QcSlice.game)
    regions = _distinct_values(legacy, QcIssue.region) | _distinct_values(slice_rows, QcSlice.region)
    return {
        "severities": sorted(severities, key=lambda value: SEVERITY_ORDER.get(value, 9)),
        "issue_types": sorted(issue_types),
        "channels": sorted(channels),
        "games": sorted(games),
        "regions": sorted(regions),
    }


@router.post("/generate", summary="生成HTML报告")
async def generate_report(
    data: dict,
    db: DbSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _admin_only(current_user)
    batch_id   = data.get("batch_id")
    report_name = data.get("name") or f"质检报告_{utcnow():%Y%m%d_%H%M%S}"
    filters    = {k: v for k, v in data.items() if k not in ("batch_id", "name") and v}

    if not batch_id:
        raise HTTPException(400, "请先选择分析批次后再生成报告")

    q = db.query(QcIssue)
    if batch_id:
        q = q.filter(QcIssue.batch_id == batch_id)
    for field in ("severity", "channel", "game", "region"):
        if filters.get(field):
            q = q.filter(getattr(QcIssue, field) == filters[field])

    issues = q.all()

    # New batches persist up to three issues per prepared Excel slice.  Build
    # report rows with the legacy report-generator contract so the HTML report
    # remains entirely local and works for both generations of data.
    slice_query = db.query(QcSliceQualityIssue, QcSlice).join(
        QcSlice, QcSliceQualityIssue.slice_id == QcSlice.id
    )
    if batch_id:
        slice_query = slice_query.filter(QcSlice.batch_id == batch_id)
    if filters.get("severity"):
        slice_query = slice_query.filter(QcSliceQualityIssue.severity == filters["severity"])
    if filters.get("channel"):
        slice_query = slice_query.filter(QcSlice.channel == filters["channel"])
    if filters.get("game"):
        slice_query = slice_query.filter(QcSlice.game == filters["game"])
    if filters.get("region"):
        slice_query = slice_query.filter(QcSlice.region == filters["region"])
    for slice_issue, slice_row in slice_query.all():
        answer = json.loads(slice_issue.ai_answer_json or "{}")
        issues.append(SimpleNamespace(
            issue_type=slice_issue.issue_type,
            severity=slice_issue.severity,
            session_uid=slice_row.slice_id,
            channel=slice_row.channel,
            game=slice_row.game,
            region=slice_row.region,
            ai_sentence_orig=answer.get("original", ""),
            reason=slice_issue.reason or "",
            suggestion=slice_issue.suggestion or "",
            revised_reply=slice_issue.revised_reply or "",
        ))

    # 获取批次信息
    batch = db.query(AnalysisBatch).filter(AnalysisBatch.id == batch_id).first()
    if not batch:
        raise HTTPException(404, "分析批次不存在")

    # HTML 报告必须离线生成：不要为报告摘要额外调用 MaaS，避免消耗
    # token、引入外部依赖，并保持“生成报告”可离线使用。
    from collections import Counter
    severity_c = Counter(i.severity for i in issues)
    type_c     = Counter(i.issue_type for i in issues)
    stats = {
        "total_ai_msgs":    (batch.total_slices or batch.total_ai_msgs) if batch else 0,
        "total_issues":     len(issues),
        "affected_sessions": len(set(i.session_uid for i in issues)),
        "severe": severity_c.get("严重", 0), "medium": severity_c.get("中级", 0),
        "general": severity_c.get("一般", 0), "review": severity_c.get("需人工复核", 0),
        "top_issues": [k for k, _ in type_c.most_common(5)],
        "channels": list(set(i.channel for i in issues if i.channel)),
        "games":    list(set(i.game    for i in issues if i.game)),
    }
    if not stats["total_issues"]:
        summary_text = "本次分析未发现进入问题列表的明确质检问题。"
    else:
        top = "、".join(stats["top_issues"][:3]) or "待分类问题"
        summary_text = (
            f"本次共发现 {stats['total_issues']} 条质检问题，涉及 {stats['affected_sessions']} 个切片；"
            f"其中严重 {stats['severe']} 条、中级 {stats['medium']} 条、一般 {stats['general']} 条、"
            f"需人工复核 {stats['review']} 条。主要问题类型：{top}。"
        )

    html = build_html_report(
        batch_name=batch.name if batch else report_name,
        issues=issues,
        stats=stats,
        summary_text=summary_text,
    )

    from config import REPORT_DIR
    html_path = os.path.join(REPORT_DIR, f"report_{batch_id}_{utcnow():%Y%m%d_%H%M%S}.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    report = Report(batch_id=batch_id, name=report_name,
                    html_path=html_path, created_by=current_user.id,
                    filter_json=json.dumps(filters, ensure_ascii=False))
    db.add(report); db.commit(); db.refresh(report)

    return {"report_id": report.id, "html_path": html_path}


@router.get("/{report_id}/html", summary="查看报告HTML", response_class=HTMLResponse)
def view_report_html(
    report_id: int,
    db: DbSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _admin_only(current_user)
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report or not report.html_path or not os.path.exists(report.html_path):
        raise HTTPException(404, "报告文件不存在")
    with open(report.html_path, encoding="utf-8") as f:
        return f.read()


@router.get("/{report_id}/download", summary="下载报告HTML")
def download_report(
    report_id: int,
    db: DbSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _admin_only(current_user)
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report or not report.html_path or not os.path.exists(report.html_path):
        raise HTTPException(404, "报告文件不存在")
    return FileResponse(report.html_path, filename=os.path.basename(report.html_path),
                        media_type="text/html")


@router.get("/list", summary="报告历史列表")
def list_reports(
    batch_id: Optional[int] = Query(None),
    db: DbSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _admin_only(current_user)
    q = db.query(Report)
    if batch_id:
        q = q.filter(Report.batch_id == batch_id)
    reports = q.order_by(Report.created_at.desc()).limit(50).all()
    return [{"id": r.id, "name": r.name, "batch_id": r.batch_id,
             "created_at": r.created_at.isoformat() if r.created_at else "",
             "filter_json": r.filter_json} for r in reports]


@router.get("/export-excel", summary="导出质检问题为Excel")
def export_excel(
    batch_id: Optional[int] = Query(None),
    severity: Optional[str] = Query(None),
    channel: Optional[str] = Query(None),
    game: Optional[str] = Query(None),
    region: Optional[str] = Query(None),
    db: DbSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _admin_only(current_user)
    import pandas as pd
    import io

    q = db.query(QcIssue)
    if batch_id: q = q.filter(QcIssue.batch_id == batch_id)
    if severity: q = q.filter(QcIssue.severity == severity)
    if channel:  q = q.filter(QcIssue.channel == channel)
    if game:     q = q.filter(QcIssue.game.contains(game))
    if region:   q = q.filter(QcIssue.region.contains(region))
    issues = q.order_by(QcIssue.batch_id, QcIssue.id).all()

    rows = []
    for i in issues:
        rows.append({
            "严重程度": i.severity, "问题类型": i.issue_type,
            "会话ID": i.session_uid, "会话链接": i.session_link or "",
            "回复时间": i.reply_time or "", "语言": i.language or "",
            "游戏": i.game or "", "地区": i.region or "", "渠道": i.channel or "",
            "AI问题句原文": i.ai_sentence_orig, "AI问题句中译": i.ai_sentence_cn,
            "上下文": i.context, "问题原因": i.reason,
            "修改建议": i.suggestion, "修改后参考回复": i.revised_reply,
            "修改后中译": i.revised_reply_cn,
        })

    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    buf.seek(0)

    fname = f"质检报告_batch{batch_id or 'all'}_{utcnow():%Y%m%d}.xlsx"
    return Response(content=buf.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


# ── 删除单条问题 ───────────────────────────────────────────────────────────────
@router.delete("/issues/{issue_id}", summary="删除单条质检问题（误判处理）")
def delete_issue(
    issue_id: int,
    db: DbSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _admin_only(current_user)
    issue = db.query(QcIssue).filter(QcIssue.id == issue_id).first()
    if not issue:
        raise HTTPException(404, "问题不存在")
    db.delete(issue)
    db.commit()
    return {"message": "已删除"}


# ── 知识库建议列表 ─────────────────────────────────────────────────────────────
@router.get("/kb-suggestions", summary="获取知识库建议")
def list_kb_suggestions(
    batch_id: Optional[int] = Query(None),
    game: Optional[str] = Query(None),
    region: Optional[str] = Query(None),
    channel: Optional[str] = Query(None),
    topic: Optional[str] = Query(None),
    decision: Optional[str] = Query(None),
    processing_status: Optional[str] = Query(None),
    assignment_scope: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: DbSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if processing_status is None and assignment_scope is None:
        _admin_only(current_user)
    # The one-click QA pool uses this existing resource path when callers
    # request an explicit processing state.  Legacy callers without these
    # parameters keep receiving the historical suggestion list contract.
    if processing_status is not None or assignment_scope is not None:
        from routers.knowledge_pool import list_pool_items
        return list_pool_items(
            game=game, region=region, channel=channel,
            processing_status=processing_status or "pending_entry",
            assignment_scope=assignment_scope,
            page=page, page_size=page_size,
            user=current_user, db=db,
        )
    q = db.query(KbSuggestion)
    if batch_id: q = q.filter(KbSuggestion.batch_id == batch_id)
    if game:     q = q.filter(KbSuggestion.game.contains(game))
    if region:   q = q.filter(KbSuggestion.region.contains(region))
    if channel:  q = q.filter(KbSuggestion.channel == channel)
    if topic:    q = q.filter(KbSuggestion.topic.contains(topic))
    items = q.order_by(KbSuggestion.id.desc()).limit(200).all()
    result = [
        {
            "id": kb.id, "batch_id": kb.batch_id,
            "game": kb.game, "region": kb.region, "channel": kb.channel,
            "topic": kb.topic, "question": kb.question,
            "question_cn": kb.question_en or "",   # question_en 存的是中译
            "answer": kb.answer,
            "answer_cn": kb.answer_en or "",        # answer_en 存的是中译
            "agent_name": kb.agent_name,
            "satisfied_expr": kb.satisfied_expr,
            "created_at": kb.created_at.isoformat() if kb.created_at else "",
        }
        for kb in items
    ]
    nq = db.query(QcSliceKnowledgeSuggestion, QcSlice).join(
        QcSlice, QcSliceKnowledgeSuggestion.slice_id == QcSlice.id
    ).filter(QcSliceKnowledgeSuggestion.decision.in_(["candidate_ready", "candidate_needs_enrichment", "candidate_pending_feedback"]))
    if batch_id: nq = nq.filter(QcSlice.batch_id == batch_id)
    if game: nq = nq.filter(QcSlice.game.contains(game))
    if region: nq = nq.filter(QcSlice.region.contains(region))
    if channel: nq = nq.filter(QcSlice.channel == channel)
    if decision: nq = nq.filter(QcSliceKnowledgeSuggestion.decision == decision)
    for suggestion, slice_row in nq.order_by(QcSliceKnowledgeSuggestion.id.desc()).limit(200).all():
        result.append({
            "id": suggestion.id, "batch_id": slice_row.batch_id,
            "game": slice_row.game, "region": slice_row.region, "channel": slice_row.channel,
            "topic": suggestion.category or "", "category": suggestion.category,
            "decision": suggestion.decision, "confidence": suggestion.confidence,
            "answer_source": "human_agent", "title": suggestion.title,
            "question": (json.loads(suggestion.standard_questions or "[]") or [""])[0],
            "standard_questions": json.loads(suggestion.standard_questions or "[]"),
            "answer": suggestion.standard_answer or "", "standard_answer": suggestion.standard_answer,
            "question_cn": "", "answer_cn": "", "agent_name": "人工客服",
            "satisfied_expr": "", "reason": suggestion.reason,
            "reject_reason": suggestion.reject_reason,
            "needs_manual_review": suggestion.needs_manual_review,
            "created_at": suggestion.created_at.isoformat() if suggestion.created_at else "",
            "slice_id": slice_row.slice_id,
        })
    return result


@router.delete("/kb-suggestions/{kb_id}", summary="删除知识库建议")
def delete_kb_suggestion(
    kb_id: int,
    db: DbSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _admin_only(current_user)
    kb = db.query(KbSuggestion).filter(KbSuggestion.id == kb_id).first()
    if not kb:
        raise HTTPException(404, "记录不存在")
    db.delete(kb)
    db.commit()
    return {"message": "已删除"}


# ── 补充KB翻译（给旧数据批量翻译） ────────────────────────────────────────────
@router.post("/kb-suggestions/translate", summary="给未翻译的KB条目补充中文翻译")
async def translate_kb_suggestions(
    batch_id: Optional[int] = Query(None),
    db: DbSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _admin_only(current_user)
    from services.maas_client import translate_to_chinese
    q = db.query(KbSuggestion).filter(
        (KbSuggestion.question_en == None) | (KbSuggestion.question_en == "")
    )
    if batch_id:
        q = q.filter(KbSuggestion.batch_id == batch_id)
    items = q.limit(50).all()

    count = 0
    for kb in items:
        if kb.question and not kb.question_en:
            kb.question_en = await translate_to_chinese(kb.question)
        if kb.answer and not kb.answer_en:
            kb.answer_en = await translate_to_chinese(kb.answer)
        count += 1

    db.commit()
    remaining = db.query(KbSuggestion).filter(
        (KbSuggestion.question_en == None) | (KbSuggestion.question_en == "")
    ).count()
    return {"translated": count, "remaining": remaining}
