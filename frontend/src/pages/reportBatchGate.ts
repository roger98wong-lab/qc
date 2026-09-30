export function parseBatchIds(raw: string | null | undefined): number[] {
  return String(raw || '')
    .split(',')
    .map(Number)
    .filter(id => Number.isFinite(id) && id > 0)
}

export function pickDefaultBatchId(rows: Array<{ id?: number; status?: string }>): number | undefined {
  const available = rows.filter(row => row.status !== 'failed')
  const fallback = available[0] || rows[0]
  const id = Number(fallback?.id)
  return Number.isFinite(id) && id > 0 ? id : undefined
}

export function shouldFetchReportBatchData(batchIds: number[]): boolean {
  return batchIds.length > 0
}

export function reportBatchQuery(batchIds: number[]): string | undefined {
  return batchIds.length ? batchIds.join(',') : undefined
}

export type ReportTab = 'issues' | 'kb'

export function workbenchItemType(tab: ReportTab): 'quality_issue' | 'knowledge_suggestion' {
  return tab === 'issues' ? 'quality_issue' : 'knowledge_suggestion'
}

export function workbenchExpectedKey(input: {
  batchQuery: string
  itemType: string
  page: number
  pageSize: number
}): string {
  return `${input.batchQuery}|${input.itemType}|${input.page}|${input.pageSize}`
}

export function applyWorkbenchListTotal(input: {
  expectedKey: string
  actualKey: string
  itemType: string
  dataTotal: unknown
  previous: { issues: number; kb: number }
}): { issues: number; kb: number } | null {
  if (input.expectedKey !== input.actualKey) return null
  const total = Number(input.dataTotal)
  if (!Number.isFinite(total) || total < 0) return null
  if (input.itemType === 'quality_issue') return { ...input.previous, issues: total }
  if (input.itemType === 'knowledge_suggestion') return { ...input.previous, kb: total }
  return null
}

export function paginationTotalForTab(tab: ReportTab, totals: { issues: number; kb: number }): number {
  return tab === 'issues' ? totals.issues : totals.kb
}

export function createRequestGate() {
  let seq = 0
  return {
    nextId() {
      seq += 1
      return seq
    },
    shouldApply(id: number) {
      return id === seq
    },
    get started() {
      return seq
    },
  }
}