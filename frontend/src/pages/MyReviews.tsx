import { useEffect, useState } from 'react'
import { Button, Card, Modal, Select, Space, Table, Tag, Typography, Input, message } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { reviewApi } from '../api'

export default function MyReviews() {
  const [rows, setRows] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [current, setCurrent] = useState<any>()
  const [decision, setDecision] = useState<string>()
  const [comment, setComment] = useState('')

  const load = async () => {
    setLoading(true)
    try {
      const result = await reviewApi.listMine()
      setRows(result.data || [])
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '加载我的审核任务失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const start = async (row: any) => {
    try {
      const result = await reviewApi.startTask(row.id)
      setRows(currentRows => currentRows.map(item => item.id === row.id ? result.data : item))
      setCurrent(result.data)
    } catch {
      message.error('开始审核失败')
    }
  }

  const submit = async (asReturn = false) => {
    if (!current) return
    try {
      if (asReturn) {
        if (!comment.trim()) {
          message.warning('请填写退回原因')
          return
        }
        await reviewApi.returnTask(current.id, comment)
      } else {
        if (!decision) {
          message.warning('请选择结论')
          return
        }
        await reviewApi.submitTask(current.id, { decision, comment })
      }
      message.success('操作成功')
      setCurrent(undefined)
      load()
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '操作失败')
    }
  }

  return (
    <div className="review-workbench">
      <Space>
        <Typography.Title level={4}>我的审核任务</Typography.Title>
        <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
      </Space>
      <Card>
        <Table
          rowKey="id"
          loading={loading}
          dataSource={rows}
          columns={[
            { title: '类型', dataIndex: 'item_type', render: value => <Tag>{value === 'quality_issue' ? '质检问题' : '知识建议'}</Tag> },
            { title: '切片', dataIndex: 'slice_id' },
            { title: '摘要', render: (_: unknown, row: any) => row.issue_type || row.title || '-' },
            { title: '状态', dataIndex: 'status' },
            { title: '操作', render: (_: unknown, row: any) => (
              <Space>
                <Button size="small" onClick={() => setCurrent(row)}>查看</Button>
                {['pending', 'returned'].includes(row.status) && <Button size="small" type="primary" onClick={() => start(row)}>开始</Button>}
              </Space>
            ) },
          ]}
        />
      </Card>
      <Modal open={!!current} title="任务详情" onCancel={() => setCurrent(undefined)} footer={null}>
        {current && (
          <Space direction="vertical" style={{ width: '100%' }}>
            <p>类型：{current.item_type}，状态：{current.status}</p>
            {['pending', 'in_progress', 'returned'].includes(current.status) && (
              <>
                <Select
                  style={{ width: '100%' }}
                  value={decision}
                  onChange={setDecision}
                  placeholder="选择审核结论"
                  options={(current.item_type === 'quality_issue'
                    ? [['confirmed', '确认问题'], ['rejected', '判定无问题'], ['needs_review', '需要复核']]
                    : [['approved', '建议有效'], ['rejected', '不适合沉淀'], ['needs_edit', '需要修改后再确认']]
                  ).map(([value, label]) => ({ value, label }))}
                />
                <Input.TextArea value={comment} onChange={event => setComment(event.target.value)} placeholder="备注" />
                <Space>
                  <Button type="primary" onClick={() => submit()}>提交</Button>
                  <Button danger onClick={() => submit(true)}>退回</Button>
                </Space>
              </>
            )}
          </Space>
        )}
      </Modal>
    </div>
  )
}
