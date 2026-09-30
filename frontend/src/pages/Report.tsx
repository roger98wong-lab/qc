import { useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  Card, Select, Button, Table, Tag, Space, Typography, Input, DatePicker, Alert,
  Descriptions, message, Row, Col, Statistic,
  Tabs, Popconfirm, Tooltip, Badge, Collapse, Modal,
} from 'antd'
import {
  DownloadOutlined, FilterOutlined, EyeOutlined,
  DeleteOutlined, BookOutlined, WarningOutlined,
} from '@ant-design/icons'
import { reportApi, analysisApi, reviewApi, reviewWorkbenchApi } from '../api'
import { applyAssignment } from '../applyAssignment'
import { useAuthStore } from '../store/auth'
import { ConversationMessageList, conversationFromCandidate, conversationFromIssue } from '../components/Conversation'
import MultiFilterSelect from '../components/MultiFilterSelect'
import { parseBatchIds, pickDefaultBatchId, reportBatchQuery, shouldFetchReportBatchData, createRequestGate, workbenchExpectedKey, workbenchItemType, applyWorkbenchListTotal, paginationTotalForTab, type ReportTab } from './reportBatchGate'
import ReviewProcessingPanel from '../components/ReviewProcessingPanel'
import DetailModalLayout from '../components/DetailModalLayout'

function normalizeSliceResult(raw: any): any {
  // New slice rows already expose every issue under all_issues.  Prefer that
  // contract before falling back to the workflow-shaped payloads, otherwise
  // the outer list row is mistakenly treated as the primary issue and its
  // nested player_question / ai_answer fields are lost in the drawer.
  const rawIssues = Array.isArray(raw?.all_issues) && raw.all_issues.length
    ? raw.all_issues
    : (Array.isArray(raw?.issues) ? raw.issues : (Array.isArray(raw?.quality_check?.issues) ? raw.quality_check.issues : []))
  const sortedIssues = [...rawIssues].sort((a, b) => Number(b?.confidence || 0) - Number(a?.confidence || 0))
  const main = sortedIssues[0] || raw?.quality_check || raw
  if (!sortedIssues.length && !main?.issue_type && !raw?.issue_type) return { ...raw, all_issues: [] }
  const allIssues = sortedIssues.length ? sortedIssues : [main]
  return {
    ...raw,
    ...main,
    issue_type: main.issue_type ?? raw.issue_type,
    severity: main.severity ?? raw.severity,
    confidence: main.confidence ?? raw.confidence,
    all_issues: allIssues,
  }
}

type TextPair = { original: string; zhCn: string }

function getIssueTextPair(issue: any, field: 'player_question' | 'ai_answer', messages: any[] = []): TextPair {
  const pair = issue?.[field]
  const isPlayerQuestion = field === 'player_question'
  const original = isPlayerQuestion
    ? pair?.original ?? issue?.player_question_original ?? issue?.question_original
    : pair?.original ?? issue?.ai_sentence_orig ?? issue?.answer_original
  const zhCn = isPlayerQuestion
    ? pair?.zh_cn ?? issue?.player_question_zh_cn ?? issue?.question_zh_cn
    : pair?.zh_cn ?? issue?.ai_sentence_cn ?? issue?.answer_zh_cn
  const expectedRole = isPlayerQuestion ? 'player' : 'ai'
  const referencedIds = new Set<string>(
    (isPlayerQuestion
      ? issue?.question_message_ids || issue?.evidence_message_ids
      : issue?.ai_message_ids || issue?.answer_message_ids
    ) || [],
  )
  const matchingMessages = messages.filter((message: any) => {
    const role = message?.role || message?.speaker
    const text = String(message?.original_text ?? message?.text ?? '').trim()
    return role === expectedRole && text && text !== '[file]' &&
      (!referencedIds.size || referencedIds.has(String(message?.message_id)))
  })
  if (!matchingMessages.length) return { original: String(original || ''), zhCn: String(zhCn || '') }
  const fullOriginal = matchingMessages.map((message: any) => String(message.original_text ?? message.text ?? '')).join('\n')
  const translated = matchingMessages
    .map((message: any) => message?.translation?.translated_text ?? message?.zh_cn ?? '')
    .filter(Boolean)
    .join('\n')
  return { original: fullOriginal || String(original || ''), zhCn: String(zhCn || translated || '') }
}
function TextPairLines({ pair }: { pair: TextPair }) {
  return <div className="qc-text-pair">
    <div className="qc-text-pair-line"><span className="qc-text-pair-label">原文</span><div className="qc-text-pair-content">{pair.original || '—'}</div></div>
    <div className="qc-text-pair-line qc-text-pair-translation"><span className="qc-text-pair-label">中文翻译</span><div className="qc-text-pair-content">{pair.zhCn || '—'}</div></div>
  </div>
}

function hasQualityIssue(row: any) {
  if (row?.quality_check?.has_issue === false || row?.has_issue === false) return false
  return Boolean(row?.issue_type || row?.all_issues?.length)
}

function messagesWithEvidence(row: any) {
  const issues = row?.all_issues?.length ? row.all_issues : (row ? [row] : [])
  const evidence = new Set<string>(issues.flatMap((issue: any) => issue?.evidence_message_ids || []))
  const questions = new Set<string>(issues.flatMap((issue: any) => issue?.question_message_ids || []))
  const answers = new Set<string>(issues.flatMap((issue: any) => issue?.ai_message_ids || issue?.answer_message_ids || []))
  return (row?.messages || []).map((item: any) => ({
    ...item,
    role: item.role || item.speaker,
    reference_tags: Array.from(new Set([
      ...(item.reference_tags || []),
      ...(questions.has(item.message_id) ? ['Q'] : []),
      ...(answers.has(item.message_id) ? ['A'] : []),
      ...(evidence.has(item.message_id) ? ['证据'] : []),
    ])),
  }))
}
const DECISION_LABELS: Record<string, string> = {
  candidate_ready: '可直接沉淀',
  candidate_needs_enrichment: '需补充后沉淀',
  candidate_pending_feedback: '待验证候选',
  not_candidate: '不适合沉淀',
  reject: '不适合沉淀',
  manual_review: '人工复核',
  no_human_answer: '无人工回复',
}
const VALIDATION_STATUS_LABELS: Record<string, string> = {
  player_validated: '玩家已明确认可',
  unvalidated: '未经玩家明确验证',
  not_applicable: '不适用',
}
const HANDOFF_DECISION_LABELS: Record<string, string> = {
  handoff_required: '需要转人工',
  handoff_reasonable: '转人工合理',
  handoff_not_required: '无需转人工',
  handoff_unreasonable: '转人工不合理',
  manual_review: '需人工复核',
}
const DEFAULT_KB_DECISIONS = ['candidate_ready', 'candidate_needs_enrichment', 'candidate_pending_feedback', 'manual_review']
const KB_DECISION_OPTIONS = [
  { value: 'candidate_ready', label: '可直接沉淀' },
  { value: 'candidate_needs_enrichment', label: '需补充后沉淀' },
  { value: 'candidate_pending_feedback', label: '待验证候选' },
  { value: 'manual_review', label: '人工复核' },
  { value: 'not_candidate', label: '不适合沉淀' },
  { value: 'no_human_answer', label: '无人工回复' },
]
const POOL_KB_DECISIONS = ['candidate_ready', 'candidate_needs_enrichment', 'candidate_pending_feedback']

function AnalysisSection({ title, children }: { title: string; children: React.ReactNode }) {
  return <section className="qc-analysis-section">
    <div className="qc-analysis-section-title">{title}</div>
    <div className="qc-analysis-section-content">{children || '—'}</div>
  </section>
}

function hasDisplayText(value: unknown): boolean {
  if (value == null) return false
  if (typeof value === 'string') return Boolean(value.trim())
  if (Array.isArray(value)) return value.some(hasDisplayText)
  return true
}

function HumanHandoffCard({ handoff, onFocusMessage }: { handoff: any; onFocusMessage?: (messageId: string) => void }) {
  if (!handoff || typeof handoff !== 'object') return null
  const evidenceIds = Array.isArray(handoff.evidence_message_ids) ? handoff.evidence_message_ids : []
  return <Card size="small" className="qc-analysis-card" title="转人工判断">
    <Descriptions column={1} bordered size="small">
      <Descriptions.Item label="转人工结论">{HANDOFF_DECISION_LABELS[handoff.decision] || displayValue(handoff.decision)}</Descriptions.Item>
      <Descriptions.Item label="是否已转人工">{handoff.handoff_occurred ? '已转人工' : '未转人工'}</Descriptions.Item>
      <Descriptions.Item label="原因类型">{displayValue(handoff.reason_type)}</Descriptions.Item>
      <Descriptions.Item label="原因">{displayValue(handoff.reason)}</Descriptions.Item>
      {handoff.needs_manual_review ? <Descriptions.Item label="人工复核原因">{displayValue(handoff.manual_review_reason)}</Descriptions.Item> : null}
      <Descriptions.Item label="证据消息">
        {evidenceIds.length
          ? <Space size={[4, 4]} wrap>{evidenceIds.map((messageId: string) => <Button key={messageId} type="text" size="small" className="qc-evidence-id" onClick={() => onFocusMessage?.(messageId)}>{messageId}</Button>)}</Space>
          : '—'}
      </Descriptions.Item>
    </Descriptions>
  </Card>
}

