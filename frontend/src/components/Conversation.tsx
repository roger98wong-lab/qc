import { useEffect, useMemo, useRef, useState } from 'react'
import { Button, Empty, Tag, Tooltip, Typography } from 'antd'
import { TranslationOutlined } from '@ant-design/icons'
import './Conversation.css'
import { resolveTransferFormPresentation } from './conversationDisplay'

export type ConversationRole = 'player' | 'ai' | 'human_agent' | 'system' | 'unknown'
export type TranslationStatus = 'pending' | 'processing' | 'success' | 'not_required' | 'uncertain' | 'failed'
export interface ConversationMessage {
  message_id: string
  sequence?: number
  speaker_source?: string | null
  /** Real name supplied by the source data for a human agent, if available. */
  agent_name?: string | null
  role: ConversationRole
  created_at?: string | null
  original_text: string
  source_language?: string | null
  translation?: { target_language: string; translated_text?: string | null; status: TranslationStatus; translation_version?: string | null } | null
  reference_tags?: string[]
  content_type?: string | null
  event_type?: string | null
  event_label?: string | null
  raw_content?: string | null
  structured_content?: Record<string, unknown> | unknown[] | null
}
export interface Conversation { conversation_id?: string | null; messages: ConversationMessage[] }

export const ROLE_LABEL: Record<ConversationRole, string> = { player: '玩家', ai: 'AI', human_agent: '人工客服', system: '系统', unknown: '未知说话人' }
const CONVERSATION_ROLES = new Set<ConversationRole>(['player', 'ai', 'human_agent', 'system', 'unknown'])
export function mapConversationRole(source?: string | null): ConversationRole {
  const value = (source || '').trim().toLowerCase()
  if (['player', 'user', 'customer', 'visitor', '用户', '玩家'].includes(value)) return 'player'
  if (['ai', 'assistant', 'bot', 'robot', '人工智能', '智能客服'].includes(value)) return 'ai'
  if (['human_agent', 'agent', '客服', '人工客服', 'operator'].includes(value) || value.includes('人工客服') || value.includes('客服回复') || value.includes('operator') || value.startsWith('客服-') || value.startsWith('客服：')) return 'human_agent'
  if (['system', '系统'].includes(value)) return 'system'
  return 'unknown'
}
function resolveConversationRole(message: any): ConversationRole {
  // Slice protocol uses `speaker`; legacy API rows use `role`. Both are
  // system-owned facts, so prefer them over trying to infer a role from a
  // display label such as “客服-张孙易易”.
  const protocolRole = message?.role || message?.speaker
  if (typeof protocolRole === 'string' && CONVERSATION_ROLES.has(protocolRole as ConversationRole)) {
    return protocolRole as ConversationRole
  }
  return mapConversationRole(message?.speaker_source ?? message?.speaker)
}
function agentNameFromSource(source?: string | null): string | null {
  const value = (source || '').trim()
  const match = value.match(/^(?:客服|人工客服|human_agent|agent|operator)\s*[-—–:：/]\s*(.+)$/i)
  return match?.[1]?.trim() || null
}
function resolvedAgentName(message: any, role: ConversationRole, record: any): string | null {
  if (role !== 'human_agent') return null
  return message?.agent_name?.trim() || message?.human_agent_name?.trim() || agentNameFromSource(message?.speaker_source) || record?.agent_name || record?.human_agent_name || null
}
function transferFormFields(raw: any) {
  return {
    content_type: raw?.content_type ?? raw?.contentType ?? null,
    event_type: raw?.event_type ?? null,
    event_label: raw?.event_label ?? null,
    raw_content: raw?.raw_content ?? null,
    structured_content: raw?.structured_content ?? null,
  }
}
export function sortConversationMessages(messages: ConversationMessage[]): ConversationMessage[] {
  return messages.map((message, index) => ({ ...message, sequence: message.sequence ?? index })).sort((a, b) => {
    const at = a.created_at ? Date.parse(a.created_at) : Number.POSITIVE_INFINITY
    const bt = b.created_at ? Date.parse(b.created_at) : Number.POSITIVE_INFINITY
    return at === bt ? (a.sequence || 0) - (b.sequence || 0) : at - bt
  })
}

