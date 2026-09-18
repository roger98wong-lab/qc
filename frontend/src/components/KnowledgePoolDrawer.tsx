import { useEffect, useMemo, useState } from 'react'
import { Alert, Button, Drawer, Empty, Input, Modal, Select, Space, Table, Tag, Typography, message } from 'antd'
import { CheckOutlined, CopyOutlined, DownloadOutlined, ExportOutlined, HistoryOutlined } from '@ant-design/icons'
import { knowledgePoolApi } from '../api'

type PoolItem = {
  id?: number | string
  pool_id?: string
  game?: string
  region?: string
  channel?: string
  question?: string
  answer?: string
  qa_source?: string
  processing_status?: string
}

type FilterState = {
  game?: string
  region?: string
  channel?: string
  processing_status: string
}

const SOURCE_LABELS: Record<string, string> = {
  human_agent: '人工客服产出',
  quality_reviewer: '质检员产出',
}

const STATUS_LABELS: Record<string, string> = {
  pending_entry: '待录入',
  organized: '已整理',
  exported: '已导出',
  uploaded_to_jiuzhang: '已上传九章',
  excluded: '已排除',
}

const STATUS_COLORS: Record<string, string> = {
  pending_entry: 'blue',
  organized: 'processing',
  exported: 'success',
  uploaded_to_jiuzhang: 'purple',
  excluded: 'default',
}

function poolKey(row: PoolItem) {
  return String(row.pool_id || row.id || '')
}

function sourceLabel(value?: string) {
  return SOURCE_LABELS[value || 'human_agent'] || value || '人工客服产出'
}

function displayText(value: unknown) {
  if (value == null || value === '[object Object]') return ''
  return String(value)
}

function formatTime(value: unknown) {
  const raw = displayText(value)
  if (!raw) return ''
  const local = raw.match(/^(\d{4}-\d{2}-\d{2})[T\s](\d{2}:\d{2}:\d{2})(?:\.\d+)?$/)
  if (local) return `${local[1]} ${local[2]}`
  const date = new Date(raw)
  if (Number.isNaN(date.getTime())) return raw.replace('T', ' ')
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
  }).format(date).replace(/\//g, '-')
}

function copyPayload(rows: PoolItem[]) {
  return rows.map(row => `问题：\n${displayText(row.question)}\n\n答案：\n${displayText(row.answer)}`).join('\n\n---\n\n')
}

async function copyText(value: string) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value)
    return
  }
  const area = document.createElement('textarea')
  area.value = value
  area.style.position = 'fixed'
  area.style.opacity = '0'
  document.body.appendChild(area)
  area.select()
  const ok = document.execCommand('copy')
  area.remove()
  if (!ok) throw new Error('浏览器未授权剪贴板权限')
}