function termDisplayName(term: any): string {
  return displayValue(term?.text || term?.suggested_standard_term, "")
}

function termDisplayZh(term: any): string {
  return displayValue(term?.zh_cn || term?.zh_cn_meaning, "")
}

function TermSuggestionsCard({ terms }: { terms: any; onFocusMessage?: (messageId: string) => void }) {
  if (!terms?.has_terms || !Array.isArray(terms.terms) || !terms.terms.length) return null
  return <Card size="small" className="qc-analysis-card" title="需检查术语">
    <Typography.Paragraph type="secondary" style={{ marginBottom: 12 }}>
      请核对这些名称是否与游戏一致，避免知识上线后 AI 客服无法召回。下列名称仅供核对，不是已确认的官方术语。
    </Typography.Paragraph>
    <ol className="qc-term-list" style={{ margin: 0, paddingLeft: 22 }}>
      {terms.terms.slice(0, 3).map((term: any, index: number) => {
        const name = termDisplayName(term)
        const zh = termDisplayZh(term)
        return <li key={index}>{name || "—"}{zh && zh !== name ? `（${zh}）` : ""}</li>
      })}
    </ol>
  </Card>
}

function IssueAnalysisCards({ issues, messages = [], onFocusMessage }: { issues: any[]; messages?: any[]; onFocusMessage?: (messageId: string) => void }) {
  if (!issues.length) return <Text type="secondary">当前切片没有可展示的问题分析。</Text>
  return <div className="qc-analysis-card-list">
    {issues.map((issue, index) => {
      const playerQuestion = getIssueTextPair(issue, 'player_question', messages)
      const aiAnswer = getIssueTextPair(issue, 'ai_answer', messages)
      const evidenceIds = Array.isArray(issue?.evidence_message_ids) ? issue.evidence_message_ids : []
      return <Card key={issue.issue_id || index} size="small" className="qc-analysis-card" title={index === 0 ? '主问题（置信度最高）' : `附加问题 ${index + 1}`} extra={<Space size={4} wrap><Tag color={SEV_COLOR[issue.severity]}>{issue.severity || '—'}</Tag><Tag color="blue">{issue.issue_type || '—'}</Tag>{issue.needs_manual_review && <Tag color="warning">需人工复核</Tag>}</Space>}>
        <AnalysisSection title="玩家问题"><TextPairLines pair={playerQuestion} /></AnalysisSection>
        <AnalysisSection title="AI 回复"><TextPairLines pair={aiAnswer} /></AnalysisSection>
        {hasDisplayText(issue.reason) ? <AnalysisSection title="问题原因">{issue.reason}</AnalysisSection> : null}
        {hasDisplayText(issue.suggestion) ? <AnalysisSection title="修改建议">{issue.suggestion}</AnalysisSection> : null}
        {(hasDisplayText(issue.revised_reply) || hasDisplayText(issue.revised_reply_zh_cn) || hasDisplayText(issue.revised_reply_cn))
          ? <AnalysisSection title="修改后参考回复"><TextPairLines pair={{ original: issue.revised_reply || '', zhCn: issue.revised_reply_zh_cn || issue.revised_reply_cn || '' }} /></AnalysisSection>
          : null}
        <AnalysisSection title="证据消息 ID">
          {evidenceIds.length ? <Space size={[4, 4]} wrap>{evidenceIds.map((messageId: string) => <Button key={messageId} type="text" size="small" className="qc-evidence-id" onClick={() => onFocusMessage?.(messageId)}>{messageId}</Button>)}</Space> : '—'}
        </AnalysisSection>
        <AnalysisSection title="人工复核信息">
          {issue.needs_manual_review ? <Tag color="warning">需要人工复核</Tag> : <Tag>无需人工复核</Tag>}{displayValue(issue.manual_review_reason) !== '—' ? <span className="qc-analysis-inline-text">{displayValue(issue.manual_review_reason)}</span> : null}
        </AnalysisSection>
      </Card>
    })}
  </div>
}
const { Text } = Typography
const ISSUE_CATEGORIES = ['兜底异常', '其他待复核', '其他疑似问题', '情绪风险', '技术异常', '数据异常', '无效回复', '服务未满足', '知识错误', '知识错误/幻觉风险', '转人工问题', '风险场景未满足']
const { RangePicker } = DatePicker
const ANALYSIS_STATUS_LABELS: Record<string, string> = {
  pending: '等待分析', processing: '分析中', completed: '分析完成', partial: '部分完成', failed: '分析失败',
}
const ASSIGNMENT_STATUS_OPTIONS = [
  { value: 'all', label: '全部审核状态' },
  { value: 'unassigned', label: '未分配' },
  { value: 'assigned', label: '已分配' },
  { value: 'in_progress', label: '审核中' },
  { value: 'completed', label: '已完成' },
  { value: 'returned', label: '已退回' },
  { value: 'cancelled', label: '已取消' },
]

const SEV_COLOR: Record<string, string> = {
  '严重': 'red', '中级': 'orange', '一般': 'blue', '需人工复核': 'default',
}

function displayValue(value: unknown, fallback = '—'): string {
  if (value === null || value === undefined) return fallback
  if (typeof value === 'string') return value.trim() ? value : fallback
  if (Array.isArray(value)) return value.length ? value.map(item => displayValue(item, '')).filter(Boolean).join('、') || fallback : fallback
  if (typeof value === 'object') return fallback
  return String(value)
}

const AGENT_NAME_DISCARDED = /^(system|auto_reply|ai|unknown|系统|用户|玩家|客服|人工客服|operator|agent|human_agent|command|slash command|slash-command|slash_command|application command)$/i
const AGENT_NAME_PREFIX = /^(?:人工客服|human_agent|operator|agent|客服)(?:[\s\-－‐‑‒–—―:：/、]+)/i
const AGENT_NAME_DECORATION = /^[^\p{L}\p{N}]+/u