/** Keep timestamps visually stable regardless of browser locale. */
export function formatConversationTime(value?: string | null): string {
  if (!value) return ''
  const source = String(value).trim()
  const localMatch = source.match(/^(\d{4}-\d{2}-\d{2})[T\s](\d{2}:\d{2}:\d{2})(?:\.\d+)?$/)
  // Source rows without an offset are already Beijing business time. Do not
  // route them through the browser timezone parser (which would shift them).
  if (localMatch) return `${localMatch[1]} ${localMatch[2]}`
  const date = new Date(source)
  if (Number.isNaN(date.getTime())) {
    const match = source.match(/^(\d{4}-\d{2}-\d{2})[T\s](\d{2}:\d{2}(?::\d{2})?)/)
    return match ? `${match[1]} ${match[2]}` : source
  }
  const pad = (number: number) => String(number).padStart(2, '0')
  return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' }).format(date).replace(/\//g, '-')
}
const isChinese = (text: string) => Array.from(text || '').filter(c => /[\u4e00-\u9fff]/.test(c)).length >= Math.max(2, Math.floor((text || '').length * 0.1))
export function makeTranslation(original: string, translated?: string | null, status?: TranslationStatus, sourceLanguage?: string | null) {
  const resolved: TranslationStatus = status || (translated && translated.trim() !== original.trim() ? 'success' : (sourceLanguage === 'Chinese' || sourceLanguage === '中文' || isChinese(original) ? 'not_required' : 'pending'))
  return { target_language: 'zh-CN', translated_text: translated || null, status: resolved }
}
function normalizeTranslation(raw: any, original: string, fallbackLanguage?: string | null) {
  if (raw && typeof raw === 'object') {
    return makeTranslation(
      original,
      raw.translated_text ?? raw.zh_cn ?? null,
      raw.status,
      raw.source_language ?? fallbackLanguage,
    )
  }
  return makeTranslation(original, null, undefined, fallbackLanguage)
}
function contextMessage(line: string, index: number, prefix: string): ConversationMessage {
  const match = line.match(/^\s*\[([^\]]+)\](?:\[([^\]]+)\])?:?\s*(.*)$/)
  const text = match?.[3] || line.trim()
  return { message_id: `${prefix}-context-${index}`, sequence: index, speaker_source: match?.[1] || null, role: mapConversationRole(match?.[1] === '用户' ? 'player' : match?.[1]), created_at: match?.[2] || null, original_text: text, translation: makeTranslation(text), reference_tags: ['上下文'] }
}
export function conversationFromIssue(issue: any): Conversation {
  const prefix = `issue-${issue?.id ?? issue?.session_uid ?? 'unknown'}`
  if (Array.isArray(issue?.messages) && issue.messages.length) {
    return { conversation_id: issue?.session_uid || null, messages: sortConversationMessages(issue.messages.map((message: any, index: number) => {
      const role = resolveConversationRole(message)
      return {
      message_id: String(message.message_id || `${prefix}-message-${index}`), sequence: message.sequence ?? index,
      speaker_source: message.speaker_source ?? message.speaker ?? null,
      agent_name: resolvedAgentName(message, role, issue),
      role,
      created_at: message.created_at ?? null, original_text: String(message.original_text || message.text || ''),
      source_language: message.source_language ?? issue.language ?? null, translation: normalizeTranslation(message.translation, String(message.original_text || message.text || ''), message.source_language || issue.language),
      reference_tags: message.reference_tags || [],
      ...transferFormFields(message),
      }
    })) }
  }
  if (Array.isArray(issue?.messages)) {
    const messages = issue.messages.map((raw: any, index: number) => {
      const original = String(raw.original_text ?? raw.content ?? raw.text ?? '')
      const role = resolveConversationRole(raw)
      return {
        message_id: String(raw.message_id ?? `${prefix}-message-${index}`), sequence: raw.sequence ?? index,
        speaker_source: raw.speaker_source ?? raw.speaker ?? null, agent_name: raw.agent_name ?? (role === 'human_agent' ? issue.agent_name ?? issue.human_agent_name ?? null : null), role, created_at: raw.created_at ?? raw.msg_time ?? null,
        original_text: original, source_language: raw.source_language ?? issue.language ?? null,
        translation: normalizeTranslation(raw.translation, original, raw.source_language ?? issue.language),
        reference_tags: raw.reference_tags,
        ...transferFormFields(raw),
      } as ConversationMessage
    })
    return { conversation_id: issue?.session_uid || issue?.conversation_id || null, messages: sortConversationMessages(messages) }
  }
  let messages: ConversationMessage[] = []
  // New backend rows persist the compact slice JSON in context. Parse it as
  // messages instead of rendering the whole JSON blob as one “unknown” bubble.
  if (typeof issue?.context === 'string' && issue.context.trim()) {
    try {
      const slice = JSON.parse(issue.context)
      // New slice rows persist messages_json directly as an array. Older
      // records may persist the whole { messages: [...] } payload instead.
      const rawMessages = Array.isArray(slice) ? slice : slice?.messages
      if (Array.isArray(rawMessages)) {
        messages = sortConversationMessages(rawMessages.map((raw: any, index: number) => {
          const original = String(raw.original_text ?? raw.text ?? raw.content ?? '')
          const role = resolveConversationRole(raw)
          return {
            message_id: String(raw.message_id ?? `${prefix}-context-${index}`),
            sequence: raw.sequence ?? index, speaker_source: raw.speaker_source ?? raw.speaker ?? null,
            agent_name: raw.agent_name ?? (role === 'human_agent' ? issue.agent_name ?? issue.human_agent_name ?? null : null),
            role, created_at: raw.created_at ?? null, original_text: original,
            source_language: raw.source_language ?? issue.language ?? null,
            translation: normalizeTranslation(raw.translation, original, raw.source_language ?? issue.language),
            reference_tags: raw.reference_tags || [],
            ...transferFormFields(raw),
          } as ConversationMessage
        }))
      }
    } catch { /* legacy line-oriented context handled below */ }
    if (!messages.length) {
      messages = issue.context.split(/\r?\n/).filter(Boolean).map((line: string, index: number) => contextMessage(line, index, prefix))
    }
  }
  // 单切片协议在没有原始 messages 时通过 quality_check 提供问答文本。
  const hasSliceMessages = messages.length > 0
  const questionOriginal = issue?.player_question_original ?? issue?.question_original ?? issue?.quality_check?.player_question?.original
  const questionZh = issue?.player_question_zh_cn ?? issue?.question_zh_cn ?? issue?.quality_check?.player_question?.zh_cn
  if (!hasSliceMessages && questionOriginal) messages.push({ message_id: `${prefix}-question`, sequence: messages.length, speaker_source: 'player', role: 'player', original_text: String(questionOriginal), source_language: issue.language || null, created_at: issue.question_time || null, translation: makeTranslation(String(questionOriginal), questionZh, issue.question_translation_status, issue.language), reference_tags: ['Q', '证据消息'] })
  const answerOriginal = issue?.ai_sentence_orig ?? issue?.quality_check?.ai_answer?.original
  const answerZh = issue?.ai_sentence_cn ?? issue?.quality_check?.ai_answer?.zh_cn
  if (!hasSliceMessages && answerOriginal) messages.push({ message_id: `${prefix}-ai`, sequence: messages.length, speaker_source: 'ai', role: 'ai', original_text: String(answerOriginal), source_language: issue.language || null, created_at: issue.reply_time || null, translation: makeTranslation(String(answerOriginal), answerZh, issue.translation_status, issue.language), reference_tags: ['A', '证据消息'] })
  if (!hasSliceMessages && issue?.revised_reply) messages.push({ message_id: `${prefix}-agent`, sequence: messages.length, speaker_source: 'human_agent', role: 'human_agent', original_text: String(issue.revised_reply), source_language: issue.language || null, translation: makeTranslation(String(issue.revised_reply), issue.revised_reply_cn, issue.revised_translation_status, issue.language), reference_tags: ['证据消息'] })
  return { conversation_id: issue?.session_uid || null, messages: sortConversationMessages(messages) }
}
export function conversationFromCandidate(candidate: any): Conversation {
  const prefix = `candidate-${candidate?.id ?? 'unknown'}`
  if (Array.isArray(candidate?.messages) && candidate.messages.length) {
    return { conversation_id: candidate?.session_uid || null, messages: sortConversationMessages(candidate.messages.map((message: any, index: number) => {
      const role = resolveConversationRole(message)
      return {
      message_id: String(message.message_id || `${prefix}-message-${index}`), sequence: message.sequence ?? index,
      speaker_source: message.speaker_source ?? message.speaker ?? null,
      agent_name: resolvedAgentName(message, role, candidate),
      role,
      created_at: message.created_at ?? null, original_text: String(message.original_text || message.text || ''),
      source_language: message.source_language ?? candidate.language ?? null, translation: normalizeTranslation(message.translation, String(message.original_text || message.text || ''), message.source_language || candidate.language),
      reference_tags: [
        ...(message.reference_tags || []),
        ...(candidate.question_message_ids?.includes(message.message_id) ? ['Q'] : []),
        ...(candidate.answer_message_ids?.includes(message.message_id) ? ['A'] : []),
        ...(candidate.evidence_message_ids?.includes(message.message_id) ? ['证据消息'] : []),
      ].filter((tag, tagIndex, tags) => tags.indexOf(tag) === tagIndex),
      ...transferFormFields(message),
      }
    })) }
  }
  if (Array.isArray(candidate?.messages)) {
    const messages = candidate.messages.map((raw: any, index: number) => {
      const original = String(raw.original_text ?? raw.content ?? raw.text ?? '')
      const role = resolveConversationRole(raw)
      return {
        message_id: String(raw.message_id ?? `${prefix}-message-${index}`), sequence: raw.sequence ?? index,
        speaker_source: raw.speaker_source ?? raw.speaker ?? null, agent_name: resolvedAgentName(raw, role, candidate), role, created_at: raw.created_at ?? raw.msg_time ?? null,
        original_text: original, source_language: raw.source_language ?? candidate.language ?? null,
        translation: normalizeTranslation(raw.translation, original, raw.source_language ?? candidate.language),
        reference_tags: raw.reference_tags,
        ...transferFormFields(raw),
      } as ConversationMessage
    })
    return { conversation_id: candidate?.session_uid || candidate?.conversation_id || null, messages: sortConversationMessages(messages) }
  }
  const messages: ConversationMessage[] = []
  if (candidate?.question) messages.push({ message_id: `${prefix}-question`, sequence: 0, speaker_source: 'player', role: 'player', original_text: String(candidate.question), source_language: candidate.language || null, translation: makeTranslation(String(candidate.question), candidate.question_cn, candidate.question_translation_status, candidate.language), reference_tags: ['Q', '证据消息'] })
  if (candidate?.answer) messages.push({ message_id: `${prefix}-answer`, sequence: 1, speaker_source: 'human_agent', role: 'human_agent', original_text: String(candidate.answer), source_language: candidate.language || null, translation: makeTranslation(String(candidate.answer), candidate.answer_cn, candidate.answer_translation_status, candidate.language), reference_tags: ['A', '证据消息'] })
  if (candidate?.satisfied_expr) messages.push({ message_id: `${prefix}-feedback`, sequence: 2, speaker_source: 'player', role: 'player', original_text: String(candidate.satisfied_expr), source_language: candidate.language || null, translation: makeTranslation(String(candidate.satisfied_expr), candidate.satisfied_expr_cn, candidate.feedback_translation_status, candidate.language), reference_tags: ['玩家反馈'] })
  return { conversation_id: candidate?.session_uid || null, messages: sortConversationMessages(messages) }
}