export default function KnowledgePoolDrawer({ open, onClose, isAdmin }: { open: boolean; onClose: () => void; isAdmin: boolean }) {
  const [items, setItems] = useState<PoolItem[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(50)
  const [loading, setLoading] = useState(false)
  const [filters, setFilters] = useState<FilterState>({ processing_status: 'pending_entry' })
  const [options, setOptions] = useState<{ games: string[]; regions: string[]; channels: string[] }>({ games: [], regions: [], channels: [] })
  const [selectedKeys, setSelectedKeys] = useState<Array<string | number>>([])
  const [selectedMap, setSelectedMap] = useState<Record<string, PoolItem>>({})
  const [statusTarget, setStatusTarget] = useState<PoolItem | null>(null)
  const [nextStatus, setNextStatus] = useState('pending_entry')
  const [reason, setReason] = useState('')
  const [logs, setLogs] = useState<any[] | null>(null)
  const [logsLoading, setLogsLoading] = useState(false)

  const selectedRows = useMemo(
    () => selectedKeys.map(key => selectedMap[String(key)]).filter(Boolean),
    [selectedKeys, selectedMap],
  )

  const load = async () => {
    if (!open) return
    setLoading(true)
    try {
      const result = await knowledgePoolApi.list({
        ...filters,
        assignment_scope: isAdmin ? 'all' : 'mine',
        page,
        page_size: pageSize,
      })
      const data = result.data || {}
      setItems(data.items || [])
      setTotal(data.total || 0)
      const raw = data.filter_options || {}
      setOptions({
        games: raw.games || raw.game || [],
        regions: raw.regions || raw.region || [],
        channels: raw.channels || raw.channel || [],
      })
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '加载知识池失败，请检查权限或稍后重试')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [open, isAdmin, page, pageSize, filters.game, filters.region, filters.channel, filters.processing_status])

  const clearSelection = () => {
    setSelectedKeys([])
    setSelectedMap({})
  }

  const copySelected = async () => {
    if (!selectedRows.length) {
      message.info('请先选择 QA')
      return
    }
    try {
      await copyText(copyPayload(selectedRows))
      message.success(`已复制 ${selectedRows.length} 条 QA`)
    } catch (error: any) {
      message.error(error?.message || '复制失败，请检查浏览器剪贴板权限')
    }
  }

  const exportSelected = async () => {
    if (!selectedRows.length) {
      message.info('请先选择 QA')
      return
    }
    try {
      const result = await knowledgePoolApi.exportCsv(selectedRows.map(poolKey))
      const blob = new Blob([result.data], { type: 'text/csv;charset=utf-8' })
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      const stamp = formatTime(new Date().toISOString()).replace(/[-: ]/g, '')
      link.download = `知识库QA_${stamp || '导出'}.csv`
      link.click()
      URL.revokeObjectURL(url)
      const skipped = Number(result.headers['x-qa-skipped-count'] || 0)
      message[skipped ? 'warning' : 'success'](
        skipped ? `已导出 ${selectedRows.length - skipped} 条，${skipped} 条未满足导出条件` : `已导出 ${selectedRows.length} 条 QA`,
      )
      clearSelection()
      await load()
    } catch (error: any) {
      let detail = error?.response?.data?.detail
      if (error?.response?.data instanceof Blob) {
        try { detail = JSON.parse(await error.response.data.text()).detail } catch {}
      }
      message.error(detail || '导出失败，未改变条目状态，请重新筛选后再选择')
    }
  }

  const organizeSelected = () => {
    if (!selectedRows.length) {
      message.info('请先选择 QA')
      return
    }
    Modal.confirm({
      title: '确认将选中条目标记为已整理？',
      content: `即将把 ${selectedRows.length} 条 QA 标记为已整理，是否继续？`,
      okText: '确认',
      cancelText: '取消',
      onOk: async () => {
        try {
          const result = await knowledgePoolApi.updateStatus({ ids: selectedRows.map(poolKey), status: 'organized' })
          const failed = result.data.failed_count || 0
          message[failed ? 'warning' : 'success'](`已整理 ${result.data.success_count || 0} 条${failed ? `，${failed} 条未处理` : ''}`)
          clearSelection()
          await load()
        } catch (error: any) {
          message.error(error?.response?.data?.detail || '整理失败，请刷新后重试')
        }
      },
    })
  }

  const saveStatus = async () => {
    if (!statusTarget) return
    if (nextStatus !== 'uploaded_to_jiuzhang' && !reason.trim()) {
      message.warning('排除或回退状态必须填写原因')
      return
    }
    try {
      const result = await knowledgePoolApi.updateStatus({
        ids: [poolKey(statusTarget)],
        status: nextStatus,
        reason: reason.trim() || undefined,
      })
      if ((result.data.success_count || 0) > 0) message.success(`已更新为${STATUS_LABELS[nextStatus]}`)
      else message.error(result.data.results?.[0]?.reason || '状态更新失败')
      setStatusTarget(null)
      setReason('')
      await load()
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '状态更新失败，请刷新后重试')
    }
  }

  const loadLogs = async (row: PoolItem) => {
    if (!isAdmin) return
    setLogs([])
    setLogsLoading(true)
    try {
      const result = await knowledgePoolApi.logs(poolKey(row))
      setLogs(result.data || [])
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '状态日志加载失败')
      setLogs(null)
    } finally {
      setLogsLoading(false)
    }
  }

  const columns = [
    { title: '游戏', dataIndex: 'game', width: 120, ellipsis: true },
    { title: '地区', dataIndex: 'region', width: 90, ellipsis: true },
    { title: '渠道', dataIndex: 'channel', width: 80 },
    {
      title: '问题', dataIndex: 'question', width: 260, ellipsis: { showTitle: false },
      render: (value: string) => <Typography.Text ellipsis={{ tooltip: value }}>{displayText(value)}</Typography.Text>,
    },
    {
      title: '答案', dataIndex: 'answer', width: 320, ellipsis: { showTitle: false },
      render: (value: string) => <Typography.Text ellipsis={{ tooltip: value }}>{displayText(value)}</Typography.Text>,
    },
    {
      title: '来源', dataIndex: 'qa_source', width: 120,
      render: (_: string, row: PoolItem) => <Tag color={row.qa_source === 'quality_reviewer' ? 'geekblue' : 'green'}>{sourceLabel(row.qa_source)}</Tag>,
    },
    {
      title: '状态', dataIndex: 'processing_status', width: 110,
      render: (value: string) => <Tag color={STATUS_COLORS[value] || 'default'}>{STATUS_LABELS[value] || value || '-'}</Tag>,
    },
    {
      title: '操作', width: isAdmin ? 230 : 90, fixed: 'right' as const,
      render: (_: unknown, row: PoolItem) => (
        <Space size={4}>
          {isAdmin && ['pending_entry', 'organized'].includes(row.processing_status || '') && (
            <Button type="link" danger size="small" onClick={() => { setStatusTarget(row); setNextStatus('excluded'); setReason('') }}>排除</Button>
          )}
          {isAdmin && ['organized', 'exported', 'excluded'].includes(row.processing_status || '') && (
            <Button type="link" size="small" onClick={() => { setStatusTarget(row); setNextStatus('pending_entry'); setReason('') }}>恢复待录入</Button>
          )}
          {isAdmin && row.processing_status === 'exported' && (
            <Button type="link" size="small" onClick={() => { setStatusTarget(row); setNextStatus('uploaded_to_jiuzhang'); setReason('') }}>标记已上传</Button>
          )}
          {isAdmin && <Button type="link" size="small" icon={<HistoryOutlined />} onClick={() => loadLogs(row)}>日志</Button>}
        </Space>
      ),
    },
  ]

  return (
    <>
      <Drawer
        title="一键 QA · 知识池待录入"
        placement="right"
        width="min(1100px, 92vw)"
        open={open}
        onClose={onClose}
        destroyOnClose={false}
        styles={{ body: { padding: 16 } }}
      >
        <Space direction="vertical" size={14} style={{ width: '100%' }}>
          <Alert type="info" showIcon message="展示人工客服产出和质检员整理过的待录入 QA。导出后仍需人工上传九章，本系统不会自动上传。" />
          <Space wrap>
            <Select allowClear placeholder="游戏" style={{ width: 170 }} value={filters.game} onChange={value => { setFilters(current => ({ ...current, game: value })); setPage(1) }} options={options.games.map(value => ({ value, label: value }))} />
            <Select allowClear placeholder="地区" style={{ width: 130 }} value={filters.region} onChange={value => { setFilters(current => ({ ...current, region: value })); setPage(1) }} options={options.regions.map(value => ({ value, label: value }))} />
            <Select allowClear placeholder="渠道" style={{ width: 110 }} value={filters.channel} onChange={value => { setFilters(current => ({ ...current, channel: value })); setPage(1) }} options={options.channels.map(value => ({ value, label: value }))} />
            <Select style={{ width: 150 }} value={filters.processing_status} onChange={value => { setFilters(current => ({ ...current, processing_status: value })); setPage(1) }} options={Object.entries(STATUS_LABELS).map(([value, label]) => ({ value, label }))} />
            <Button onClick={() => { setFilters({ processing_status: 'pending_entry' }); setPage(1) }}>重置筛选</Button>
            <Typography.Text type="secondary">当前 {total} 条</Typography.Text>
          </Space>
          <Space wrap>
            <Button icon={<CopyOutlined />} disabled={!selectedRows.length} onClick={copySelected}>复制选中 QA（{selectedRows.length}）</Button>
            <Button icon={<DownloadOutlined />} disabled={!selectedRows.length} onClick={exportSelected}>导出选中 QA（{selectedRows.length}）</Button>
            <Button className="qc-external-link qc-external-link--jiuzhang" icon={<ExportOutlined />} href="https://uce.4399houtai.com/micro/user" target="_blank" rel="noopener noreferrer">前往九章知识库</Button>
            <Button type="primary" icon={<CheckOutlined />} disabled={!selectedRows.length} onClick={organizeSelected}>标记已整理（{selectedRows.length}）</Button>
          </Space>
          <Table
            rowKey={row => poolKey(row)}
            size="small"
            loading={loading}
            dataSource={items}
            columns={columns}
            rowSelection={{
              selectedRowKeys: selectedKeys,
              preserveSelectedRowKeys: true,
              onChange: (keys, rows) => {
                const normalized = keys.map(key => typeof key === 'number' ? key : String(key))
                setSelectedKeys(normalized)
                setSelectedMap(current => {
                  const next = { ...current }
                  rows.forEach(row => { next[String(poolKey(row))] = row })
                  items.forEach(row => {
                    if (!normalized.some(key => String(key) === String(poolKey(row)))) delete next[String(poolKey(row))]
                  })
                  return next
                })
              },
            }}
            scroll={{ x: 1050 }}
            locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={filters.processing_status === 'pending_entry' ? '当前没有待录入 QA' : '当前筛选条件下没有结果'} /> }}
            pagination={{
              current: page,
              pageSize,
              total,
              showSizeChanger: true,
              pageSizeOptions: ['20', '50', '100'],
              showTotal: value => `共 ${value} 条`,
              onChange: (nextPage, nextSize) => {
                setPage(nextPage)
                if (nextSize !== pageSize) {
                  setPageSize(nextSize)
                  setPage(1)
                }
              },
            }}
          />
        </Space>
      </Drawer>
      <Modal
        title={`${statusTarget ? STATUS_LABELS[statusTarget.processing_status || ''] : ''} → ${STATUS_LABELS[nextStatus]}`}
        open={!!statusTarget}
        onCancel={() => setStatusTarget(null)}
        onOk={saveStatus}
        okText="确认修改"
        cancelText="取消"
      >
        <Typography.Paragraph type="secondary">该操作只更新人工处理状态，不会修改质检结论、系统原结果或旧接口。</Typography.Paragraph>
        <Input.TextArea value={reason} onChange={event => setReason(event.target.value)} placeholder={nextStatus === 'uploaded_to_jiuzhang' ? '可填写人工上传说明' : '请填写修改原因（必填）'} rows={4} />
      </Modal>
      <Modal title="状态变更日志" open={logs !== null} onCancel={() => setLogs(null)} footer={null} confirmLoading={logsLoading}>
        {logsLoading ? <Typography.Text type="secondary">正在加载…</Typography.Text> : logs != null && logs.length ? (
          <Space direction="vertical" style={{ width: '100%' }}>
            {logs.map((row: any) => (
              <Typography.Paragraph key={row.id} style={{ marginBottom: 8 }}>
                <Tag>{STATUS_LABELS[row.from_status] || row.from_status || '-'} → {STATUS_LABELS[row.to_status] || row.to_status}</Tag>
                {' '}{row.operator_name} · {formatTime(row.created_at)}
                <br />
                <Typography.Text type="secondary">{row.reason || '-'}</Typography.Text>
              </Typography.Paragraph>
            ))}
          </Space>
        ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无状态变更日志" />}
      </Modal>
    </>
  )
}
