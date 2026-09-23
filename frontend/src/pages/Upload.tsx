import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import type { UploadFile } from 'antd'
import {
  Alert, Button, Card, Input, List, Modal, Space, Statistic, Tag, Typography,
  Upload as AntUpload, message,
} from 'antd'
import {
  InboxOutlined, LinkOutlined, PauseCircleOutlined, PlayCircleOutlined,
  ReloadOutlined, StopOutlined,
} from '@ant-design/icons'
import { analysisApi, adminApi } from '../api'
import {
  BATCH_STATUS_COLOR, BATCH_STATUS_LABEL, batchStatusHint, getBatchControlState,
  isTerminalBatchStatus,
} from '../batchControl'

const { Title, Text } = Typography
const { Dragger } = AntUpload
const ACTIVE_UPLOAD_KEY = 'qc.activeUploadBatchId.v1'
const UPLOAD_CONCURRENCY = 3

type FileStage =
  | 'pending' | 'uploading' | 'uploaded' | 'parsing' | 'parsed'
  | 'upload_failed' | 'parse_failed'

interface UploadItem {
  clientId: string
  filename: string
  size: number
  file?: File
  status: FileStage
  error?: string
  serverFileId?: number
}

interface ServerUploadFile {
  file_upload_id: number
  client_file_id?: string
  filename: string
  file_size: number
  status: FileStage
  error?: string
  parse_error?: string
  upload_error?: string
}

const STAGE_LABEL: Record<FileStage, string> = {
  pending: '待上传', uploading: '上传中', uploaded: '已上传', parsing: '解析中',
  parsed: '成功', upload_failed: '失败', parse_failed: '失败',
}

const STAGE_COLOR: Record<FileStage, string> = {
  pending: 'default', uploading: 'processing', uploaded: 'blue', parsing: 'processing',
  parsed: 'success', upload_failed: 'error', parse_failed: 'error',
}

const makeClientId = () => {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID()
  return `file-${Date.now()}-${Math.random().toString(36).slice(2)}`
}

const errorText = (error: any, fallback: string) => {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  return error?.message || fallback
}

const mapCounts = (data: any) => ({
  processed: Number(data?.processed_count || 0),
  pending: Number(data?.pending_count || 0),
  runnable: Number(data?.runnable_count ?? data?.pending_count ?? 0),
})

const sameCounts = (
  current: { processed: number; pending: number; runnable: number },
  next: { processed: number; pending: number; runnable: number },
) => current.processed === next.processed && current.pending === next.pending && current.runnable === next.runnable

const sameUploadItem = (current: UploadItem | undefined, next: UploadItem) => {
  if (!current) return false
  return current.filename === next.filename
    && current.size === next.size
    && current.status === next.status
    && current.error === next.error
    && current.serverFileId === next.serverFileId
}

