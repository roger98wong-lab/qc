import { useEffect, useState } from 'react'
import { Alert, Card, Descriptions, Drawer, Select, Space, Table, Tag, Tooltip, Typography } from 'antd'
import { adminApi } from '../api'
import { ConversationMessageList, conversationFromIssue, formatConversationTime } from '../components/Conversation'
import { useAuthStore } from '../store/auth'

const { Title, Text } = Typography

const ANALYSIS_STATUS_LABELS: Record<string, string> = { pending: '等待分析', processing: '分析中', completed: '分析完成', partial: '部分完成', failed: '分析失败' }
const KNOWLEDGE_DECISION_LABELS: Record<string, string> = { candidate_ready: '可直接沉淀', candidate_needs_enrichment: '需补充后沉淀', candidate_pending_feedback: '待验证候选', not_candidate: '不适合沉淀', reject: '不适合沉淀', manual_review: '人工复核', no_human_answer: '无人工回复' }
const displayValue = (value: unknown, fallback = '—'): string => {
  if (value === null || value === undefined) return fallback
  if (typeof value === 'string') return value.trim() || fallback
  if (Array.isArray(value)) return value.length ? value.join('、') : fallback
  if (typeof value === 'object') return fallback
  return String(value)
}
const formatDateTime = (value: unknown): string => {
  if (typeof value !== 'string' || !value) return '—'
  return formatConversationTime(value) || '—'
}
function StatusTag({ value, labels }: { value: unknown; labels: Record<string, string> }) {
  const raw = typeof value === 'string' ? value : ''
  const color = raw === 'failed' ? 'error' : raw === 'completed' || raw === 'candidate_ready' || raw === 'candidate_needs_enrichment' ? 'success' : raw ? 'processing' : 'default'
  return <Tag color={color}>{labels[raw] || displayValue(raw, '—')}</Tag>
}

export default function AdminAudit() {
  const user = useAuthStore(s => s.user)
  const [rows, setRows] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [detail, setDetail] = useState<any>(null)
  const [status, setStatus] = useState<string>()
  useEffect(() => {
    if (user?.role !== 'admin') return
    setLoading(true)
    adminApi.listAuditSlices(status ? { analysis_status: status } : undefined)
      .then(r => setRows(Array.isArray(r.data) ? r.data : (r.data.items || [])))
      .finally(() => setLoading(false))
  }, [user?.role, status])
  if (user?.role !== 'admin') return <Alert type="error" message="无权访问" />
  const columns = [
    { title: '批次', dataIndex: 'batch_name', render: (v: string, row: any) => displayValue(v || row.batch_id) },
    { title: '切片ID', dataIndex: 'slice_id', ellipsis: true, render: (v: string) => displayValue(v) },
    { title: '渠道', dataIndex: 'channel', render: (v: string) => displayValue(v) }, { title: '游戏', dataIndex: 'game', render: (v: string) => displayValue(v) }, { title: '地区', dataIndex: 'region', render: (v: string) => displayValue(v) },
    { title: '质检', dataIndex: 'quality_has_issue', render: (v: boolean, row: any) => v ? <Tag color="red">有问题（{row.quality_issue_count || 0}）</Tag> : <Tag>无问题</Tag> },
    { title: '知识建议', dataIndex: 'knowledge_decision', render: (v: string) => <StatusTag value={v} labels={KNOWLEDGE_DECISION_LABELS} /> },
    { title: '分析状态', dataIndex: 'analysis_status', render: (v: string) => <StatusTag value={v} labels={ANALYSIS_STATUS_LABELS} /> },
    { title: '操作', render: (_: any, row: any) => <a onClick={() => adminApi.getAuditSlice(row.id).then(r => setDetail(r.data))}>详情</a> },
  ]
  const detailRow = detail || {}
  const transcript = detailRow.messages ? { conversation_id: detailRow.conversation_id || detailRow.slice_id, messages: detailRow.messages } : conversationFromIssue(detailRow)
  return <div className="qc-admin-audit-page">
    <Title level={4} className="page-heading">分析审计</Title>
    <Card extra={<Select allowClear placeholder="选择分析状态" style={{ width: 140 }} value={status} onChange={setStatus} options={['pending', 'processing', 'completed', 'partial', 'failed'].map(v => ({ value: v, label: ANALYSIS_STATUS_LABELS[v] }))} />}>
      <Table rowKey="id" loading={loading} dataSource={rows} columns={columns} scroll={{ x: 1000 }} size="small" locale={{ emptyText: '暂无分析记录' }} />
    </Card>
    <Drawer title="切片分析详情" placement="right" width="40vw" open={!!detail} onClose={() => setDetail(null)} className="qc-issue-drawer">
      {detail && <Space direction="vertical" style={{ width: '100%' }} size={16}>
        <Descriptions bordered column={1} size="small">
          <Descriptions.Item label="切片ID">{displayValue(detailRow.slice_id)}</Descriptions.Item>
          <Descriptions.Item label="渠道">{displayValue(detailRow.channel)}</Descriptions.Item>
          <Descriptions.Item label="游戏">{displayValue(detailRow.game)}</Descriptions.Item>
          <Descriptions.Item label="地区">{displayValue(detailRow.region)}</Descriptions.Item>
          <Descriptions.Item label="分析状态"><StatusTag value={detailRow.analysis_status} labels={ANALYSIS_STATUS_LABELS} /></Descriptions.Item>
          <Descriptions.Item label="知识建议"><StatusTag value={detailRow.knowledge_decision} labels={KNOWLEDGE_DECISION_LABELS} /></Descriptions.Item>
          <Descriptions.Item label="分析时间"><Tooltip title={displayValue(detailRow.completed_at || detailRow.started_at || detailRow.created_at)}>{formatDateTime(detailRow.completed_at || detailRow.started_at || detailRow.created_at)}</Tooltip></Descriptions.Item>
        </Descriptions>
        <ConversationMessageList conversation={transcript} emptyText="暂无切片消息" />
        {detailRow.error_message && <Text type="danger">错误：{detailRow.error_message}</Text>}
      </Space>}
    </Drawer>
  </div>
}
