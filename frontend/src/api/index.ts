import axios from 'axios'

const api = axios.create({ baseURL: '/api' })

api.interceptors.request.use(config => {
  const token = localStorage.getItem('token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

api.interceptors.response.use(
  r => r,
  err => {
    // A failed login also returns 401, but it is an authentication attempt,
    // not an expired session.  Redirecting here reloads /login immediately
    // and removes the error toast, making the button appear unresponsive.
    const requestUrl = String(err.config?.url || '')
    const isLoginRequest = requestUrl.includes('/auth/login')
    if (err.response?.status === 401 && !isLoginRequest) {
      localStorage.removeItem('token')
      localStorage.removeItem('user')
      window.location.href = '/login'
    }
    return Promise.reject(err)
  }
)

export default api

// ── 认证 ──────────────────────────────────────────────────────────────────────
export const authApi = {
  login: (username: string, password: string) =>
    api.post('/auth/login', new URLSearchParams({ username, password })),
  me: () => api.get('/auth/me'),
  changePassword: (old_password: string, new_password: string) =>
    api.post('/auth/change-password', { old_password, new_password }),
  listUsers: () => api.get('/auth/users'),
  createUser: (data: object) => api.post('/auth/users', data),
  toggleUser: (id: number) => api.patch(`/auth/users/${id}/toggle`),
  updateUser: (id: number, data: object) => api.patch(`/auth/users/${id}`, data),
  deleteUser: (id: number, data?: object) => api.delete(`/auth/users/${id}`, { data }),
  resetPassword: (id: number, new_password: string) =>
    api.post(`/auth/users/${id}/reset-password`, { new_password }),
}

// ── 分析 ──────────────────────────────────────────────────────────────────────
export const analysisApi = {
  createBatch: (name: string, files: File[]) => {
    const fd = new FormData()
    fd.append('name', name)
    files.forEach(f => fd.append('files', f))
    return api.post('/analysis/batch', fd)
  },
  startBatch: (batchId: number) => api.post(`/analysis/batch/${batchId}/start`),
  pauseBatch: (batchId: number) => api.post(`/analysis/batch/${batchId}/pause`),
  resumeBatch: (batchId: number) => api.post(`/analysis/batch/${batchId}/resume`),
  abortBatch: (batchId: number) => api.post(`/analysis/batch/${batchId}/abort`),
  initUploadBatch: (name: string, expected_file_count: number) =>
    api.post('/analysis/batch/init', { name, expected_file_count }),
  uploadBatchFile: (batchId: number, clientFileId: string, file: File, onUploadProgress?: (percent: number) => void) => {
    const fd = new FormData()
    fd.append('client_file_id', clientFileId)
    fd.append('file', file)
    return api.post(`/analysis/batch/${batchId}/files`, fd, {
      onUploadProgress: event => {
        if (event.total) onUploadProgress?.(Math.min(100, Math.round(event.loaded / event.total * 100)))
      },
    })
  },
  getUploadStatus: (batchId: number) => api.get(`/analysis/batch/${batchId}/upload-status`),
  finalizeUpload: (batchId: number) => api.post(`/analysis/batch/${batchId}/finalize-upload`),
  retryParse: (batchId: number, fileId: number) => api.post(`/analysis/batch/${batchId}/files/${fileId}/retry-parse`),
  listBatches: () => api.get('/analysis/batches'),
  deleteBatch: (batchId: number) => api.delete(`/analysis/batch/${batchId}`, { timeout: 180000 }),
}

// ── 报告 ──────────────────────────────────────────────────────────────────────
export const reportApi = {
  getIssues: (params: object) => api.get('/reports/issues', { params }),
  updateIssue: (id: number, data: object) => api.patch(`/reports/issues/${id}`, data),
  getStats: (params: object) => api.get('/reports/stats', { params }),
  getFilterOptions: (params: object) => api.get('/reports/filter-options', { params }),
  generateReport: (data: object) => api.post('/reports/generate', data),
  listReports: (params: object) => api.get('/reports/list', { params }),
  viewReport: (id: number) => `/api/reports/${id}/html`,
  fetchReportHtml: (id: number) => api.get(`/reports/${id}/html`, { responseType: 'blob' }),
  downloadReport: (id: number) => `/api/reports/${id}/download`,
  exportExcel: (params: object) => {
    const qs = new URLSearchParams(params as Record<string, string>).toString()
    window.open(`/api/reports/export-excel?${qs}`)
  },
  deleteIssue: (issueId: number) => api.delete(`/reports/issues/${issueId}`),
  getKbSuggestions: (params: object) => api.get('/reports/kb-suggestions', { params }),
  deleteKbSuggestion: (kbId: number) => api.delete(`/reports/kb-suggestions/${kbId}`),
  translateKb: (batchId: number) => api.post(`/reports/kb-suggestions/translate?batch_id=${batchId}`),
}

// ── 管理 ──────────────────────────────────────────────────────────────────────
export const adminApi = {
  getConfig: () => api.get('/admin/config'),
  updateConfig: (data: object) => api.put('/admin/config', data),
  restart: () => api.post('/admin/restart'),
  backup: () => api.post('/admin/backup'),
  listBackups: () => api.get('/admin/backups'),
  listMappings: (params?: object) => api.get('/admin/mappings', { params }),
  listMappingGaps: (batch_id: number) => api.get('/admin/mappings/gaps', { params: { batch_id } }),
  createMappingDrafts: (batch_id?: number, scope?: 'all') => api.post('/admin/mappings/drafts', scope === 'all' ? { scope: 'all' } : { batch_id }),
  getMappingPendingCount: () => api.get('/admin/mappings/pending-count'),
  getMappingOptions: () => api.get('/admin/mappings/options'),
  createMapping: (data: object) => api.post('/admin/mappings', data),
  updateMapping: (id: number, data: object) => api.patch(`/admin/mappings/${id}`, data),
  batchSetMappingEnabled: (ids: number[], enabled: boolean) => api.post('/admin/mappings/batch-enabled', { ids, enabled }),
  applyGameProductIds: (match_values?: string[]) => api.post('/admin/mappings/apply-game-product-ids', match_values ? { match_values } : {}),
  deleteMapping: (id: number) => api.delete(`/admin/mappings/${id}`),
  listGameAiConfigs: (params?: object) => api.get('/admin/game-ai-configs', { params }),
  getGameAiConfigOptions: () => api.get('/admin/game-ai-configs/options'),
  createGameAiConfig: (data: object) => api.post('/admin/game-ai-configs', data),
  updateGameAiConfig: (id: number, data: object) => api.put(`/admin/game-ai-configs/${id}`, data),
  deleteGameAiConfig: (id: number) => api.delete(`/admin/game-ai-configs/${id}`),
  listAuditSlices: (params?: object) => api.get('/admin/audit/slices', { params }),
  getAuditSlice: (id: number | string) => api.get(`/admin/audit/slices/${id}`),
  listAuditLogs: (params?: object) => api.get('/admin/audit-logs', { params }),
  getAuditLogOptions: () => api.get('/admin/audit-logs/options'),
  exportAuditLogs: (params?: object) => api.get('/admin/audit-logs/export', { params, responseType: 'blob' }),
  listDictionaries: (group?: string) => api.get('/admin/dictionaries', { params: group ? { group } : undefined }),
  createDictionary: (data: object) => api.post('/admin/dictionaries', data),
  updateDictionary: (id: number, data: object) => api.patch(`/admin/dictionaries/${id}`, data),
  disableDictionary: (id: number) => api.post(`/admin/dictionaries/${id}/disable`),
  deleteDictionary: (id: number) => api.delete(`/admin/dictionaries/${id}`),
}

export const dictionaryApi = {
  list: (group: 'knowledge_category' | 'knowledge_base' | 'issue_tag' | 'issue_type') => api.get('/dictionaries', { params: { group } }),
}

// Review assignment (管理员分派与质检员审核)
export const reviewApi = {
  listTasks: (params?: object) => api.get('/review-assignments', { params }),
  listMine: (params?: object) => api.get('/review-assignments/mine', { params }),
  listReviewers: () => api.get('/review-assignments/reviewers'),
  assign: (data: { items: Array<{ item_type: 'quality_issue' | 'knowledge_suggestion'; item_id: number }>; assignee_id?: number; strategy?: 'manual' | 'least_load' | 'round_robin' | 'random'; due_at?: string }) => api.post('/review-assignments/assign', data),
  startTask: (id: number) => api.post(`/review-assignments/${id}/start`),
  submitTask: (id: number, data: { decision: string; comment?: string }) => api.post(`/review-assignments/${id}/submit`, data),
  returnTask: (id: number, reason: string) => api.post(`/review-assignments/${id}/return`, { reason }),
  cancelTask: (id: number) => api.post(`/review-assignments/${id}/cancel`, {}),
  reassignTask: (id: number, assignee_id: number) => api.post(`/review-assignments/${id}/reassign`, { assignee_id }),
  claim: (item_type: 'quality_issue' | 'knowledge_suggestion', item_id: number) => api.post('/review-assignments/claim', { item_type, item_id }),
  release: (id: number) => api.post(`/review-assignments/${id}/release`),
  forceRelease: (id: number, reason?: string) => api.post(`/review-assignments/${id}/force-release`, { reason }),
}

// Unified read model used by the /report review workbench.
export const reviewWorkbenchApi = {
  listItems: (params?: Record<string, unknown>) => api.get('/review-workbench/items', { params }),
}

export const knowledgePoolApi = {
  list: (params?: Record<string, unknown>) => api.get('/knowledge-pool/items', { params }),
  updateStatus: (data: { ids: Array<string | number>; status: string; reason?: string }) => api.patch('/knowledge-pool/status', data),
  exportCsv: (ids: Array<string | number>) => api.post('/knowledge-pool/export.csv', { ids }, { responseType: 'blob' }),
  logs: (id: string | number) => api.get(`/knowledge-pool/${encodeURIComponent(String(id))}/logs`),
}

export const reviewProcessingApi = {
  get: (itemType: 'quality_issue' | 'knowledge_suggestion', itemId: number) => api.get(`/review-processing/${itemType}/${itemId}`),
  save: (itemType: 'quality_issue' | 'knowledge_suggestion', itemId: number, data: object) => api.patch(`/review-processing/${itemType}/${itemId}`, data),
  logs: (itemType: 'quality_issue' | 'knowledge_suggestion', itemId: number) => api.get(`/review-processing/${itemType}/${itemId}/logs`),
}

export const dashboardApi = {
  reviewWorkload: (params: { start_date: string; end_date: string; assignee_id?: number }) =>
    api.get('/dashboard/review-workload', { params }),
}