export default function Upload() {
  const navigate = useNavigate()
  const [batchName, setBatchName] = useState('')
  const [pickerFiles, setPickerFiles] = useState<UploadFile[]>([])
  const [items, setItems] = useState<UploadItem[]>([])
  const [batchId, setBatchId] = useState<number | null>(null)
  const [batchStatus, setBatchStatus] = useState('uploading')
  const [counts, setCounts] = useState({ processed: 0, pending: 0, runnable: 0 })
  const [submitting, setSubmitting] = useState(false)
  const [actionLoading, setActionLoading] = useState<string | null>(null)
  const [restored, setRestored] = useState(false)
  const [mappingGaps, setMappingGaps] = useState<any[]>([])
  const [gapLoading, setGapLoading] = useState(false)
  const [draftLoading, setDraftLoading] = useState(false)
  const eventSourceRef = useRef<EventSource | null>(null)
  const submitLockRef = useRef(false)
  const mappingGapsRef = useRef<any[]>([])
  const gapBatchIdRef = useRef<number | null>(null)
  const gapFetchedRef = useRef(false)

  const control = useMemo(
    () => getBatchControlState({
      status: batchStatus,
      pendingCount: counts.pending,
      processedCount: counts.processed,
      runnableCount: counts.runnable,
    }),
    [batchStatus, counts],
  )

  const patchItem = useCallback((clientId: string, patch: Partial<UploadItem>) => {
    setItems(current => current.map(item => item.clientId === clientId ? { ...item, ...patch } : item))
  }, [])

  const mergeServerFiles = useCallback((files: ServerUploadFile[]) => {
    setItems(current => {
      const byClient = new Map(current.map(item => [item.clientId, item]))
      let changed = false
      for (const server of files) {
        const clientId = server.client_file_id || `server-${server.file_upload_id}`
        const previous = byClient.get(clientId)
        const next: UploadItem = {
          ...previous,
          clientId,
          filename: server.filename,
          size: server.file_size || previous?.size || 0,
          status: server.status || 'parsed',
          error: server.error || server.upload_error || server.parse_error || undefined,
          serverFileId: server.file_upload_id,
        }
        if (sameUploadItem(previous, next)) continue
        changed = true
        byClient.set(clientId, next)
      }
      if (!changed) return current
      return Array.from(byClient.values())
    })
  }, [])

  const applyServerBatch = useCallback((data: any) => {
    if (!data) return
    if (data.batch_id) setBatchId(current => current === data.batch_id ? current : data.batch_id)
    if (data.name) setBatchName(current => current === data.name ? current : data.name)
    if (data.status) setBatchStatus(current => current === data.status ? current : data.status)
    const nextCounts = mapCounts(data)
    setCounts(current => sameCounts(current, nextCounts) ? current : nextCounts)
    if (Array.isArray(data.files)) mergeServerFiles(data.files)
  }, [mergeServerFiles])

  const refreshUploadStatus = useCallback(async (id: number, silent = false) => {
    try {
      const response = await analysisApi.getUploadStatus(id)
      applyServerBatch(response.data)
      return response.data
    } catch (error) {
      if (!silent) message.error(errorText(error, '无法恢复批次状态'))
      return null
    }
  }, [applyServerBatch])

  useEffect(() => {
    const raw = localStorage.getItem(ACTIVE_UPLOAD_KEY)
    if (!raw) return
    let saved = 0
    try {
      const persisted = JSON.parse(raw)
      saved = Number(persisted.batchId)
      if (Array.isArray(persisted.items)) {
        setItems(persisted.items.map((item: UploadItem) =>
          ['pending', 'uploading', 'uploaded'].includes(item.status)
            ? { ...item, status: 'upload_failed' as FileStage, error: '页面已刷新，浏览器未保留原文件；请重新选择' }
            : item
        ))
      }
    } catch {
      saved = Number(raw)
    }
    if (!Number.isInteger(saved) || saved <= 0) return
    setRestored(true)
    void refreshUploadStatus(saved)
  }, [refreshUploadStatus])

  useEffect(() => {
    if (!batchId || isTerminalBatchStatus(batchStatus)) return
    const persistedItems = items.map(({ file: _file, ...item }) => item)
    localStorage.setItem(ACTIVE_UPLOAD_KEY, JSON.stringify({ version: 1, batchId, items: persistedItems }))
  }, [batchId, batchStatus, items])

  useEffect(() => {
    if (!batchId) return
    const parsing = items.some(item => item.status === 'parsing')
    const interval = batchStatus === 'analyzing' || parsing ? 1500 : 4000
    const timer = window.setInterval(() => void refreshUploadStatus(batchId, true), interval)
    return () => window.clearInterval(timer)
  }, [batchId, batchStatus, items, refreshUploadStatus])

  mappingGapsRef.current = mappingGaps

  useEffect(() => {
    if (!batchId) {
      gapBatchIdRef.current = null
      gapFetchedRef.current = false
      setMappingGaps([])
      setGapLoading(false)
      return
    }
    let active = true
    const batchChanged = gapBatchIdRef.current !== batchId
    if (batchChanged) {
      gapBatchIdRef.current = batchId
      gapFetchedRef.current = false
      setMappingGaps([])
      setGapLoading(true)
    } else if (!gapFetchedRef.current) {
      setGapLoading(true)
    }
    adminApi.listMappingGaps(batchId).then(response => {
      if (!active) return
      gapFetchedRef.current = true
      setMappingGaps(response.data?.items || [])
    }).catch(() => {
      if (!active) return
      gapFetchedRef.current = true
    }).finally(() => { if (active) setGapLoading(false) })
    return () => { active = false }
  }, [batchId, counts.pending, counts.processed])

  const createDraftsAndOpen = async () => {
    if (!batchId || draftLoading) return
    setDraftLoading(true)
    try {
      const result = await adminApi.createMappingDrafts(batchId)
      const created = Number(result.data?.created || 0)
      const skipped = Number(result.data?.skipped || 0)
      message.success(created ? `已生成 ${created} 条待补映射` : `没有新草稿（已存在 ${skipped} 条）`)
      navigate('/admin?tab=mappings&pending=1')
    } catch (error: any) {
      message.error(errorText(error, '生成待补映射失败'))
    } finally {
      setDraftLoading(false)
    }
  }

  useEffect(() => () => eventSourceRef.current?.close(), [])

  const connectProgress = useCallback((id: number) => {
    eventSourceRef.current?.close()
    const token = localStorage.getItem('token')
    const source = new EventSource(`/api/analysis/batch/${id}/progress?token=${encodeURIComponent(token || '')}`)
    eventSourceRef.current = source
    source.onmessage = event => {
      const next = JSON.parse(event.data)
      setBatchStatus(next.status || 'analyzing')
      setCounts(mapCounts(next))
      if (isTerminalBatchStatus(next.status)) {
        source.close()
        localStorage.removeItem(ACTIVE_UPLOAD_KEY)
      }
    }
    source.onerror = () => source.close()
  }, [])

  const uploadOne = useCallback(async (id: number, item: UploadItem) => {
    if (!item.file) {
      patchItem(item.clientId, { status: 'upload_failed', error: '刷新后浏览器无法保留本地文件，请重新选择该文件' })
      return false
    }
    patchItem(item.clientId, { status: 'uploading', error: undefined })
    try {
      const response = await analysisApi.uploadBatchFile(id, item.clientId, item.file)
      const server = response.data as ServerUploadFile
      patchItem(item.clientId, {
        status: server.status || 'parsing',
        error: server.error || server.parse_error || undefined,
        serverFileId: server.file_upload_id,
      })
      return true
    } catch (error) {
      const status = await refreshUploadStatus(id, true)
      const server = (status?.files || []).find((row: ServerUploadFile) => row.client_file_id === item.clientId)
      if (server) {
        mergeServerFiles([server])
        return true
      }
      patchItem(item.clientId, { status: 'upload_failed', error: errorText(error, '文件上传失败') })
      return false
    }
  }, [mergeServerFiles, patchItem, refreshUploadStatus])

  const runPool = async <T,>(jobs: Array<() => Promise<T>>, limit: number) => {
    const executing = new Set<Promise<void>>()
    for (const job of jobs) {
      const task = job().then(() => { executing.delete(task) })
      executing.add(task)
      if (executing.size >= limit) await Promise.race(executing)
    }
    await Promise.all(executing)
  }

  const ensureBatch = async (extraFiles: number) => {
    if (batchId) return batchId
    const initialized = await analysisApi.initUploadBatch(batchName, Math.max(extraFiles, 1))
    const id = initialized.data.batch_id as number
    applyServerBatch(initialized.data)
    localStorage.setItem(ACTIVE_UPLOAD_KEY, JSON.stringify({ version: 1, batchId: id, items: [] }))
    return id
  }

  const enqueueSelected = async () => {
    if (submitLockRef.current) return
    const selected = pickerFiles
      .map(upload => upload.originFileObj as File | undefined)
      .filter((file): file is File => Boolean(file))
    if (!selected.length) {
      message.warning('请先选择至少一个 Excel 文件')
      return
    }
    if (!control.canUpload) {
      message.warning('当前状态不能上传，请暂停后再追加，或新建批次')
      return
    }
    submitLockRef.current = true
    setSubmitting(true)
    const localItems: UploadItem[] = selected.map(file => ({
      clientId: makeClientId(), filename: file.name, size: file.size, file, status: 'pending',
    }))
    setItems(current => [...current, ...localItems])
    setPickerFiles([])
    try {
      const id = await ensureBatch(localItems.length)
      await runPool(localItems.map(item => () => uploadOne(id, item)), UPLOAD_CONCURRENCY)
      await refreshUploadStatus(id, true)
    } catch (error) {
      message.error(errorText(error, '无法上传文件'))
    } finally {
      submitLockRef.current = false
      setSubmitting(false)
    }
  }

  const retryItem = async (item: UploadItem) => {
    if (!batchId || !control.canUpload) return
    if (item.status === 'upload_failed') {
      await uploadOne(batchId, item)
      return
    }
    if (item.status !== 'parse_failed' || !item.serverFileId) return
    patchItem(item.clientId, { status: 'parsing', error: undefined })
    try {
      const response = await analysisApi.retryParse(batchId, item.serverFileId)
      mergeServerFiles([response.data])
    } catch (error) {
      patchItem(item.clientId, { status: 'parse_failed', error: errorText(error, '重试解析失败') })
    }
  }

  const runControl = async (action: 'start' | 'pause' | 'resume' | 'abort') => {
    if (!batchId) {
      if (action === 'abort') {
        resetPage()
        return
      }
      message.warning('请先上传文件')
      return
    }
    setActionLoading(action)
    try {
      const api = action === 'start'
        ? analysisApi.startBatch
        : action === 'pause'
          ? analysisApi.pauseBatch
          : action === 'resume'
            ? analysisApi.resumeBatch
            : analysisApi.abortBatch
      const response = await api(batchId)
      applyServerBatch(response.data)
      if (action === 'start' || action === 'resume') connectProgress(batchId)
      if (action === 'abort') localStorage.removeItem(ACTIVE_UPLOAD_KEY)
    } catch (error) {
      message.error(errorText(error, '任务控制失败'))
      await refreshUploadStatus(batchId, true)
    } finally {
      setActionLoading(null)
    }
  }

  const confirmAbort = () => {
    Modal.confirm({
      title: '确认终止该批次？',
      content: '未分析的切片不会再分析，已出结果保留。之后不能追加、不能重启、不能再开始。',
      okText: '终止',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: () => runControl('abort'),
    })
  }

  const resetPage = () => {
    eventSourceRef.current?.close()
    localStorage.removeItem(ACTIVE_UPLOAD_KEY)
    setBatchId(null)
    setBatchName('')
    setPickerFiles([])
    setItems([])
    setRestored(false)
    setBatchStatus('uploading')
    setCounts({ processed: 0, pending: 0, runnable: 0 })
  }

  const hint = batchStatusHint(batchStatus, counts.pending, counts.processed)
  const uploadDisabled = !control.canUpload || submitting || actionLoading === 'start'

  return (
    <div className="qc-upload-page">
      <Title level={4} className="page-heading">上传文件</Title>
      <Text className="qc-upload-intro">先上传，再点开始。分析不会自动跑。</Text>

      <Card className="qc-upload-card" style={{ marginTop: 16 }}>
        {restored && batchId ? (
          <Alert showIcon type="info" style={{ marginBottom: 16 }} message={`已恢复批次 ${batchId}`} description="刷新不会停止服务端解析或分析；未传完的本地文件需重新选择。" />
        ) : null}
        {!gapLoading && mappingGaps.length > 0 ? (
          <Alert
            showIcon
            type="warning"
            style={{ marginBottom: 16 }}
            message="有未配置的映射，分析仍可开始，但游戏/地区可能不对"
            description={
              <Space direction="vertical" size={8} style={{ width: '100%' }}>
                <div>
                  {mappingGaps.slice(0, 8).map((gap: any) => (
                    <div key={`${gap.kind}-${gap.match_value || ''}-${gap.raw_channel || ''}-${gap.game || ''}-${gap.raw_region || ''}`}>
                      {gap.kind === 'gameProductId'
                        ? `游戏产品 ID ${gap.match_value}（${gap.sample_count} 条切片）`
                        : `${gap.raw_region || '空地区'} / ${gap.raw_channel} / ${gap.game}（${gap.sample_count} 条）`}
                    </div>
                  ))}
                  {mappingGaps.length > 8 ? <Text type="secondary">还有 {mappingGaps.length - 8} 项</Text> : null}
                </div>
                <Button type="primary" loading={draftLoading} onClick={() => void createDraftsAndOpen()}>生成待补映射并去配置</Button>
              </Space>
            }
          />
        ) : null}

        <div className="qc-upload-toolbar">
          <Input
            placeholder="分析批次名称（可选，默认按日期命名）"
            value={batchName}
            onChange={event => setBatchName(event.target.value)}
            disabled={Boolean(batchId)}
            style={{ maxWidth: 420 }}
          />
          <Tag color={BATCH_STATUS_COLOR[batchStatus] || 'default'}>{BATCH_STATUS_LABEL[batchStatus] || batchStatus}</Tag>
        </div>

        <div className="qc-upload-counts" aria-live="polite">
          <div className="qc-upload-count-card">
            <Statistic title="已处理" value={counts.processed} />
          </div>
          <div className="qc-upload-count-card">
            <Statistic title="待处理" value={counts.pending} />
          </div>
        </div>
        <Text type="secondary">{hint}</Text>

        <div className="qc-upload-actions">
          <Button
            type={control.primary === 'start' ? 'primary' : 'default'}
            icon={<PlayCircleOutlined />}
            disabled={!control.canStart || !batchId}
            loading={actionLoading === 'start'}
            onClick={() => void runControl('start')}
          >开始</Button>
          <Button
            type={control.primary === 'pause' ? 'primary' : 'default'}
            icon={<PauseCircleOutlined />}
            disabled={!control.canPause}
            loading={actionLoading === 'pause'}
            onClick={() => void runControl('pause')}
          >暂停</Button>
          <Button
            type={control.primary === 'resume' ? 'primary' : 'default'}
            icon={<ReloadOutlined />}
            disabled={!control.canResume}
            loading={actionLoading === 'resume'}
            onClick={() => void runControl('resume')}
          >重启</Button>
          <Button
            danger
            icon={<StopOutlined />}
            disabled={!control.canAbort && Boolean(batchId)}
            loading={actionLoading === 'abort'}
            onClick={confirmAbort}
          >终止</Button>
          {isTerminalBatchStatus(batchStatus) ? (
            <Button type="primary" onClick={resetPage}>新建批次</Button>
          ) : null}
        </div>

        <Dragger
          accept=".xlsx,.xls" multiple fileList={pickerFiles}
          disabled={uploadDisabled}
          onChange={({ fileList }) => setPickerFiles(fileList)}
          beforeUpload={() => false}
          style={{ marginTop: 20 }}
        >
          <p><InboxOutlined style={{ fontSize: 48, color: uploadDisabled ? '#94a3b8' : '#1d4ed8' }} /></p>
          <p style={{ fontSize: 16, fontWeight: 500 }}>点击或拖拽上传 Excel 文件</p>
          <Text type="secondary">支持 .xlsx / .xls；一次可选多个，3 路并发上传。分析中不能追加。</Text>
        </Dragger>
        <Space wrap style={{ marginTop: 16 }}>
          <Button type="primary" icon={<PlayCircleOutlined />} onClick={() => void enqueueSelected()} loading={submitting} disabled={uploadDisabled || !pickerFiles.length}>
            {batchId ? '追加到当前批次' : '上传到新批次'}
          </Button>
          <Button
            className="qc-external-link qc-external-link--overseas"
            icon={<LinkOutlined />}
            href="https://app.neko33.icu/overseas_tools/auto_collector"
            target="_blank"
            rel="noopener noreferrer"
          >
            前往下载海外客诉切片
          </Button>
          {batchId ? <Button onClick={() => void refreshUploadStatus(batchId)}>刷新</Button> : null}
          {control.canView ? <Button onClick={() => navigate(`/report?batch_id=${batchId || ''}`)}>查看报告</Button> : null}
        </Space>

        {items.length > 0 ? (
          <List
            className="qc-upload-file-list"
            bordered size="small" style={{ marginTop: 16 }} dataSource={items}
            header={<Text type="secondary">文件列表仅作辅助，成功/失败以服务端为准</Text>}
            renderItem={item => (
              <List.Item
                actions={(item.status === 'parse_failed' || (item.status === 'upload_failed' && item.file)) && control.canUpload ? [
                  <Button key="retry" size="small" icon={<ReloadOutlined />} onClick={() => void retryItem(item)}>
                    {item.status === 'parse_failed' ? '重试解析' : '重试上传'}
                  </Button>,
                ] : undefined}
              >
                <List.Item.Meta
                  title={<Space wrap><span className="qc-upload-file-name">{item.filename}</span><Tag color={STAGE_COLOR[item.status]}>{STAGE_LABEL[item.status]}</Tag></Space>}
                  description={item.error ? <Text type="danger">{item.error}</Text> : null}
                />
              </List.Item>
            )}
          />
        ) : (
          <div className="qc-upload-empty">还没选文件。选好 Excel 后点上传。</div>
        )}
      </Card>
    </div>
  )
}
