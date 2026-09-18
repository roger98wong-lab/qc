export type BatchStatus =
  | 'uploading'
  | 'pending'
  | 'parsing'
  | 'analyzing'
  | 'paused'
  | 'completed'
  | 'partial'
  | 'failed'
  | 'done'
  | string

export const TERMINAL_BATCH_STATUSES = ['completed', 'partial', 'failed', 'done'] as const

export function isTerminalBatchStatus(status?: string | null) {
  return TERMINAL_BATCH_STATUSES.includes((status || '') as typeof TERMINAL_BATCH_STATUSES[number])
}

export function isIdleUploadStatus(status?: string | null) {
  return ['uploading', 'pending', 'parsing', ''].includes(status || '')
}

export interface BatchControlInput {
  status?: string | null
  pendingCount?: number
  processedCount?: number
  runnableCount?: number
}

export interface BatchControlState {
  status: string
  canUpload: boolean
  canStart: boolean
  canPause: boolean
  canResume: boolean
  canAbort: boolean
  canDelete: boolean
  canView: boolean
  primary: 'start' | 'pause' | 'resume' | 'view' | null
}

/** Shared enablement for upload-page buttons and history-page menus. */
export function getBatchControlState(input: BatchControlInput): BatchControlState {
  const status = input.status || 'uploading'
  const pendingCount = Number(input.pendingCount || 0)
  const processedCount = Number(input.processedCount || 0)
  const runnableCount = Number(input.runnableCount ?? pendingCount)
  const terminal = isTerminalBatchStatus(status)
  const analyzing = status === 'analyzing'
  const paused = status === 'paused'
  const idle = isIdleUploadStatus(status) && !terminal
  return {
    status,
    canUpload: (idle || paused) && !terminal,
    canStart: idle && runnableCount > 0,
    canPause: analyzing,
    canResume: paused && !terminal && runnableCount > 0,
    canAbort: !terminal,
    canDelete: !analyzing,
    canView: processedCount > 0,
    primary: analyzing
      ? 'pause'
      : paused && runnableCount > 0
        ? 'resume'
        : idle && runnableCount > 0
          ? 'start'
          : terminal && processedCount > 0
            ? 'view'
            : null,
  }
}

export const BATCH_STATUS_LABEL: Record<string, string> = {
  uploading: '未开始',
  pending: '未开始',
  parsing: '解析中',
  analyzing: '分析中',
  paused: '已暂停',
  completed: '已完成',
  partial: '部分完成',
  failed: '已失败',
  done: '已完成',
}

export const BATCH_STATUS_COLOR: Record<string, string> = {
  uploading: 'default',
  pending: 'default',
  parsing: 'processing',
  analyzing: 'processing',
  paused: 'warning',
  completed: 'success',
  partial: 'warning',
  failed: 'error',
  done: 'success',
}

export function batchStatusHint(status?: string | null, pendingCount = 0, processedCount = 0) {
  if (status === 'analyzing') return '正在分析中。已发出的请求会保存下来。分析中不能追加文件。'
  if (status === 'paused') return pendingCount
    ? '已暂停。可追加文件，新切片只增加待处理，点重启后才会开始分析。'
    : '队列已空，批次保持暂停。可追加文件或终止。'
  if (isTerminalBatchStatus(status)) return processedCount
    ? '批次已终止或已完成。已处理结果保留，不能追加、开始或重启。'
    : '批次已终止。未分析的切片不会再分析。'
  if (pendingCount > 0) return '切片已入库。点开始后才会开始分析。'
  return '选择 Excel 后并发上传；解析出的切片会计入待处理，不会自动开始分析。'
}