function looksLikeAgentPersonName(text: string): boolean {
  const value = text.trim()
  if (!value || value.length > 24) return false
  if (/[.?!。！？\n\r{}\[\]"]/.test(value)) return false
  if (value.split(/\s+/).filter(Boolean).length > 3) return false
  return true
}

function cleanDisplayedAgentName(value: unknown): string | null {
  let text = String(value || '').trim()
  if (!text) return null
  while (true) {
    const stripped = text.replace(AGENT_NAME_DECORATION, '').replace(AGENT_NAME_PREFIX, '').trim()
    if (stripped === text) break
    text = stripped
  }
  if (!text || AGENT_NAME_DISCARDED.test(text) || !looksLikeAgentPersonName(text)) return null
  return text
}

function displayHumanAgentName(row: any): string {
  const preferred = displayValue(row?.human_agent_name, '')
  const fallback = displayValue(row?.agent_name, '')
  const names = (preferred || fallback).split('、').map(cleanDisplayedAgentName).filter((name): name is string => Boolean(name))
  return Array.from(new Set(names)).join('、') || '—'
}

function formatDisplayTime(value: unknown): string {
  const raw = displayValue(value, '')
  if (!raw) return '—'
  const local = raw.match(/^(\d{4}-\d{2}-\d{2})[T\s](\d{2}:\d{2}:\d{2})(?:\.\d+)?$/)
  if (local) return `${local[1]} ${local[2]}`
  const date = new Date(raw)
  if (Number.isNaN(date.getTime())) return raw.replace('T', ' ')
  return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' }).format(date).replace(/\//g, '-')
}

const LANGUAGE_LABELS: Record<string, string> = {
  zh_cn: '中文（简体）', zh_tw: '中文（繁体）',
  vn: '越南语', vi: '越南语',
  tr: '土耳其语', tl: '塔加路语（菲律宾语）', th: '泰语', ms: '马来语',
  sv: '瑞典语', ru: '俄语', pt: '葡萄牙语', pl: '波兰语',
  no: '挪威语', nb: '挪威语', nn: '挪威语',
  nl: '荷兰语', ko: '韩语', ja: '日语', it: '意大利语',
  id: '印度尼西亚语', fr: '法语', es: '西班牙语', en: '英语',
  el: '希腊语', de: '德语', ar: '阿拉伯语', ara: '阿拉伯语', hi: '印地语',
  aa: '阿法尔语', ab: '阿布哈兹语', ae: '阿维斯陀语', af: '南非荷兰语', ak: '阿坎语',
  am: '阿姆哈拉语', an: '阿拉贡语', as: '阿萨姆语', av: '阿瓦尔语', ay: '艾马拉语',
  az: '阿塞拜疆语', ba: '巴什基尔语', be: '白俄罗斯语', bg: '保加利亚语', bh: '比哈尔语',
  bi: '比斯拉马语', bm: '班巴拉语', bn: '孟加拉语', bo: '藏语', br: '布列塔尼语',
  bs: '波斯尼亚语', ca: '加泰罗尼亚语', ce: '车臣语', ch: '查莫罗语', co: '科西嘉语',
  cr: '克里语', cs: '捷克语', cu: '教会斯拉夫语', cv: '楚瓦什语', cy: '威尔士语',
  da: '丹麦语', dv: '迪维希语', dz: '宗喀语', ee: '埃维语', eo: '世界语',
  et: '爱沙尼亚语', eu: '巴斯克语', fa: '波斯语', ff: '富拉语', fi: '芬兰语',
  fj: '斐济语', fo: '法罗语', fy: '西弗里西亚语', ga: '爱尔兰语', gd: '苏格兰盖尔语',
  gl: '加利西亚语', gn: '瓜拉尼语', gu: '古吉拉特语', gv: '马恩岛语', ha: '豪萨语',
  he: '希伯来语', ho: '希里莫图语', hr: '克罗地亚语', ht: '海地克里奥尔语', hu: '匈牙利语',
  hy: '亚美尼亚语', hz: '赫雷罗语', ia: '国际语', ie: '西方国际语', ig: '伊博语',
  ii: '彝语', ik: '伊努皮克语', io: '伊多语', is: '冰岛语', iu: '因纽特语',
  jv: '爪哇语', ka: '格鲁吉亚语', kg: '刚果语', ki: '基库尤语', kj: '宽亚玛语',
  kk: '哈萨克语', kl: '格陵兰语', km: '高棉语', kn: '卡纳达语', kr: '卡努里语',
  ks: '克什米尔语', ku: '库尔德语', kv: '科米语', kw: '康沃尔语', ky: '吉尔吉斯语',
  la: '拉丁语', lb: '卢森堡语', lg: '卢干达语', li: '林堡语', ln: '林加拉语',
  lo: '老挝语', lt: '立陶宛语', lu: '卢巴-加丹加语', lv: '拉脱维亚语', mg: '马达加斯加语',
  mh: '马绍尔语', mi: '毛利语', mk: '马其顿语', ml: '马拉雅拉姆语', mn: '蒙古语',
  mr: '马拉地语', mt: '马耳他语', my: '缅甸语', na: '瑙鲁语', nd: '北恩德贝莱语',
  ne: '尼泊尔语', ng: '恩敦加语', nr: '南恩德贝莱语', nv: '纳瓦霍语', ny: '齐切瓦语',
  oc: '奥克语', oj: '奥吉布瓦语', om: '奥罗莫语', or: '奥里亚语', os: '奥塞梯语',
  pa: '旁遮普语', pi: '巴利语', ps: '普什图语', qu: '克丘亚语', rm: '罗曼什语',
  rn: '基隆迪语', ro: '罗马尼亚语', rw: '卢旺达语', sa: '梵语', sc: '撒丁语',
  sd: '信德语', se: '北萨米语', sg: '桑戈语', si: '僧伽罗语', sk: '斯洛伐克语',
  sl: '斯洛文尼亚语', sm: '萨摩亚语', sn: '绍纳语', so: '索马里语', sq: '阿尔巴尼亚语',
  sr: '塞尔维亚语', ss: '斯瓦蒂语', st: '南索托语', su: '巽他语', sw: '斯瓦希里语',
  ta: '泰米尔语', te: '泰卢固语', tg: '塔吉克语', ti: '提格利尼亚语', tk: '土库曼语',
  tn: '茨瓦纳语', to: '汤加语', ts: '聪加语', tt: '鞑靼语', tw: '契维语',
  ty: '塔希提语', ug: '维吾尔语', uk: '乌克兰语', ur: '乌尔都语', uz: '乌兹别克语',
  ve: '文达语', vo: '沃拉普克语', wa: '瓦隆语', wo: '沃洛夫语', xh: '科萨语',
  yi: '意第绪语', yo: '约鲁巴语', za: '壮语', zu: '祖鲁语',
}

const LANGUAGE_ALIAS: Record<string, string> = {
  zh: 'zh_cn', 'zh-cn': 'zh_cn', 'zh_cn': 'zh_cn', 'zh-hans': 'zh_cn', 'zh_hans': 'zh_cn',
  'zh-tw': 'zh_tw', 'zh_tw': 'zh_tw', 'zh-hant': 'zh_tw', 'zh_hant': 'zh_tw', 'zh-hk': 'zh_tw', 'zh_hk': 'zh_tw',
  'zh-mo': 'zh_tw', 'zh_mo': 'zh_tw',
  vn: 'vn', vi: 'vn',
  ara: 'ar', he: 'he', iw: 'he', jw: 'jv', 'in': 'id',
}

const INVALID_SOURCE_LANGUAGES = new Set(['unknown', 'und', 'null', 'none', 'n/a', 'na', ''])

function normalizeLanguageCode(value: unknown): string | null {
  const text = String(value ?? '').trim()
  if (!text || INVALID_SOURCE_LANGUAGES.has(text.toLowerCase())) return null
  const lower = text.replace(/-/g, '_').toLowerCase()
  if (LANGUAGE_ALIAS[lower]) return LANGUAGE_ALIAS[lower]
  const base = lower.split('_')[0]
  if (LANGUAGE_ALIAS[base]) return LANGUAGE_ALIAS[base]
  return base || lower
}

function languageLabel(code: string): string {
  return LANGUAGE_LABELS[code] || LANGUAGE_LABELS[code.split('_')[0]] || code
}

function displayLanguages(language: unknown, messages: any[] = []): string {
  const direct = displayValue(language, '')
  const raw = direct
    ? direct.split(/[,、]/).map(value => value.trim()).filter(Boolean)
    : messages.map(message => displayValue(message?.source_language, '')).filter(Boolean)
  const labels: string[] = []
  const seen = new Set<string>()
  for (const value of raw) {
    const code = normalizeLanguageCode(value)
    if (!code) continue
    const label = languageLabel(code)
    if (!label || seen.has(label)) continue
    seen.add(label)
    labels.push(label)
  }
  return labels.join('、') || '—'
}


function decisionLabel(value: unknown): string {
  const raw = displayValue(value, '')
  return raw ? (DECISION_LABELS[raw] || raw) : '—'
}

function assignmentStatusLabel(value: unknown): string {
  const raw = displayValue(value, '')
  return raw ? (ASSIGNMENT_STATUS_OPTIONS.find(option => option.value === raw)?.label || raw) : '—'
}

export default function Report() {
  const [searchParams, setSearchParams] = useSearchParams()
  const user = useAuthStore(s => s.user)
  const isAdmin = user?.role === 'admin'
  const [batches, setBatches] = useState<any[]>([])
  const [batchIds, setBatchIds] = useState<number[]>(() => parseBatchIds(searchParams.get('batch_id')))
  const [filters, setFilters] = useState<Record<string, string[]>>({})
  const [filterOptions, setFilterOptions] = useState<any>({})
  const [issues, setIssues] = useState<any[]>([])
  const [stats, setStats] = useState<any>({})
  const [loading, setLoading] = useState(false)
  const [detail, setDetail] = useState<any>(null)
  const [detailConversationOpen, setDetailConversationOpen] = useState(false)
  const [detailFocusedMessageId, setDetailFocusedMessageId] = useState<string | null>(null)
  const [genLoading, setGenLoading] = useState(false)
  const [deletingIssue, setDeletingIssue] = useState<number | null>(null)
  // KB Tab
  const [kbList, setKbList] = useState<any[]>([])
  const [kbLoading, setKbLoading] = useState(false)
  const [activeTab, setActiveTab] = useState('issues')
  const [search, setSearch] = useState('')
  const [priority, setPriority] = useState<string[]>([])
  const [issuePage, setIssuePage] = useState(1)
  const [kbPage, setKbPage] = useState(1)
  const [issuePageSize, setIssuePageSize] = useState(50)
  const [kbPageSize, setKbPageSize] = useState(50)
  const [issueTotal, setIssueTotal] = useState(0)
  const [kbTotal, setKbTotal] = useState(0)
  const [timeRange, setTimeRange] = useState<any>(null)
  const [assignmentStatus, setAssignmentStatus] = useState<string[]>([])
  const [assignmentScope, setAssignmentScope] = useState<string>('all')
  const [reviewerIds, setReviewerIds] = useState<number[]>([])
  const [decision, setDecision] = useState<string[]>(DEFAULT_KB_DECISIONS)
  const [kbActionableCount, setKbActionableCount] = useState(0)
  const [reviewers, setReviewers] = useState<any[]>([])
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([])
  const [assignOpen, setAssignOpen] = useState(false)
  const [assignReviewer, setAssignReviewer] = useState<number>()

  useEffect(() => {
    const requestedBatches = parseBatchIds(searchParams.get('batch_id'))
    if (requestedBatches.length) setBatchIds(requestedBatches)
    const requestedTab = searchParams.get('tab')
    if (requestedTab === 'issues' || requestedTab === 'kb') setActiveTab(requestedTab)
    // 报告页应展示真实创建的全部可查看批次。分析失败/进行中的批次
    // 也可能已经产出部分问题，不应因 status 过滤而从下拉框消失。
    analysisApi.listBatches().then(r => {
      const rows = (r.data || []).filter((b: any) => Number(b.processed_count || b.analyzed_slices || b.total_ai_msgs || 0) > 0)
      setBatches(rows)
      if (!requestedBatches.length) {
        const fallbackId = pickDefaultBatchId(rows)
        if (fallbackId) {
          setBatchIds([fallbackId])
          setSearchParams(prev => { const next = new URLSearchParams(prev); next.set('batch_id', String(fallbackId)); return next }, { replace: true })
        }
      }
    }).catch(() => { setBatches([]); message.error('加载可查看批次失败') })
  }, [searchParams])

  useEffect(() => {
    setAssignmentScope('all')
    setAssignmentStatus([])
    if (isAdmin) {
      reviewApi.listReviewers().then(r => setReviewers(r.data || [])).catch(() => setReviewers([]))
    } else {
      setReviewers([])
      setReviewerIds([])
    }
  }, [isAdmin])

  const batchQuery = reportBatchQuery(batchIds)
  const requestGate = useRef(createRequestGate())
  const fetchAbort = useRef<AbortController | null>(null)

  const ignoreCanceled = (error: any) => error?.code === 'ERR_CANCELED' || error?.name === 'CanceledError' || error?.name === 'AbortError'

  const currentTab = (activeTab === 'kb' ? 'kb' : 'issues') as ReportTab
  const csv = (values?: Array<string | number>) => values?.length ? values.join(',') : undefined
  const workbenchParams = () => ({
    assignment_scope: isAdmin ? assignmentScope : undefined,
    assignment_status: csv(assignmentStatus),
    batch_ids: batchQuery,
    item_type: workbenchItemType(currentTab),
    assignee_ids: isAdmin ? csv(reviewerIds) : undefined,
    issue_type: currentTab === 'issues' ? csv(filters.issue_type) : undefined,
    severity: currentTab === 'issues' ? csv(filters.severity) : undefined,
    priority: currentTab === 'issues' ? csv(priority) : undefined,
    decision: currentTab === 'kb' ? decision.join(',') : undefined,
    channel: csv(filters.channel),
    game: csv(filters.game),
    region: csv(filters.region),
    search: search.trim() || undefined,
    page: currentTab === 'issues' ? issuePage : kbPage,
    page_size: currentTab === 'issues' ? issuePageSize : kbPageSize,
  })

  const beginFetch = () => {
    if (!shouldFetchReportBatchData(batchIds) || !batchQuery) return null
    fetchAbort.current?.abort()
    const controller = new AbortController()
    fetchAbort.current = controller
    return { requestId: requestGate.current.nextId(), signal: controller.signal, controller }
  }

  const loadWorkbench = (requestId?: number, signal?: AbortSignal) => {
    if (!shouldFetchReportBatchData(batchIds) || !batchQuery) return
    const params = workbenchParams()
    const expectedKey = workbenchExpectedKey({
      batchQuery: String(params.batch_ids),
      itemType: String(params.item_type),
      page: Number(params.page),
      pageSize: Number(params.page_size),
    })
    const id = requestId ?? beginFetch()?.requestId
    if (id == null) return
    if (currentTab === 'issues') setLoading(true)
    else setKbLoading(true)
    reviewWorkbenchApi.listItems(params, { signal }).then(r => {
      if (!requestGate.current.shouldApply(id)) return
      const actualKey = workbenchExpectedKey({
        batchQuery: String(params.batch_ids),
        itemType: String(params.item_type),
        page: Number(params.page),
        pageSize: Number(params.page_size),
      })
      const data = r.data || {}
      const nextTotals = applyWorkbenchListTotal({
        expectedKey,
        actualKey,
        itemType: String(params.item_type),
        dataTotal: data.total,
        previous: { issues: issueTotal, kb: kbTotal },
      })
      if (!nextTotals) return
      const rows = Array.isArray(data.items) ? data.items : []
      if (params.item_type === 'quality_issue') {
        setIssues(rows.map(normalizeSliceResult).filter(hasQualityIssue))
        setIssueTotal(nextTotals.issues)
      } else {
        setKbList(rows)
        setKbTotal(nextTotals.kb)
      }
      if (typeof data.kb_actionable_count === 'number') setKbActionableCount(data.kb_actionable_count)
      setSelectedRowKeys([])
    }).catch((e: any) => {
      if (!requestGate.current.shouldApply(id) || ignoreCanceled(e)) return
      message.error(e?.response?.data?.detail || '加载审核工作台失败')
    }).finally(() => {
      if (!requestGate.current.shouldApply(id)) return
      setLoading(false)
      setKbLoading(false)
    })
  }

  useEffect(() => {
    if (!shouldFetchReportBatchData(batchIds) || !batchQuery) {
      fetchAbort.current?.abort()
      setIssues([])
      setKbList([])
      setStats({})
      setFilterOptions({})
      setKbActionableCount(0)
      setIssueTotal(0)
      setKbTotal(0)
      setSelectedRowKeys([])
      return
    }
    const req = beginFetch()
    if (!req) return
    reportApi.getFilterOptions({ batch_ids: batchQuery }, { signal: req.signal }).then(r => {
      if (requestGate.current.shouldApply(req.requestId)) setFilterOptions(r.data)
    }).catch(error => {
      if (requestGate.current.shouldApply(req.requestId) && !ignoreCanceled(error)) setFilterOptions({})
    })
    reportApi.getStats({ batch_ids: batchQuery }, { signal: req.signal }).then(r => {
      if (requestGate.current.shouldApply(req.requestId)) setStats(r.data || {})
    }).catch(error => {
      if (requestGate.current.shouldApply(req.requestId) && !ignoreCanceled(error)) setStats({})
    })
    loadWorkbench(req.requestId, req.signal)
    return () => { req.controller.abort() }
  }, [batchQuery, activeTab, assignmentScope, assignmentStatus, issuePage, kbPage, issuePageSize, kbPageSize, reviewerIds, decision, priority])

  const loadData = () => {
    const req = beginFetch()
    if (!req) return
    loadWorkbench(req.requestId, req.signal)
  }

  const loadKb = () => {
    const req = beginFetch()
    if (!req) return
    loadWorkbench(req.requestId, req.signal)
  }

  const handleDeleteIssue = async (issueId: number) => {
    setDeletingIssue(issueId)
    try {
      await reportApi.deleteIssue(issueId)
      message.success('已删除误判')
      setIssues(prev => prev.filter(i => i.id !== issueId))
      setStats((s: any) => ({ ...s, total_issues: (s.total_issues || 1) - 1 }))
    } catch (e: any) {
      message.error('删除失败')
    } finally {
      setDeletingIssue(null)
    }
  }

  const activeRows = activeTab === 'issues' ? issues : kbList
  const selectedRows = activeRows.filter(row => selectedRowKeys.includes(row.id))

  const assignSelected = async () => {
    if (!selectedRows.length) {
      message.warning('请先选择要分派的案例')
      return
    }
    if (!assignReviewer) {
      message.warning('请选择审核员')
      return
    }
    try {
      await reviewApi.assign({
        assignee_id: assignReviewer,
        strategy: 'manual',
        items: selectedRows.map(row => ({ item_type: activeTab === 'issues' ? 'quality_issue' : 'knowledge_suggestion', item_id: row.item_id || row.id })),
      })
      message.success(`已分派 ${selectedRows.length} 条案例`)
      setAssignOpen(false)
      setSelectedRowKeys([])
      loadData()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '分派失败')
    }
  }

  const claimReview = async (row: any) => {
    try {
      const result = await reviewApi.claim(activeTab === 'issues' ? 'quality_issue' : 'knowledge_suggestion', Number(row.item_id || row.id))
      const update = applyAssignment(row, result.data)
      if (activeTab === 'issues') setDetail(update); else setKbDetail(update)
      message.success('已认领并锁定该问题')
      loadData()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '认领失败')
    }
  }

  const canEdit = (row: any) => {
    const writable = ['pending', 'in_progress', 'returned'].includes(row?.assignment_status)
    return Boolean(row?.assignment_id) && writable && row?.assignee_id === user?.id
  }

  const releaseReview = async (row: any) => {
    if (!row?.assignment_id) return
    try {
      await reviewApi.release(row.assignment_id)
      message.success('已释放处理锁')
      if (activeTab === 'issues') setDetail(null); else setKbDetail(null)
      loadData()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '释放失败')
    }
  }

  const forceReleaseReview = async (row: any) => {
    if (!row?.assignment_id) return
    try {
      await reviewApi.forceRelease(row.assignment_id)
      message.success('已强制释放处理锁')
      if (activeTab === 'issues') setDetail(null); else setKbDetail(null)
      loadData()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '强制释放失败')
    }
  }

  const generateReport = async () => {
    setGenLoading(true)
    try {
      if (batchIds.length !== 1) { message.warning('生成 HTML 报告请只选一个批次'); return }
      const res = await reportApi.generateReport({ batch_id: batchIds[0], ...filters })
      const id = res.data.report_id
      message.success('报告已生成')
      const html = await reportApi.fetchReportHtml(id)
      const url = URL.createObjectURL(new Blob([html.data], { type: 'text/html' }))
      window.open(url, '_blank', 'noopener,noreferrer')
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
    } catch {
      message.error('报告操作失败，请重新登录或联系管理员')
    } finally {
      setGenLoading(false)
    }
  }

  const openIssueDetail = (row: any) => {
    setDetail(row)
    setDetailConversationOpen(false)
    setDetailFocusedMessageId(null)
    setSearchParams(prev => { const next = new URLSearchParams(prev); next.set('tab', 'issues'); if (batchIds.length) next.set('batch_id', batchIds.join(',')); return next }, { replace: true })
    requestAnimationFrame(() => {
      document.querySelector<HTMLElement>('.qc-issue-detail-modal .qc-detail-modal-body')?.scrollTo({ top: 0 })
    })
  }

  const navigateIssue = (direction: 'previous' | 'next') => {
    if (direction === 'previous') {
      if (hasPreviousIssue) return openIssueDetail(issues[detailIndex - 1])
      message.info({ content: '已是第一条', duration: 2 })
      return
    }
    if (hasNextIssue) return openIssueDetail(issues[detailIndex + 1])
    message.info({ content: '已是最后一条', duration: 2 })
  }

  const focusDetailMessage = (messageId: string) => {
    setDetailConversationOpen(true)
    setDetailFocusedMessageId(messageId)
  }

  useEffect(() => {
    if (!detail) return
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      if (target?.closest('input, textarea, button, .ant-select, [contenteditable="true"]')) return
      if (event.key === 'ArrowLeft') { event.preventDefault(); navigateIssue('previous') }
      if (event.key === 'ArrowRight') { event.preventDefault(); navigateIssue('next') }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [detail])
  const columns = [
    { title: '会话时间', dataIndex: 'session_time', width: 168, ellipsis: true, render: (value: string) => <Tooltip title={displayValue(value)}><span style={{ whiteSpace: 'nowrap' }}>{formatDisplayTime(value)}</span></Tooltip> },
    { title: '优先级', dataIndex: 'priority', width: 80, defaultSortOrder: 'ascend' as const, sorter: (a: any, b: any) => ['P0','P1','P2','P3'].indexOf(a.priority || 'P2') - ['P0','P1','P2','P3'].indexOf(b.priority || 'P2'), render: (v: string) => <Tag color={({P0:'red',P1:'orange',P2:'blue',P3:'default'} as Record<string,string>)[v]}>{v || 'P2'}</Tag> },
    {
      title: '严重程度', dataIndex: 'severity', width: 100,
      render: (v: string) => <Tag color={SEV_COLOR[v]}>{displayValue(v)}</Tag>,
    },
    { title: '问题类型', dataIndex: 'issue_type', width: 160, render: (v: string) => displayValue(v) },
    { title: '渠道', dataIndex: 'channel', width: 96, render: (v: string) => <span style={{ whiteSpace: 'nowrap' }}>{displayValue(v)}</span> },
    { title: '游戏', dataIndex: 'game', width: 100, render: (v: string) => displayValue(v) },
    { title: '地区', dataIndex: 'region', width: 90, render: (v: string) => displayValue(v) },
    { title: '置信度', dataIndex: 'confidence', width: 92, render: (value: number) => typeof value === 'number' ? `${Math.round(value * 100)}%` : '—' },
    { title: '切片ID', dataIndex: 'slice_id', width: 160, ellipsis: true, render: (value: string, row: any) => displayValue(value || row.session_uid) },
    { title: '批次', dataIndex: 'batch_id', width: 140, ellipsis: true, render: (value: number) => displayValue(batches.find(item => item.id === value)?.name, String(value ?? '')) },
    { title: '审核状态', dataIndex: 'assignment_status', width: 100, render: (v: string) => <Tag color={v === 'completed' ? 'success' : v === 'in_progress' ? 'processing' : v === 'returned' ? 'warning' : v === 'unassigned' ? 'default' : 'blue'}>{assignmentStatusLabel(v)}</Tag> },
    { title: '审核员', dataIndex: 'assignee', width: 100, render: (v: string) => displayValue(v) },
    { title: '最后修改时间', dataIndex: 'last_modified_at', width: 168, ellipsis: true, render: (value: string) => <Tooltip title={displayValue(value)}><span style={{ whiteSpace: 'nowrap' }}>{formatDisplayTime(value)}</span></Tooltip> },
    {
      title: '操作', width: 120,
      render: (_: any, row: any) => (
        <Space>
          <Button type="link" size="small" icon={<EyeOutlined />} onClick={() => openIssueDetail(row)}>详情</Button>
          {!row.slice_id && <Popconfirm
            title="确认删除此条问题？"
            description="该问题将被标记为误判并移除，不可恢复。"
            onConfirm={() => handleDeleteIssue(row.id)}
            okText="确认" cancelText="取消" okButtonProps={{ danger: true }}
          >
            <Tooltip title="标记为误判并删除">
              <Button
                type="link" size="small" danger
                icon={<DeleteOutlined />}
                loading={deletingIssue === row.id}
              />
            </Tooltip>
          </Popconfirm>}
        </Space>
      ),
    },
  ]

  // KB 详情
  const [kbDetail, setKbDetail] = useState<any>(null)
  const [kbConversationOpen, setKbConversationOpen] = useState(false)
  const [kbFocusedMessageId, setKbFocusedMessageId] = useState<string | null>(null)

  const kbColumns = [
    { title: '会话时间', dataIndex: 'session_time', width: 148, ellipsis: true, render: (value: string) => <Tooltip title={displayValue(value)}><span className="qc-table-nowrap">{formatDisplayTime(value)}</span></Tooltip> },
    {
      title: '渠道', dataIndex: 'channel', width: 96,
      render: (v: string) => <Tag color="geekblue" style={{ whiteSpace: 'nowrap' }}>{displayValue(v)}</Tag>,
    },
    { title: '游戏', dataIndex: 'game', width: 110, ellipsis: { showTitle: false }, render: (v: string) => <Tooltip title={displayValue(v)}><span className="qc-table-ellipsis">{displayValue(v)}</span></Tooltip> },
    { title: '地区', dataIndex: 'region', width: 90, ellipsis: { showTitle: false }, render: (v: string) => <Tooltip title={displayValue(v)}><span className="qc-table-ellipsis">{displayValue(v)}</span></Tooltip> },
    {
      title: '知识分类', dataIndex: 'category', width: 110,
      render: (v: string) => {
        const display = displayValue(v)
        const label = display.length > 12 ? display.slice(0, 12) + '…' : display
        return (
          <Tooltip title={display}>
            <Tag color={display !== '—' ? 'purple' : 'default'} style={{ fontWeight: 600, cursor: 'default' }}>
              {label}
            </Tag>
          </Tooltip>
        )
      },
    },
    { title: '候选决策', dataIndex: 'decision', width: 110, render: (value: string) => <Tag color={value === 'candidate_ready' ? 'success' : value === 'manual_review' ? 'warning' : value === 'no_human_answer' ? 'default' : 'processing'}>{decisionLabel(value)}</Tag> },
    { title: '置信度', dataIndex: 'confidence', width: 80, render: (value: number) => typeof value === 'number' ? `${Math.round(value * 100)}%` : '—' },
    { title: '审核状态', dataIndex: 'assignment_status', width: 100, render: (v: string) => <Tag color={v === 'completed' ? 'success' : v === 'in_progress' ? 'processing' : v === 'returned' ? 'warning' : v === 'unassigned' ? 'default' : 'blue'}>{assignmentStatusLabel(v)}</Tag> },
    { title: '审核员', dataIndex: 'assignee', width: 88, render: (v: string) => displayValue(v) },
    { title: '最后修改时间', dataIndex: 'last_modified_at', width: 148, ellipsis: true, render: (value: string) => <Tooltip title={displayValue(value)}><span className="qc-table-nowrap">{formatDisplayTime(value)}</span></Tooltip> },
    {
      title: '建议标题', dataIndex: 'title', width: 180, ellipsis: true,
      render: (v: string) => <Text className="qc-table-ellipsis" ellipsis={{ tooltip: displayValue(v) }}>{displayValue(v)}</Text>,
    },
    {
      title: '标准答案', dataIndex: 'standard_answer', width: 160, ellipsis: true,
      render: (v: string) => <Text className="qc-table-ellipsis" ellipsis={{ tooltip: displayValue(v) }} style={{ color: '#166534' }}>{displayValue(v)}</Text>,
    },
    {
      title: '操作', width: 80, fixed: 'right' as const,
      render: (_: any, row: any) => (
        <Button type="link" size="small" icon={<EyeOutlined />} onClick={() => { setKbDetail(row); setKbConversationOpen(false); setKbFocusedMessageId(null); setSearchParams(prev => { const next = new URLSearchParams(prev); next.set('tab', 'kb'); if (batchIds.length) next.set('batch_id', batchIds.join(',')); return next }, { replace: true }) }}>详情</Button>
      ),
    },
  ]

  const tabItems = [
    {
      key: 'issues',
      label: (
        <span>
          <WarningOutlined />
          质检问题
          {stats.total_issues > 0 && (
            <Badge count={stats.total_issues} style={{ marginLeft: 6, backgroundColor: '#ef4444' }} />
          )}
        </span>
      ),
      children: (
        <Card>
          <Table className="review-table"
            rowKey="id" dataSource={issues} columns={columns}
            rowSelection={isAdmin ? { selectedRowKeys, onChange: setSelectedRowKeys } : undefined}
            loading={loading} scroll={{ x: 900 }}
            sortDirections={['ascend', 'descend']}
            pagination={{ current: issuePage, pageSize: issuePageSize, total: issueTotal, showSizeChanger: true, pageSizeOptions: ['20','50','100','200','500'], showTotal: n => `共 ${n} 条`, onChange: (p, ps) => { if (ps !== issuePageSize) { setIssuePageSize(ps); setIssuePage(1) } else setIssuePage(p) } }}
            size="small"
          />
        </Card>
      ),
    },
    {
      key: 'kb',
      label: (
        <span>
          <BookOutlined />
          知识库建议
          {kbActionableCount > 0 && (
            <Badge count={kbActionableCount} overflowCount={9999} style={{ marginLeft: 6, backgroundColor: '#16a34a' }} />
          )}
        </span>
      ),
      children: (
        <Card>
          <Table
            className="review-table qc-kb-table"
            rowKey="id" dataSource={kbList} columns={kbColumns}
            rowSelection={isAdmin ? { selectedRowKeys, onChange: setSelectedRowKeys, getCheckboxProps: (row: any) => ({ disabled: !POOL_KB_DECISIONS.includes(row.decision) }) } : undefined}
            loading={kbLoading} scroll={{ x: 1580 }}
            pagination={{ current: kbPage, pageSize: kbPageSize, total: kbTotal, showSizeChanger: true, pageSizeOptions: ['20','50','100','200','500'], showTotal: n => `共 ${n} 条`, onChange: (p, ps) => { if (ps !== kbPageSize) { setKbPageSize(ps); setKbPage(1) } else setKbPage(p) } }}
            size="small"
            rowClassName={() => 'kb-row'}
          />
        </Card>
      ),
    },
  ]

  const detailIssues = detail?.all_issues?.length ? detail.all_issues : (detail ? [detail] : [])
  const primaryDetailIssue = detailIssues[0] || detail
  const primaryPlayerQuestion = getIssueTextPair(primaryDetailIssue, 'player_question', detail?.messages || [])
  const primaryAiAnswer = getIssueTextPair(primaryDetailIssue, 'ai_answer', detail?.messages || [])
  const detailIndex = issues.findIndex(item => String(item.id) === String(detail?.id))
  const hasPreviousIssue = detailIndex > 0
  const hasNextIssue = detailIndex >= 0 && detailIndex < issues.length - 1
  const kbIndex = kbList.findIndex(item => String(item.id) === String(kbDetail?.id))
  const kbConversation = kbDetail ? conversationFromCandidate({
    ...kbDetail,
    messages: (kbDetail.messages || []).map((m: any) => ({
      ...m,
      reference_tags: Array.from(new Set([
        ...(m.reference_tags || []),
        ...(m.role === 'ai' ? ['上下文'] : []),
        ...((kbDetail.answer_message_ids || []).includes(m.message_id) ? ['A', '证据'] : []),
        ...((kbDetail.question_message_ids || []).includes(m.message_id) ? ['Q'] : []),
        ...((kbDetail.feedback_message_ids || []).includes(m.message_id) ? ['反馈'] : []),
      ])),
    })),
  }) : null
  const navigateKb = (direction: 'previous' | 'next') => {
    const targetIndex = direction === 'previous' ? kbIndex - 1 : kbIndex + 1
    if (targetIndex < 0) return message.info({ content: '已是第一条', duration: 2 })
    if (targetIndex >= kbList.length) return message.info({ content: '已是最后一条', duration: 2 })
    setKbDetail(kbList[targetIndex]); setKbConversationOpen(false); setKbFocusedMessageId(null)
    requestAnimationFrame(() => document.querySelector<HTMLElement>('.qc-kb-detail-modal .qc-detail-modal-body')?.scrollTo({ top: 0 }))
  }

  return (
    <div className="review-workbench">
      <style>{`
        .review-workbench .ant-card { border-color: #dbe4f0; box-shadow: 0 2px 12px rgba(30, 64, 175, .04); }
        .review-workbench .filter-label { display: block; margin-bottom: 5px; color: #334155; font-size: 12px; font-weight: 600; }
        .review-workbench .review-table .ant-table-thead > tr > th { background: #f3f6fb; color: #27364d; font-size: 12px; font-weight: 600; }
        .review-workbench .review-table .ant-table-tbody > tr:nth-child(even) > td { background: #f6f8fc; }
        .review-workbench .review-table .ant-table-tbody > tr:hover > td { background: #edf4ff !important; }
        .review-workbench .ant-tabs-nav { margin-bottom: 10px; }
         .qc-issue-drawer-layout { min-height: 100%; }
         .qc-issue-navigation { position: sticky; top: 0; z-index: 4; display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 24px; background: #fff; border-bottom: 1px solid #e5e7eb; box-shadow: 0 3px 8px rgba(15, 23, 42, .04); }
         .qc-issue-navigation .ant-btn:focus-visible, .qc-evidence-id:focus-visible { outline: 2px solid #2563eb; outline-offset: 2px; }
         .qc-issue-drawer-content { padding: 20px 24px 28px; }
         .qc-detail-descriptions .ant-descriptions-item-label { width: 112px; }
         @media (max-width: 768px) { .qc-issue-drawer .ant-drawer-content-wrapper { width: 100vw !important; max-width: 100vw; } .qc-issue-navigation { padding: 10px 16px; } .qc-issue-drawer-content { padding: 16px; } }}
      `}</style>
      <Card className="qc-filter-card" style={{ marginBottom: 14 }} bodyStyle={{ padding: 18 }}>
        <div className="qc-filter-header">
          <Text type="secondary">{batchIds.length ? `当前批次：${batchIds.map(id => displayValue(batches.find(b => b.id === id)?.name, String(id))).join('、')}` : '请选择批次'}</Text>
        </div>
        <Space className="qc-filter-controls" wrap size={[8, 10]}>
          <RangePicker value={timeRange} onChange={setTimeRange} placeholder={['开始日期', '结束日期']} />
          <MultiFilterSelect placeholder="选择分析批次" style={{ minWidth: 240 }} value={batchIds} onChange={v => { setBatchIds(v); setFilters({}); setIssuePage(1); setKbPage(1); setSelectedRowKeys([]); setSearchParams(prev => { const next = new URLSearchParams(prev); if (v?.length) next.set('batch_id', v.join(',')); else next.delete('batch_id'); return next }, { replace: true }) }}
            options={batches.map(b => ({ value: b.id, label: b.name }))} />
          <MultiFilterSelect placeholder="选择严重程度" style={{ minWidth: 140 }}
            value={filters.severity} onChange={v => setFilters(f => ({ ...f, severity: v }))}
            options={(filterOptions.severities || []).map((item: string) => ({ value: item, label: item }))} />
          <MultiFilterSelect placeholder="选择问题类型" style={{ minWidth: 180 }}
            value={filters.issue_type} onChange={v => setFilters(f => ({ ...f, issue_type: v }))}
            options={Array.from(new Set([...(filterOptions.issue_types || []), ...ISSUE_CATEGORIES])).map((item: string) => ({ value: item, label: item }))} />
          <MultiFilterSelect placeholder="选择渠道" style={{ minWidth: 120 }}
            value={filters.channel} onChange={v => setFilters(f => ({ ...f, channel: v }))}
            options={(filterOptions.channels || []).map((item: string) => ({ value: item, label: item }))} />
          <MultiFilterSelect placeholder="选择游戏" style={{ minWidth: 150 }}
            value={filters.game} onChange={v => setFilters(f => ({ ...f, game: v }))}
            options={(filterOptions.games || []).map((item: string) => ({ value: item, label: item }))} />
          <MultiFilterSelect placeholder="选择地区" style={{ minWidth: 130 }}
            value={filters.region} onChange={v => setFilters(f => ({ ...f, region: v }))}
            options={(filterOptions.regions || []).map((item: string) => ({ value: item, label: item }))} />
          <MultiFilterSelect
            placeholder="选择审核状态" style={{ minWidth: 150 }}
            value={assignmentStatus}
            onChange={v => { setAssignmentStatus(v); setIssuePage(1); setKbPage(1) }}
            options={ASSIGNMENT_STATUS_OPTIONS.filter(option => option.value !== 'all')}
          />
          {isAdmin && <MultiFilterSelect
            placeholder="选择审核员" style={{ minWidth: 150 }} showSearch
            value={reviewerIds} onChange={v => { setReviewerIds(v); setIssuePage(1); setKbPage(1) }}
            options={reviewers.map((reviewer: any) => ({ value: reviewer.id, label: reviewer.username }))}
          />}
          {activeTab === 'kb' && <Select
            placeholder="知识建议状态" style={{ minWidth: 220 }} allowClear mode="multiple"
            maxTagCount="responsive"
            value={decision}
            onChange={v => { setDecision(v?.length ? v : DEFAULT_KB_DECISIONS); setKbPage(1) }}
            options={KB_DECISION_OPTIONS}
          />}
          <MultiFilterSelect placeholder="选择优先级" style={{ minWidth: 120 }} value={priority} onChange={v => { setPriority(v); setIssuePage(1) }} options={['P0','P1','P2','P3'].map(item => ({value:item,label:item}))} />
          <Input.Search value={search} onChange={e => setSearch(e.target.value)} onSearch={loadData} placeholder="搜索切片ID、会话、产品或对话内容" style={{width:240}} allowClear />
          <Button icon={<FilterOutlined />} type="primary" onClick={loadData} loading={loading}>查询</Button>
          <Button onClick={() => { setFilters({}); setPriority([]); setReviewerIds([]); setDecision(DEFAULT_KB_DECISIONS); setSearch(''); setTimeRange(null); setAssignmentStatus([]); setIssuePage(1); setKbPage(1) }}>重置</Button>
          {isAdmin && <Button onClick={() => reportApi.exportExcel({ batch_ids: batchQuery, severity: csv(filters.severity), channel: csv(filters.channel), game: csv(filters.game), region: csv(filters.region) })} icon={<DownloadOutlined />}>
            导出Excel
          </Button>}
          {isAdmin && <Button type="default" onClick={generateReport} loading={genLoading}>
            生成HTML报告
          </Button>}
          {isAdmin && <Button type="primary" disabled={!selectedRows.length} onClick={() => setAssignOpen(true)}>
            分派选中（{selectedRows.length}）
          </Button>}
        </Space>
      </Card>

      {!batches.length && <Alert type="info" showIcon message="还没有可看的分析批次" description="先完成一次上传分析，或联系管理员检查批次状态。" style={{ marginBottom: 14 }} />}

      <Row gutter={12} style={{ marginBottom: 16 }}>
        {[
          { label: '问题总数', value: stats.total_issues, color: '#374151' },
          { label: '严重', value: stats.severe, color: '#dc2626' },
          { label: '中级', value: stats.medium, color: '#d97706' },
          { label: '一般', value: stats.general, color: '#2563eb' },
          { label: '需复核', value: stats.review, color: '#6b7280' },
          { label: '知识库建议', value: kbActionableCount, color: '#16a34a' },
        ].map(s => (
          <Col key={s.label} flex="1 1 145px">
            <Card className="qc-stat-card" size="small">
              <Statistic title={s.label} value={s.value ?? 0} valueStyle={{ color: s.color, fontSize: 20 }} />
            </Card>
          </Col>
        ))}
      </Row>

      <Tabs activeKey={activeTab} onChange={key => { setActiveTab(key); setSelectedRowKeys([]); setSearchParams(prev => { const next = new URLSearchParams(prev); next.set('tab', key); if (batchIds.length) next.set('batch_id', batchIds.join(',')); return next }, { replace: true }) }} items={tabItems} />

      <DetailModalLayout
        open={!!detail}
        title="质检问题详情"
        recordLabel={detail ? `切片 ${displayValue(detail.slice_id || detail.item_id)}` : undefined}
        reviewerLabel={detail ? displayValue(detail.assignee, '未分配') : undefined}
        navigationLabel="当前问题"
        index={detailIndex >= 0 ? detailIndex : 0}
        total={issues.length}
        onPrevious={() => navigateIssue('previous')}
        onNext={() => navigateIssue('next')}
        onClose={() => setDetail(null)}
        className="qc-issue-detail-modal"
      >
        {detail && (
          <div className="qc-issue-drawer-layout">
            <div className="qc-issue-drawer-two-column">
              <div className="qc-issue-drawer-review-panel">
                {canEdit(detail) ? <ReviewProcessingPanel
                  itemType="quality_issue"
                  itemId={Number(detail.item_id || detail.id)}
                  initial={detail}
                  defaultQualityQuestionZhCn={primaryPlayerQuestion.zhCn}
                  defaultQualityAnswerZhCn={String(primaryDetailIssue?.revised_reply_zh_cn || primaryDetailIssue?.revised_reply_cn || '').trim()}
                  onSaved={value => { setDetail((current: any) => ({ ...current, ...value, issue_type: value.human_issue_type, severity: value.human_severity })); setIssues(current => current.map(row => row.id === detail.id ? { ...row, ...value, issue_type: value.human_issue_type, severity: value.human_severity } : row)) }}
                /> : <Card size="small" title="审核处理"><Typography.Paragraph type="secondary">该问题当前仅可查看。</Typography.Paragraph>{detail.assignment_status === 'unassigned' ? <><Typography.Text type="secondary">{isAdmin ? '请先认领或分派后再审核。' : '请联系管理员分派，或自己认领。'}</Typography.Text><div style={{ marginTop: 8 }}><Button type="primary" onClick={() => claimReview(detail)}>认领并开始处理</Button></div></> : <Typography.Text type="secondary">{`已由 ${displayValue(detail.assignee)} 锁定。`}</Typography.Text>}</Card>}
              </div>
              <div className="qc-issue-drawer-content">
              <Space direction="vertical" size={16} style={{ width: '100%' }}>
                {detail.assignment_id && detail.assignment_status !== 'completed' && detail.assignment_status !== 'cancelled' && (
                  (!isAdmin && detail.assignee_id === user?.id) || isAdmin
                ) && <Space wrap>
                  {!isAdmin && detail.assignee_id === user?.id && <Button danger onClick={() => releaseReview(detail)}>释放认领</Button>}
                  {isAdmin && <Button danger onClick={() => forceReleaseReview(detail)}>强制释放锁</Button>}
                </Space>}
                <Descriptions className="qc-detail-descriptions qc-detail-summary" column={{ xs: 1, sm: 2 }} bordered size="small">
                  <Descriptions.Item label="优先级"><Select size="small" value={detail.priority || 'P2'} options={['P0','P1','P2','P3'].map(v => ({value:v,label:v}))} onChange={async v => { try { await reportApi.updateIssue(detail.id, { priority: v }); setDetail((d:any) => ({ ...d, priority: v })); setIssues(prev => prev.map(i => i.id === detail.id ? { ...i, priority: v } : i)); message.success('优先级已更新') } catch { message.error('更新失败') } }} /></Descriptions.Item>
                  <Descriptions.Item label="严重程度"><Tag color={SEV_COLOR[primaryDetailIssue?.severity]}>{displayValue(primaryDetailIssue?.severity)}</Tag></Descriptions.Item>
                  <Descriptions.Item label="问题类型">{displayValue(primaryDetailIssue?.issue_type)}</Descriptions.Item>
                  <Descriptions.Item label="置信度">{typeof primaryDetailIssue?.confidence === 'number' ? `${Math.round(primaryDetailIssue.confidence * 100)}%` : '—'}</Descriptions.Item>
                  <Descriptions.Item label="会话链接">{detail.session_link ? <a className="qc-session-link" href={detail.session_link} target="_blank" rel="noreferrer">打开会话 ↗</a> : '—'}</Descriptions.Item>
                  <Descriptions.Item label="渠道">{displayValue(detail.channel)}</Descriptions.Item>
                  <Descriptions.Item label="游戏">{displayValue(detail.game)}</Descriptions.Item>
                  <Descriptions.Item label="地区">{displayValue(detail.region)}</Descriptions.Item>
                  <Descriptions.Item label="语言">{displayLanguages(detail.language, detail.messages || [])}</Descriptions.Item>
                </Descriptions>
                {detail.assignment_id && <Descriptions className="qc-detail-descriptions qc-detail-aux" column={1} bordered size="small">
                  <Descriptions.Item label="审核状态"><Tag color={detail.assignment_status === 'completed' ? 'success' : detail.assignment_status === 'in_progress' ? 'processing' : detail.assignment_status === 'returned' ? 'warning' : 'blue'}>{assignmentStatusLabel(detail.assignment_status)}</Tag></Descriptions.Item>
                  <Descriptions.Item label="审核员">{displayValue(detail.assignee)}</Descriptions.Item>
                  <Descriptions.Item label="会话时间"><Tooltip title={displayValue(detail.session_time)}>{formatDisplayTime(detail.session_time)}</Tooltip></Descriptions.Item>
                  <Descriptions.Item label="最后修改时间"><Tooltip title={displayValue(detail.last_modified_at)}>{formatDisplayTime(detail.last_modified_at)}</Tooltip></Descriptions.Item>
                </Descriptions>}
                <HumanHandoffCard handoff={detail.human_handoff} onFocusMessage={focusDetailMessage} />
                <IssueAnalysisCards issues={detailIssues} messages={detail?.messages || []} onFocusMessage={focusDetailMessage} />
                <Collapse
                  activeKey={detailConversationOpen ? ['conversation'] : []}
                  onChange={keys => { setDetailConversationOpen((keys as string[]).includes('conversation')); if (!(keys as string[]).includes('conversation')) setDetailFocusedMessageId(null) }}
                  items={[{
                    key: 'conversation',
                    label: '完整对话',
                    children: <ConversationMessageList conversation={conversationFromIssue({ ...detail, messages: messagesWithEvidence(detail) })} focusMessageId={detailFocusedMessageId} onFocusHandled={() => setDetailFocusedMessageId(null)} emptyText="当前记录没有可展示的原始消息" />,
                  }]}
                />
              </Space>
              </div>
            </div>
          </div>
        )}
      </DetailModalLayout>

      {/* 知识库建议详情 */}
      <DetailModalLayout
        open={!!kbDetail}
        title={<Space><BookOutlined style={{ color: '#16a34a' }} /><span>知识库建议详情</span></Space>}
        recordLabel={kbDetail ? `切片 ${displayValue(kbDetail.slice_id || kbDetail.item_id)}` : undefined}
        reviewerLabel={kbDetail ? displayValue(kbDetail.assignee, '未分配') : undefined}
        navigationLabel="当前建议"
        index={kbIndex >= 0 ? kbIndex : 0}
        total={kbList.length}
        onPrevious={() => navigateKb('previous')}
        onNext={() => navigateKb('next')}
        onClose={() => { setKbDetail(null); setKbFocusedMessageId(null); setKbConversationOpen(false) }}
        className="qc-kb-detail-modal"
      >
        {kbDetail && (
          <div className="qc-issue-drawer-two-column">
          <div className="qc-issue-drawer-review-panel">
            {canEdit(kbDetail) ? <ReviewProcessingPanel
              itemType="knowledge_suggestion"
              itemId={Number(kbDetail.item_id || kbDetail.id)}
              initial={kbDetail}
              onSaved={value => setKbDetail((current: any) => ({ ...current, ...value, standard_questions: value.human_standard_questions, standard_answer: value.human_standard_answer, category: value.human_category, human_applicable_scope: value.human_applicable_scope }))}
            /> : <Card size="small" title="审核处理"><Typography.Paragraph type="secondary">该知识库建议当前仅可查看。</Typography.Paragraph>{['not_candidate', 'no_human_answer', 'reject'].includes(kbDetail.decision) ? <Typography.Text type="secondary">废案不可认领。</Typography.Text> : kbDetail.assignment_status === 'unassigned' ? <><Typography.Text type="secondary">{isAdmin ? '请先认领或分派后再审核。' : '请联系管理员分派，或自己认领。'}</Typography.Text><div style={{ marginTop: 8 }}><Button type="primary" onClick={() => claimReview(kbDetail)}>认领并开始处理</Button></div></> : <Typography.Text type="secondary">{`已由 ${displayValue(kbDetail.assignee)} 锁定。`}</Typography.Text>}</Card>}
          </div>
          <div className="qc-issue-drawer-content">
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
          {kbDetail.assignment_id && kbDetail.assignment_status !== 'completed' && kbDetail.assignment_status !== 'cancelled' && (
            (!isAdmin && kbDetail.assignee_id === user?.id) || isAdmin
          ) && <Space wrap>
            {!isAdmin && kbDetail.assignee_id === user?.id && <Button danger size="small" onClick={() => releaseReview(kbDetail)}>释放认领</Button>}
            {isAdmin && <Button danger size="small" onClick={() => forceReleaseReview(kbDetail)}>强制释放锁</Button>}
          </Space>}
          <Descriptions className="qc-detail-descriptions qc-detail-summary" column={{ xs: 1, sm: 2 }} bordered size="small">
            <Descriptions.Item label="候选决策"><Tag color={kbDetail.decision === 'candidate_ready' ? 'success' : kbDetail.decision === 'manual_review' ? 'warning' : kbDetail.decision === 'candidate_pending_feedback' ? 'gold' : 'purple'}>{decisionLabel(kbDetail.decision)}</Tag></Descriptions.Item>
            {kbDetail.decision === 'candidate_pending_feedback' ? <Descriptions.Item label="审核说明">尚无明确玩家正向反馈，须人工审核后才能录入。</Descriptions.Item> : null}
            <Descriptions.Item label="验证状态">{VALIDATION_STATUS_LABELS[kbDetail.validation_status] || displayValue(kbDetail.validation_status)}</Descriptions.Item>
            <Descriptions.Item label="置信度">{typeof kbDetail.confidence === 'number' ? `${Math.round(kbDetail.confidence * 100)}%` : '—'}</Descriptions.Item>
            <Descriptions.Item label="需要人工复核">{kbDetail.needs_manual_review ? <Tag color="warning">是</Tag> : <Tag>否</Tag>}</Descriptions.Item>
            <Descriptions.Item label="来源"><Tag color="green">人工客服</Tag></Descriptions.Item>
            <Descriptions.Item label="客服姓名">{displayHumanAgentName(kbDetail)}</Descriptions.Item>
            <Descriptions.Item label="审核状态">{assignmentStatusLabel(kbDetail.assignment_status)}</Descriptions.Item>
            <Descriptions.Item label="审核员">{displayValue(kbDetail.assignee)}</Descriptions.Item>
            <Descriptions.Item label="渠道"><Tag color="geekblue">{displayValue(kbDetail.channel)}</Tag></Descriptions.Item>
            <Descriptions.Item label="游戏">{displayValue(kbDetail.game)}</Descriptions.Item>
            <Descriptions.Item label="地区">{displayValue(kbDetail.region)}</Descriptions.Item>
            <Descriptions.Item label="语言">{displayLanguages(kbDetail.language, kbDetail.messages || [])}</Descriptions.Item>
            <Descriptions.Item label="会话时间"><Tooltip title={displayValue(kbDetail.session_time)}>{formatDisplayTime(kbDetail.session_time)}</Tooltip></Descriptions.Item>
            <Descriptions.Item label="最后修改时间"><Tooltip title={displayValue(kbDetail.last_modified_at)}>{formatDisplayTime(kbDetail.last_modified_at)}</Tooltip></Descriptions.Item>
          </Descriptions>
          {kbDetail.decision === 'no_human_answer' && <Card size="small" type="inner" title="无人工回复">当前切片不生成知识库建议。</Card>}
          <TermSuggestionsCard terms={kbDetail.term_suggestions} />
          <Collapse activeKey={kbConversationOpen ? ['conversation'] : []} onChange={keys => setKbConversationOpen((keys as string[]).includes('conversation'))} items={[{ key: 'conversation', label: '完整对话（AI 消息仅作上下文）', children: <ConversationMessageList conversation={kbConversation} focusMessageId={kbFocusedMessageId} onFocusHandled={() => setKbFocusedMessageId(null)} emptyText="当前记录没有可展示的原始消息" /> }]} />
          <Descriptions column={1} bordered size="small">
            <Descriptions.Item label="建议标题">{displayValue(kbDetail.title)}</Descriptions.Item>
            <Descriptions.Item label="标准问题">{Array.isArray(kbDetail.standard_questions) && kbDetail.standard_questions.length ? <Space direction="vertical" size={2}>{kbDetail.standard_questions.map((question: string, index: number) => <div key={index}>{question}</div>)}</Space> : displayValue(kbDetail.question)}</Descriptions.Item>
            <Descriptions.Item label="标准答案"><div className="qc-long-text">{displayValue(kbDetail.standard_answer || kbDetail.answer)}</div></Descriptions.Item>
            <Descriptions.Item label="适用范围"><Space wrap><span>渠道：{displayValue(kbDetail.channel)}</span><span>游戏：{displayValue(kbDetail.game)}</span><span>地区：{displayValue(kbDetail.region)}</span></Space></Descriptions.Item>
            <Descriptions.Item label="判断原因">{displayValue(kbDetail.reason)}</Descriptions.Item>
            <Descriptions.Item label="排除原因">{displayValue(kbDetail.reject_reason)}</Descriptions.Item>
            <Descriptions.Item label="人工复核原因">{displayValue(kbDetail.manual_review_reason)}</Descriptions.Item>
            <Descriptions.Item label="证据消息 ID">
              {Array.isArray(kbDetail.evidence_message_ids) && kbDetail.evidence_message_ids.length
                ? <Space size={[4, 4]} wrap>{kbDetail.evidence_message_ids.map((id: string) => <Button key={id} type="link" size="small" onClick={() => { setKbConversationOpen(true); setKbFocusedMessageId(id) }}>{id}</Button>)}</Space>
                : '—'}
            </Descriptions.Item>
          </Descriptions>
          </Space>
          </div>
          </div>
        )}
      </DetailModalLayout>

      <Modal
        title="分派审核任务"
        open={assignOpen}
        onCancel={() => setAssignOpen(false)}
        onOk={assignSelected}
        okText="确认分派"
        cancelText="取消"
      >
        <Text>已选择 {selectedRows.length} 条{activeTab === 'issues' ? '质检问题' : '知识库建议'}。</Text>
        <Select
          style={{ width: '100%', marginTop: 16 }}
          showSearch
          placeholder="选择审核员"
          value={assignReviewer}
          onChange={setAssignReviewer}
          options={reviewers.map((reviewer: any) => ({ value: reviewer.id, label: reviewer.username }))}
        />
      </Modal>

    </div>
  )
}