const ROLE_CLASS: Record<ConversationRole, string> = { player: 'player', ai: 'ai', human_agent: 'human-agent', system: 'system', unknown: 'unknown' }
export function ConversationMessageList({ conversation, loading = false, error, emptyText = '暂无对话消息', focusMessageId, onFocusHandled }: { conversation?: Conversation | null; loading?: boolean; error?: string; emptyText?: string; focusMessageId?: string | null; onFocusHandled?: () => void }) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [highlighted, setHighlighted] = useState<string | null>(null)
  const [missingReference, setMissingReference] = useState(false)
  const refs = useRef<Record<string, HTMLDivElement | null>>({})
  const messages = useMemo(() => sortConversationMessages(conversation?.messages || []), [conversation])
  const focusMessage = (id: string) => { refs.current[id]?.scrollIntoView({ behavior: 'smooth', block: 'center' }); setHighlighted(id); window.setTimeout(() => setHighlighted(current => current === id ? null : current), 2200) }
  useEffect(() => {
    if (!focusMessageId) return
    if (!refs.current[focusMessageId]) {
      setMissingReference(true)
      const timer = window.setTimeout(() => setMissingReference(false), 2600)
      onFocusHandled?.()
      return () => window.clearTimeout(timer)
    }
    focusMessage(focusMessageId)
    onFocusHandled?.()
  }, [focusMessageId, messages.length])
  if (loading) return <div className="qc-conversation-state">正在加载对话…</div>
  if (error) return <div className="qc-conversation-state qc-conversation-error">{error}</div>
  if (!messages.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={emptyText} />
  return <div className="qc-conversation-list" aria-label="统一对话窗口">
    {missingReference ? <div className="qc-conversation-reference-missing" role="status">未找到引用的消息</div> : null}
    {messages.map(message => {
      const translation = message.translation
      const showToggle = !!translation && translation.status !== 'not_required' && !!translation.translated_text
      const defaultTranslationOpen = translation?.status === 'success' || translation?.status === 'uncertain'
      const showTranslation = expanded[message.message_id] ?? defaultTranslationOpen
      const humanAgentName = message.role === 'human_agent' && message.agent_name?.trim()
        ? message.agent_name.trim()
        : null
      const form = resolveTransferFormPresentation(message)
      const rawExpanded = expanded[`${message.message_id}-raw`] === true
      return <div key={message.message_id} ref={node => { refs.current[message.message_id] = node }} className={`qc-conversation-row qc-role-${ROLE_CLASS[message.role]} ${highlighted === message.message_id ? 'is-highlighted' : ''}`}>
        <div className="qc-conversation-bubble">
          <div className="qc-conversation-meta">
            <span className="qc-conversation-identity"><Tag className="qc-role-tag">{ROLE_LABEL[message.role]}</Tag>{form.eventLabel ? <Tag className="qc-event-tag">{form.eventLabel}</Tag> : null}{humanAgentName ? <Typography.Text className="qc-human-agent-name">{humanAgentName}</Typography.Text> : null}</span>
            {message.created_at && <Typography.Text type="secondary">{formatConversationTime(message.created_at)}</Typography.Text>}
            {message.reference_tags?.length ? <span className="qc-message-reference-tags">{message.reference_tags.map(tag => <Tag key={tag} className="qc-message-ref">{tag}</Tag>)}</span> : null}
          </div>
          {form.entries.length
            ? <dl className="qc-form-fields">{form.entries.map(entry => <div key={entry.key} className="qc-form-field"><dt>{entry.key}</dt><dd>{entry.value}</dd></div>)}</dl>
            : <div className="qc-message-original">{form.displayText || message.original_text || '—'}</div>}
          {form.showRawToggle ? <Button type="link" size="small" aria-expanded={rawExpanded} onClick={() => setExpanded(state => ({ ...state, [`${message.message_id}-raw`]: !rawExpanded }))}>{rawExpanded ? '收起原始内容' : '查看原始内容'}</Button> : null}
          {form.showRawToggle && rawExpanded ? <pre className="qc-message-raw">{form.rawContent || '—'}</pre> : null}
          {translation?.status === 'pending' || translation?.status === 'processing' ? <div className="qc-translation-note">翻译处理中</div> : null}
          {translation?.status === 'failed' ? <div className="qc-translation-note">翻译暂不可用</div> : null}
          {translation?.status === 'uncertain' && showTranslation ? <div className="qc-message-translation"><span>中译（可能不准确）：</span>{translation.translated_text}</div> : null}
          {translation?.status === 'success' && showTranslation ? <div className="qc-message-translation">{translation.translated_text}</div> : null}
          {showToggle && <Button type="link" size="small" icon={<TranslationOutlined />} aria-expanded={showTranslation} onClick={() => setExpanded(state => ({ ...state, [message.message_id]: !showTranslation }))}>{showTranslation ? '收起中文翻译' : '展开中文翻译'}</Button>}
          {translation?.status === 'failed' && !translation.translated_text ? <Tooltip title="原文已保留，翻译服务暂不可用"><span className="qc-translation-failed">译文不可用</span></Tooltip> : null}
        </div>
      </div>
    })}
  </div>
}
