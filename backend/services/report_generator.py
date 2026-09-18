from __future__ import annotations

from collections import Counter
from datetime import datetime
from html import escape
from database import utcnow


SEVERITY_META = {
    "严重": ("critical", "严重问题"),
    "中级": ("medium", "中级问题"),
    "一般": ("general", "一般问题"),
    "需人工复核": ("review", "人工复核"),
}


def _text(value: object) -> str:
    return escape(str(value or ""))


def _number(value: object) -> str:
    try:
        return f"{int(value or 0):,}"
    except (TypeError, ValueError):
        return "0"


def _percent(part: int, total: int) -> str:
    return f"{part / total * 100:.1f}%" if total else "0.0%"


def build_html_report(batch_name: str, issues: list, stats: dict, summary_text: str) -> str:
    total_ai = int(stats.get("total_ai_msgs", 0) or 0)
    total_issues = int(stats.get("total_issues", len(issues)) or 0)
    affected_sessions = int(stats.get("affected_sessions", 0) or 0)
    issue_rate = _percent(total_issues, total_ai)

    severity_counts = {
        "严重": int(stats.get("severe", 0) or 0),
        "中级": int(stats.get("medium", 0) or 0),
        "一般": int(stats.get("general", 0) or 0),
        "需人工复核": int(stats.get("review", 0) or 0),
    }
    if not any(severity_counts.values()) and issues:
        detected = Counter(item.severity for item in issues)
        severity_counts = {name: detected[name] for name in SEVERITY_META}

    severity_cards = []
    severity_segments = []
    for name, count in severity_counts.items():
        css_name, label = SEVERITY_META[name]
        severity_cards.append(
            f'<div class="severity-card severity-{css_name}">'
            f'<span class="severity-dot"></span><span>{label}</span>'
            f'<strong>{_number(count)}</strong><small>{_percent(count, total_issues)}</small></div>'
        )
        if count:
            severity_segments.append(
                f'<span class="segment segment-{css_name}" style="width:{count / max(total_issues, 1) * 100:.4f}%" '
                f'title="{_text(label)}：{_number(count)}"></span>'
            )

    distribution = stats.get("type_distribution") or dict(Counter(item.issue_type for item in issues).most_common(10))
    top_types = list(distribution.items())[:8]
    top_max = max((int(count or 0) for _, count in top_types), default=1)
    type_rows = []
    for index, (name, count_value) in enumerate(top_types, 1):
        count = int(count_value or 0)
        type_rows.append(
            '<div class="rank-row">'
            f'<span class="rank-index">{index:02d}</span>'
            f'<div class="rank-main"><div class="rank-label"><span>{_text(name)}</span><strong>{_number(count)}</strong></div>'
            f'<div class="rank-track"><span style="width:{count / top_max * 100:.2f}%"></span></div></div>'
            f'<span class="rank-rate">{_percent(count, total_issues)}</span></div>'
        )
    if not type_rows:
        type_rows.append('<div class="empty-state">本批次暂无问题类型数据</div>')

    issue_cards = []
    for index, item in enumerate(issues, 1):
        severity = str(item.severity or "需人工复核")
        severity_css, severity_label = SEVERITY_META.get(severity, ("review", severity))
        search_text = " ".join(
            str(value or "")
            for value in (item.issue_type, item.game, item.channel, item.ai_sentence_orig, item.reason, item.suggestion)
        ).lower()
        revised_reply = str(item.revised_reply or "").strip()
        revised_block = (
            '<div class="detail-block revised-block"><span class="detail-label">优化后参考回复</span>'
            f'<p>{_text(revised_reply)}</p></div>'
            if revised_reply
            else ""
        )
        issue_cards.append(
            f'<article class="issue-card" data-severity="{_text(severity)}" data-search="{_text(search_text)}">'
            '<header class="issue-header">'
            f'<div class="issue-number">#{index:03d}</div>'
            f'<span class="severity-badge badge-{severity_css}"><i></i>{_text(severity_label)}</span>'
            f'<h3>{_text(item.issue_type or "未分类问题")}</h3>'
            '<div class="issue-meta">'
            f'<span>{_text(item.game or "未标注游戏")}</span><b>·</b><span>{_text(item.channel or "未标注渠道")}</span>'
            '</div></header>'
            '<div class="issue-body">'
            '<div class="detail-block original-block"><span class="detail-label">AI 问题回复</span>'
            f'<p>{_text(item.ai_sentence_orig)}</p></div>'
            '<div class="finding-grid">'
            '<div class="detail-block"><span class="detail-label">问题判断</span>'
            f'<p>{_text(item.reason)}</p></div>'
            '<div class="detail-block suggestion-block"><span class="detail-label">改进建议</span>'
            f'<p>{_text(item.suggestion)}</p></div></div>{revised_block}'
            '</div></article>'
        )

    if not issue_cards:
        issue_cards.append('<div class="empty-state issue-empty">本批次未发现质检问题</div>')

    generated_at = utcnow().strftime("%Y-%m-%d %H:%M:%S")
    safe_batch_name = _text(batch_name)
    safe_summary = _text(summary_text)

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{safe_batch_name} - AI 客服质检报告</title>
<style>
:root{{--ink:#152238;--muted:#65748b;--line:#e3e9f1;--paper:#fff;--canvas:#f3f6fa;--navy:#172a46;
--blue:#2f6fed;--blue-soft:#edf4ff;--red:#dc3f4f;--red-soft:#fff0f1;--orange:#dd7a21;--orange-soft:#fff5e9;
--green:#238267;--green-soft:#eaf8f3;--violet:#7655c5;--violet-soft:#f3efff;--shadow:0 14px 38px rgba(24,42,70,.08)}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:var(--canvas);color:var(--ink);
font:14px/1.65 "Inter","PingFang SC","Microsoft YaHei",system-ui,sans-serif;-webkit-font-smoothing:antialiased}}
.shell{{max-width:1240px;margin:0 auto;padding:36px 28px 72px}}.report-hero{{position:relative;overflow:hidden;
padding:42px 46px;background:linear-gradient(125deg,#142540 0%,#1d3e6e 62%,#285fa7 100%);color:#fff;border-radius:22px;
box-shadow:0 22px 55px rgba(18,39,72,.19)}}.report-hero:after{{content:"";position:absolute;width:340px;height:340px;
right:-90px;top:-170px;border:70px solid rgba(255,255,255,.055);border-radius:50%}}.hero-top{{display:flex;align-items:center;
justify-content:space-between;gap:20px;margin-bottom:52px}}.brand{{display:flex;align-items:center;gap:12px;font-size:12px;
font-weight:700;letter-spacing:.15em}}.brand-mark{{display:grid;place-items:center;width:34px;height:34px;background:#fff;color:var(--navy);
border-radius:10px;font-size:16px;letter-spacing:0}}.report-tag{{padding:7px 12px;border:1px solid rgba(255,255,255,.25);
border-radius:999px;color:#d9e7fa;font-size:12px}}.hero-copy{{position:relative;z-index:1;max-width:850px}}.eyebrow{{margin:0 0 11px;
color:#9fc4f9;font-size:12px;font-weight:700;letter-spacing:.14em;text-transform:uppercase}}h1{{margin:0;font-size:clamp(28px,4vw,46px);
line-height:1.22;letter-spacing:-.035em}}.hero-meta{{display:flex;gap:18px;flex-wrap:wrap;margin-top:18px;color:#bed0e8;font-size:13px}}
.kpi-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-top:-20px;padding:0 22px;position:relative;z-index:2}}
.kpi-card{{padding:22px 24px;background:var(--paper);border:1px solid rgba(220,228,238,.8);border-radius:16px;box-shadow:var(--shadow)}}
.kpi-label{{display:block;color:var(--muted);font-size:12px;font-weight:600}}.kpi-card strong{{display:block;margin:7px 0 2px;font-size:30px;line-height:1.15;
letter-spacing:-.04em}}.kpi-card small{{color:#8c98a9}}.kpi-card.accent strong{{color:var(--blue)}}
.section{{margin-top:28px;padding:30px;background:var(--paper);border:1px solid var(--line);border-radius:18px;box-shadow:0 8px 25px rgba(24,42,70,.04)}}
.section-heading{{display:flex;align-items:flex-end;justify-content:space-between;gap:20px;margin-bottom:24px}}.section-kicker{{display:block;margin-bottom:5px;
color:var(--blue);font-size:11px;font-weight:800;letter-spacing:.14em;text-transform:uppercase}}h2{{margin:0;font-size:22px;line-height:1.3;letter-spacing:-.02em}}
.section-note{{color:var(--muted);font-size:12px}}.summary-box{{display:flex;gap:16px;align-items:flex-start;padding:19px 21px;background:var(--blue-soft);
border-left:4px solid var(--blue);border-radius:5px 12px 12px 5px;color:#294365}}.summary-icon{{flex:0 0 auto;display:grid;place-items:center;
width:30px;height:30px;background:#fff;border-radius:9px;color:var(--blue);font-weight:800}}.summary-box p{{margin:2px 0 0}}
.overview-grid{{display:grid;grid-template-columns:minmax(0,1.1fr) minmax(320px,.9fr);gap:26px;margin-top:24px}}
.severity-grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}}.severity-card{{display:grid;grid-template-columns:12px 1fr auto;
align-items:center;gap:9px;padding:16px;border:1px solid var(--line);border-radius:13px}}.severity-card strong{{font-size:21px;line-height:1}}
.severity-card small{{grid-column:2/4;color:var(--muted);font-size:11px}}.severity-dot{{width:9px;height:9px;border-radius:50%}}
.severity-critical .severity-dot,.segment-critical{{background:var(--red)}}.severity-medium .severity-dot,.segment-medium{{background:var(--orange)}}
.severity-general .severity-dot,.segment-general{{background:var(--green)}}.severity-review .severity-dot,.segment-review{{background:var(--violet)}}
.severity-bar{{display:flex;height:11px;overflow:hidden;margin-top:16px;background:#edf1f5;border-radius:999px}}.segment{{min-width:3px;height:100%}}
.rank-list{{display:grid;gap:15px}}.rank-row{{display:grid;grid-template-columns:27px 1fr 48px;gap:10px;align-items:center}}.rank-index{{color:#9aa6b6;
font-size:11px;font-variant-numeric:tabular-nums}}.rank-label{{display:flex;justify-content:space-between;gap:12px;margin-bottom:5px;font-size:12px}}.rank-label strong{{font-variant-numeric:tabular-nums}}
.rank-track{{height:5px;background:#edf1f6;border-radius:999px;overflow:hidden}}.rank-track span{{display:block;height:100%;background:linear-gradient(90deg,#4c81ec,#80a9fb);border-radius:999px}}
.rank-rate{{color:var(--muted);font-size:11px;text-align:right}}
.toolbar{{position:sticky;top:12px;z-index:5;display:flex;gap:12px;align-items:center;margin-bottom:18px;padding:11px;background:rgba(255,255,255,.92);
border:1px solid var(--line);border-radius:14px;box-shadow:0 8px 25px rgba(24,42,70,.08);backdrop-filter:blur(12px)}}.search-wrap{{position:relative;flex:1}}
.search-wrap:before{{content:"⌕";position:absolute;left:14px;top:8px;color:#7f8da0;font-size:20px}}input[type=search]{{width:100%;height:42px;padding:0 15px 0 41px;
border:1px solid var(--line);outline:0;border-radius:10px;background:#f8fafc;color:var(--ink);font:inherit}}input[type=search]:focus{{border-color:#91b2ef;background:#fff;box-shadow:0 0 0 3px #edf4ff}}
.filter-group{{display:flex;gap:7px;flex-wrap:wrap}}.filter-button{{padding:9px 12px;border:0;border-radius:9px;background:#f1f4f8;color:#536176;font:600 12px inherit;cursor:pointer}}
.filter-button:hover,.filter-button.active{{background:var(--navy);color:#fff}}.result-count{{min-width:84px;color:var(--muted);font-size:12px;text-align:right}}
.page-size{{height:42px;padding:0 11px;border:1px solid var(--line);border-radius:10px;background:#f8fafc;color:#536176;font:600 12px inherit;outline:0}}
.pagination{{display:flex;align-items:center;justify-content:center;gap:12px;margin-top:18px;padding-top:18px;border-top:1px solid var(--line)}}
.page-button{{min-width:74px;height:36px;padding:0 12px;border:1px solid var(--line);border-radius:9px;background:#fff;color:#536176;font:600 12px inherit;cursor:pointer}}
.page-button:hover:not(:disabled){{border-color:#a9bfe9;background:var(--blue-soft);color:var(--blue)}}.page-button:disabled{{cursor:not-allowed;opacity:.42}}
.page-info{{min-width:110px;color:var(--muted);font-size:12px;text-align:center}}
.issue-list{{display:grid;gap:14px}}.issue-card{{overflow:hidden;border:1px solid var(--line);border-radius:15px;background:#fff;transition:.18s ease}}
.issue-card:hover{{border-color:#cbd7e6;box-shadow:0 10px 28px rgba(24,42,70,.07);transform:translateY(-1px)}}.issue-card[hidden]{{display:none}}
.issue-header{{display:grid;grid-template-columns:56px auto minmax(160px,1fr) auto;align-items:center;gap:12px;padding:16px 20px;background:#fbfcfe;border-bottom:1px solid var(--line)}}
.issue-number{{color:#96a2b2;font:600 11px/1.2 ui-monospace,monospace}}.severity-badge{{display:inline-flex;align-items:center;gap:6px;padding:5px 9px;border-radius:999px;font-size:11px;font-weight:700;white-space:nowrap}}
.severity-badge i{{width:6px;height:6px;border-radius:50%;background:currentColor}}.badge-critical{{color:var(--red);background:var(--red-soft)}}.badge-medium{{color:var(--orange);background:var(--orange-soft)}}
.badge-general{{color:var(--green);background:var(--green-soft)}}.badge-review{{color:var(--violet);background:var(--violet-soft)}}.issue-header h3{{margin:0;font-size:15px}}
.issue-meta{{display:flex;align-items:center;gap:7px;color:var(--muted);font-size:11px}}.issue-meta b{{color:#c5cdd8}}.issue-body{{padding:20px}}
.detail-block{{padding-left:14px;border-left:2px solid #dbe3ed}}.detail-label{{display:block;margin-bottom:5px;color:#748297;font-size:10px;font-weight:800;letter-spacing:.08em;text-transform:uppercase}}
.detail-block p{{margin:0;white-space:pre-wrap;overflow-wrap:anywhere}}.original-block{{padding:14px 16px;background:#f6f8fb;border-left-color:#95a6bd;border-radius:0 10px 10px 0}}
.original-block p{{font-weight:600;color:#29384d}}.finding-grid{{display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-top:18px}}.suggestion-block{{border-left-color:#80a9f2}}
.revised-block{{margin-top:18px;padding:14px 16px;background:#f0f8f5;border-left-color:#5aa58e;border-radius:0 10px 10px 0}}.empty-state{{padding:26px;text-align:center;color:var(--muted);background:#f8fafc;border-radius:12px}}
.issue-empty{{padding:60px}}.report-footer{{display:flex;justify-content:space-between;gap:20px;margin-top:26px;padding:0 4px;color:#8b98aa;font-size:11px}}
@media(max-width:850px){{.shell{{padding:18px 14px 45px}}.report-hero{{padding:28px 24px}}.hero-top{{margin-bottom:36px}}.kpi-grid{{grid-template-columns:1fr 1fr;padding:0 8px}}
.overview-grid{{grid-template-columns:1fr}}.issue-header{{grid-template-columns:45px auto 1fr}}.issue-meta{{grid-column:2/4}}.toolbar{{align-items:stretch;flex-direction:column}}.result-count{{text-align:left}}
.finding-grid{{grid-template-columns:1fr}}}}@media(max-width:520px){{.kpi-grid,.severity-grid{{grid-template-columns:1fr}}.hero-top{{align-items:flex-start;flex-direction:column}}.section{{padding:21px 16px}}}}
@media print{{@page{{size:A4;margin:13mm}}body{{background:#fff;font-size:10px}}.shell{{max-width:none;padding:0}}.report-hero{{padding:24px;border-radius:0;box-shadow:none;-webkit-print-color-adjust:exact;print-color-adjust:exact}}
.hero-top{{margin-bottom:24px}}h1{{font-size:28px}}.kpi-grid{{margin:12px 0 0;padding:0;box-shadow:none}}.kpi-card,.section{{box-shadow:none;break-inside:avoid}}.toolbar,.pagination{{display:none}}.issue-card{{break-inside:avoid;box-shadow:none}}
.issue-card:hover{{transform:none}}.section{{margin-top:14px;padding:18px}}.report-footer{{margin-top:14px}}}}
</style>
</head>
<body>
<main class="shell">
  <header class="report-hero">
    <div class="hero-top"><div class="brand"><span class="brand-mark">Q</span><span>AI QUALITY INSIGHT</span></div><span class="report-tag">质量检测报告</span></div>
    <div class="hero-copy"><p class="eyebrow">Customer Service Quality Review</p><h1>{safe_batch_name}</h1>
      <div class="hero-meta"><span>报告生成时间&nbsp; {generated_at}</span><span>·</span><span>数据范围&nbsp; 当前质检批次</span></div>
    </div>
  </header>
  <section class="kpi-grid" aria-label="核心指标">
    <div class="kpi-card"><span class="kpi-label">已质检 AI 消息</span><strong>{_number(total_ai)}</strong><small>条回复</small></div>
    <div class="kpi-card"><span class="kpi-label">发现问题</span><strong>{_number(total_issues)}</strong><small>条问题记录</small></div>
    <div class="kpi-card accent"><span class="kpi-label">问题检出率</span><strong>{issue_rate}</strong><small>问题数 / AI 消息数</small></div>
    <div class="kpi-card"><span class="kpi-label">影响会话</span><strong>{_number(affected_sessions)}</strong><small>个独立服务事件</small></div>
  </section>
  <section class="section">
    <div class="section-heading"><div><span class="section-kicker">Executive Summary</span><h2>质量概览</h2></div><span class="section-note">快速定位主要风险与改进方向</span></div>
    <div class="summary-box"><span class="summary-icon">i</span><p>{safe_summary}</p></div>
    <div class="overview-grid">
      <div><div class="severity-grid">{''.join(severity_cards)}</div><div class="severity-bar" aria-label="问题严重度分布">{''.join(severity_segments)}</div></div>
      <div class="rank-list">{''.join(type_rows)}</div>
    </div>
  </section>
  <section class="section issues-section">
    <div class="section-heading"><div><span class="section-kicker">Detailed Findings</span><h2>问题明细</h2></div><span class="section-note">支持严重度筛选</span></div>
    <div class="toolbar">
      <div class="search-wrap"><input id="issue-search" type="search" placeholder="搜索问题类型、游戏、渠道或内容…" autocomplete="off"></div>
      <div class="filter-group" role="group" aria-label="严重度筛选">
        <button class="filter-button active" type="button" data-filter="">全部</button>
        <button class="filter-button" type="button" data-filter="严重">严重</button>
        <button class="filter-button" type="button" data-filter="中级">中级</button>
        <button class="filter-button" type="button" data-filter="一般">一般</button>
        <button class="filter-button" type="button" data-filter="需人工复核">人工复核</button>
      </div>
      <select id="page-size" class="page-size" aria-label="每页条数"><option value="50">每页 50 条</option><option value="100">每页 100 条</option><option value="200">每页 200 条</option><option value="500">每页 500 条</option></select>
      <span class="result-count" id="result-count">共 {_number(len(issues))} 条</span>
    </div>
    <div class="issue-list" id="issue-list">{''.join(issue_cards)}</div>
    <nav class="pagination" aria-label="问题列表分页"><button id="prev-page" class="page-button" type="button">上一页</button><span id="page-info" class="page-info"></span><button id="next-page" class="page-button" type="button">下一页</button></nav>
  </section>
  <footer class="report-footer"><span>AI 客服质检 · 内部质量改进使用</span><span>{safe_batch_name}</span></footer>
</main>
<script>
(() => {{
  const cards = [...document.querySelectorAll('.issue-card')];
  const input = document.getElementById('issue-search');
  const count = document.getElementById('result-count');
  const pageSizeSelect = document.getElementById('page-size');
  const prevPage = document.getElementById('prev-page');
  const nextPage = document.getElementById('next-page');
  const pageInfo = document.getElementById('page-info');
  const buttons = [...document.querySelectorAll('.filter-button')];
  let severity = '';
  let currentPage = 1;
  let filteredCards = cards;
  const renderPage = () => {{
    const pageSize = Number(pageSizeSelect?.value || 50);
    const totalPages = Math.max(1, Math.ceil(filteredCards.length / pageSize));
    currentPage = Math.min(currentPage, totalPages);
    const start = (currentPage - 1) * pageSize;
    const end = start + pageSize;
    cards.forEach(card => {{ card.hidden = !filteredCards.includes(card) || !filteredCards.slice(start, end).includes(card); }});
    if (pageInfo) pageInfo.textContent = `${{currentPage}} / ${{totalPages}} 页`;
    if (prevPage) prevPage.disabled = currentPage <= 1;
    if (nextPage) nextPage.disabled = currentPage >= totalPages;
    if (count) count.textContent = `显示 ${{filteredCards.length}} / ${{cards.length}} 条`;
  }};
  const apply = () => {{
    const query = (input?.value || '').trim().toLocaleLowerCase();
    filteredCards = cards.filter(card => {{
      const matchSeverity = !severity || card.dataset.severity === severity;
      const matchQuery = !query || (card.dataset.search || '').includes(query);
      return matchSeverity && matchQuery;
    }});
    currentPage = 1;
    renderPage();
  }};
  input?.addEventListener('input', apply);
  pageSizeSelect?.addEventListener('change', () => {{ currentPage = 1; renderPage(); }});
  prevPage?.addEventListener('click', () => {{ if (currentPage > 1) {{ currentPage -= 1; renderPage(); window.scrollTo({{ top: document.querySelector('.issues-section')?.offsetTop || 0, behavior: 'smooth' }}); }} }});
  nextPage?.addEventListener('click', () => {{ const pageSize = Number(pageSizeSelect?.value || 50); if (currentPage < Math.ceil(filteredCards.length / pageSize)) {{ currentPage += 1; renderPage(); window.scrollTo({{ top: document.querySelector('.issues-section')?.offsetTop || 0, behavior: 'smooth' }}); }} }});
  buttons.forEach(button => button.addEventListener('click', () => {{
    severity = button.dataset.filter || '';
    buttons.forEach(item => item.classList.toggle('active', item === button));
    apply();
  }}));
  renderPage();
}})();
</script>
</body>
</html>"""
