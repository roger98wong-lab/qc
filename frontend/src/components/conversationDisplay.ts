export const TRANSFER_FORM_LABELS: Record<string, string> = {
  '1': '转人工表单-引导填写',
  transfer_form_started: '转人工表单-引导填写',
  '3': '转人工表单-已提交并生成工单',
  transfer_form_submitted: '转人工表单-已提交并生成工单',
}

const TRANSFER_PREFIXES = Object.values(TRANSFER_FORM_LABELS).filter((value, index, all) => all.indexOf(value) === index)

export function normalizeContentType(value: unknown): string {
  if (value == null) return ''
  const text = String(value).trim()
  if (!text) return ''
  const number = Number(text)
  if (number === 0 || number === 1 || number === 2 || number === 3) return String(number)
  return text
}

export function readableStructuredValue(value: unknown): string {
  if (value == null) return ''
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  if (typeof value === 'object') {
    try { return JSON.stringify(value) } catch { return String(value) }
  }
  return String(value)
}

export function structuredContentEntries(data: unknown): Array<{ key: string; value: string }> {
  if (!data || typeof data !== 'object' || Array.isArray(data)) return []
  return Object.entries(data as Record<string, unknown>).flatMap(([key, value]) => {
    const readable = readableStructuredValue(value)
    return readable ? [{ key, value: readable }] : []
  })
}

export function parseJsonValue(raw: unknown): unknown {
  if (raw && typeof raw === 'object') return raw
  if (typeof raw !== 'string') return null
  const text = raw.trim()
  if (!text || (text[0] !== '{' && text[0] !== '[')) return null
  try { return JSON.parse(text) } catch { return null }
}

export function formatRawContent(raw: unknown): string {
  if (raw == null) return ''
  if (typeof raw === 'object') {
    try { return JSON.stringify(raw, null, 2) } catch { return String(raw) }
  }
  const text = String(raw)
  const parsed = parseJsonValue(text)
  if (parsed && typeof parsed === 'object') {
    try { return JSON.stringify(parsed, null, 2) } catch { return text }
  }
  return text
}

export function stripTransferPrefix(text: string, label?: string | null): string {
  const source = String(text || '')
  const labels = label ? [label, ...TRANSFER_PREFIXES] : TRANSFER_PREFIXES
  for (const item of labels) {
    const wrapped = `[${item}]`
    if (source.startsWith(wrapped)) return source.slice(wrapped.length).trim()
  }
  return source
}

export function resolveTransferFormPresentation(message: any): {
  eventLabel: string | null
  contentType: string
  entries: Array<{ key: string; value: string }>
  displayText: string
  rawContent: string
  showRawToggle: boolean
} {
  const contentType = normalizeContentType(message?.content_type ?? message?.contentType)
  const eventType = String(message?.event_type || '').trim()
  const original = String(message?.original_text ?? message?.text ?? message?.content ?? '')
  const eventLabel = String(message?.event_label || '').trim()
    || TRANSFER_FORM_LABELS[contentType]
    || TRANSFER_FORM_LABELS[eventType]
    || TRANSFER_PREFIXES.find(label => original.startsWith(`[${label}]`))
    || null
  const structured = message?.structured_content ?? parseJsonValue(message?.raw_content) ?? parseJsonValue(stripTransferPrefix(original, eventLabel))
  const isTransferForm = Boolean(eventLabel) || contentType === '1' || contentType === '3'
  const stripped = isTransferForm ? stripTransferPrefix(original, eventLabel) : original
  const parsedBody = isTransferForm ? parseJsonValue(stripped) : null
  const bodyEntries = isTransferForm
    ? (structuredContentEntries(structured).length ? structuredContentEntries(structured) : structuredContentEntries(parsedBody))
    : []
  const displayText = isTransferForm && bodyEntries.length
    ? bodyEntries.map(item => `${item.key}：${item.value}`).join('\n')
    : (isTransferForm ? stripped : original)
  const rawContent = isTransferForm
    ? formatRawContent(message?.raw_content ?? (parsedBody ? stripped : ''))
    : ''
  return {
    eventLabel,
    contentType,
    entries: bodyEntries,
    displayText,
    rawContent,
    showRawToggle: isTransferForm && Boolean(rawContent || parsedBody || message?.raw_content),
  }
}
