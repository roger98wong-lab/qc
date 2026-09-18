import { useEffect, useState } from 'react'
import { Button, Card, Space, Table, Tag, Typography, message } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { reviewApi } from '../api'

export default function ReviewAssignments() {
  const [rows, setRows] = useState<any[]>([])
  const [loading, setLoading] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const result = await reviewApi.listTasks()
      setRows(result.data || [])
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '加载审核分派失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  return (
    <div className="review-workbench">
      <Space>
        <Typography.Title level={4}>审核分派</Typography.Title>
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
            { title: '处理人', dataIndex: 'assignee' },
            { title: '状态', dataIndex: 'status' },
            { title: '摘要', render: (_: unknown, row: any) => row.issue_type || row.title || '-' },
          ]}
        />
      </Card>
    </div>
  )
}
