import { useCallback, useEffect, useMemo, useState } from 'react'
import { Button, Card, Dropdown, Modal, Table, Tag, Tooltip, Typography, message } from 'antd'
import {
  DeleteOutlined, EllipsisOutlined, EyeOutlined, PauseCircleOutlined,
  PlayCircleOutlined, ReloadOutlined, StopOutlined,
} from '@ant-design/icons'
import type { MenuProps } from 'antd'
import { analysisApi } from '../api'
import { formatConversationTime } from '../components/Conversation'
import {
  BATCH_STATUS_COLOR, BATCH_STATUS_LABEL, getBatchControlState, isTerminalBatchStatus,
} from '../batchControl'

const { Title, Text } = Typography

const errorText = (error: any, fallback: string) => {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  return error?.message || fallback
}

export default function History() {
  const [batches, setBatches] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [rowAction, setRowAction] = useState<{ id: number; action: string } | null>(null)
  const [openMenuId, setOpenMenuId] = useState<number | null>(null)

  const mergeRow = useCallback((id: number, patch: any) => {
    setBatches(current => current.map(row => row.id === id ? { ...row, ...patch } : row))
  }, [])

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true)
    try {
      const response = await analysisApi.listBatches()
      setBatches(response.data || [])
    } finally {
      if (!silent) setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  useEffect(() => {
    const live = batches.some(row => ['analyzing', 'paused', 'uploading', 'parsing'].includes(row.status))
    if (!live) return
    const timer = window.setInterval(() => void load(true), 2500)
    return () => window.clearInterval(timer)
  }, [batches, load])

  const runAction = async (row: any, action: 'start' | 'pause' | 'resume' | 'abort' | 'delete') => {
    setRowAction({ id: row.id, action })
    try {
      if (action === 'delete') {
        message.loading({ content: '正在删除批次数据，切片较多时可能需要一两分钟…', key: 'batch-delete', duration: 0 })
        await analysisApi.deleteBatch(row.id)
        message.success({ content: '删除成功', key: 'batch-delete' })
        setBatches(current => current.filter(item => item.id !== row.id))
        return
      }
      const api = action === 'start'
        ? analysisApi.startBatch
        : action === 'pause'
          ? analysisApi.pauseBatch
          : action === 'resume'
            ? analysisApi.resumeBatch
            : analysisApi.abortBatch
      const response = await api(row.id)
      mergeRow(row.id, response.data)
    } catch (error) {
      if (action === 'delete') message.destroy('batch-delete')
      message.error(errorText(error, '操作失败'))
      await load(true)
    } finally {
      setRowAction(null)
    }
  }

  const confirmDanger = (row: any, action: 'abort' | 'delete') => {
    Modal.confirm({
      title: action === 'abort' ? '确认终止该批次？' : '确认删除该批次？',
      content: action === 'abort'
        ? '未分析的切片不会再分析，已出结果保留。之后不能追加、不能重启、不能再开始。'
        : '将删除该批次的所有问题记录和知识库建议，无法恢复。',
      okText: action === 'abort' ? '终止' : '确认删除',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: () => runAction(row, action),
    })
  }

  const columns = useMemo(() => [
    {
      title: '批次名称', dataIndex: 'name', ellipsis: true,
      render: (value: string) => <span className="qc-history-name" title={value}>{value}</span>,
    },
    {
      title: '状态', dataIndex: 'status', width: 92,
      render: (value: string) => <Tag color={BATCH_STATUS_COLOR[value] || 'default'}>{BATCH_STATUS_LABEL[value] || value}</Tag>,
    },
    {
      title: '已处理 / 待处理', width: 176, ellipsis: false,
      onHeaderCell: () => ({ style: { minWidth: 176 } }),
      onCell: () => ({ className: 'qc-history-counts-cell', style: { minWidth: 176 } }),
      render: (_: any, row: any) => {
        const processed = Number(row.processed_count ?? row.analyzed_slices ?? 0)
        const pending = Number(row.pending_count ?? 0)
        return (
          <span className="qc-history-counts">
            <span>已处理 {processed}</span>
            <span>待处理 {pending}</span>
          </span>
        )
      },
    },
    { title: '问题数', dataIndex: 'issue_count', width: 72 },
    {
      title: '创建时间', dataIndex: 'created_at', width: 168,
      render: (value: string) => formatConversationTime(value) || '—',
    },
    {
      title: '操作', key: 'actions', width: 136, fixed: 'right' as const,
      render: (_: any, row: any) => {
        const control = getBatchControlState({
          status: row.status,
          pendingCount: Number(row.pending_count || 0),
          processedCount: Number(row.processed_count || 0),
          runnableCount: Number(row.runnable_count ?? row.pending_count ?? 0),
        })
        const busy = rowAction?.id === row.id
        const primary = control.primary === 'start' && control.canStart
          ? 'start'
          : control.primary === 'pause'
            ? 'pause'
            : control.primary === 'resume'
              ? 'resume'
              : null
        const primaryButton = primary === 'start' ? (
          <Button size="small" type="primary" icon={<PlayCircleOutlined />} loading={busy && rowAction?.action === 'start'} onClick={() => void runAction(row, 'start')}>开始</Button>
        ) : primary === 'pause' ? (
          <Button size="small" type="primary" icon={<PauseCircleOutlined />} loading={busy && rowAction?.action === 'pause'} onClick={() => void runAction(row, 'pause')}>暂停</Button>
        ) : primary === 'resume' ? (
          <Button size="small" type="primary" icon={<ReloadOutlined />} loading={busy && rowAction?.action === 'resume'} onClick={() => void runAction(row, 'resume')}>重启</Button>
        ) : control.canView ? (
          <Button size="small" type="link" icon={<EyeOutlined />} href={`/report?batch_id=${row.id}`}>查看</Button>
        ) : null

        const items: MenuProps['items'] = [
          primary === 'start' ? null : { key: 'start', label: '开始', disabled: !control.canStart, icon: <PlayCircleOutlined /> },
          primary === 'pause' ? null : { key: 'pause', label: '暂停', disabled: !control.canPause, icon: <PauseCircleOutlined /> },
          primary === 'resume' ? null : { key: 'resume', label: '重启', disabled: !control.canResume, icon: <ReloadOutlined /> },
          { key: 'abort', label: '终止', danger: true, disabled: !control.canAbort, icon: <StopOutlined /> },
          {
            key: 'view',
            label: control.canView ? '查看报告' : <Tooltip title="尚无结果">查看报告</Tooltip>,
            disabled: !control.canView,
            icon: <EyeOutlined />,
          },
          { key: 'delete', label: '删除', danger: true, disabled: !control.canDelete, icon: <DeleteOutlined /> },
        ].filter(Boolean) as MenuProps['items']

        return (
          <div className="qc-history-ops">
            {primaryButton}
            <Dropdown
              trigger={['click']}
              placement="bottomRight"
              open={openMenuId === row.id}
              onOpenChange={open => setOpenMenuId(open ? row.id : null)}
              overlayClassName="qc-history-menu"
              menu={{
                items,
                onClick: ({ key, domEvent }) => {
                  domEvent.stopPropagation()
                  setOpenMenuId(null)
                  if (key === 'view') {
                    if (control.canView) window.location.href = `/report?batch_id=${row.id}`
                    return
                  }
                  if (key === 'abort' || key === 'delete') {
                    confirmDanger(row, key)
                    return
                  }
                  void runAction(row, key as 'start' | 'pause' | 'resume')
                },
              }}
              getPopupContainer={() => document.body}
            >
              <Button size="small" className="qc-history-more" icon={<EllipsisOutlined />} aria-label="更多" />
            </Dropdown>
          </div>
        )
      },
    },
  ], [openMenuId, rowAction])

  return (
    <div className="qc-history-page">
      <Title level={4} className="page-heading">历史记录</Title>
      <Text type="secondary" className="qc-history-intro">这里看每个批次的进度。已处理是分析完的，待处理是还没分析完的。</Text>
      <Card extra={<Button onClick={() => void load()} loading={loading}>刷新</Button>}>
        <Table
          className="qc-history-table"
          rowKey="id" dataSource={batches} columns={columns}
          loading={loading} pagination={{ pageSize: 20 }}
          size="small" scroll={{ x: 980 }}
          expandable={{
            expandedRowRender: row => row.error_msg
              ? <div className="qc-history-error">{row.error_msg}</div>
              : <Text type="secondary">{isTerminalBatchStatus(row.status) ? '批次已结束。' : '可在此开始、暂停、重启或终止。'}</Text>,
            rowExpandable: () => true,
          }}
        />
      </Card>
    </div>
  )
}
