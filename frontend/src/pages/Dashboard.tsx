import { useEffect, useState } from 'react'
import { Row, Col, Card, Statistic, Spin, Typography, Alert } from 'antd'
import { reportApi, analysisApi } from '../api'
import { formatConversationTime } from '../components/Conversation'

const { Title, Text } = Typography

export default function Dashboard() {
  const [stats, setStats] = useState<any>(null)
  const [batches, setBatches] = useState<any[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.all([
      reportApi.getStats({}),
      analysisApi.listBatches(),
    ]).then(([s, b]) => {
      setStats(s.data)
      setBatches(b.data.slice(0, 5))
    }).finally(() => setLoading(false))
  }, [])

  if (loading) return <Spin size="large" style={{ display: 'block', marginTop: 80, textAlign: 'center' }} />

  return (
    <div>
      <Title level={4} style={{ marginBottom: 20 }}>概览</Title>

      <Row gutter={16} style={{ marginBottom: 24 }}>
        {[
          { title: '质检AI消息数', value: stats?.total_ai_msgs ?? 0, color: '#1d4ed8' },
          { title: '发现问题总数', value: stats?.total_issues ?? 0, color: '#374151' },
          { title: '严重问题', value: stats?.severe ?? 0, color: '#dc2626' },
          { title: '中级问题', value: stats?.medium ?? 0, color: '#d97706' },
          { title: '一般问题', value: stats?.general ?? 0, color: '#2563eb' },
          { title: '需人工复核', value: stats?.review ?? 0, color: '#6b7280' },
        ].map(item => (
          <Col xs={12} sm={8} md={4} key={item.title}>
            <Card>
              <Statistic title={item.title} value={item.value} valueStyle={{ color: item.color, fontSize: 28 }} />
            </Card>
          </Col>
        ))}
      </Row>

      <Row gutter={16}>
        <Col span={12}>
          <Card title="主要问题类型 TOP10">
            {stats?.top_issues?.length ? (
              <ol style={{ paddingLeft: 20, margin: 0 }}>
                {stats.top_issues.map((t: string) => <li key={t} style={{ marginBottom: 4 }}>{t}</li>)}
              </ol>
            ) : <Text type="secondary">还没有问题类型统计。</Text>}
          </Card>
        </Col>
        <Col span={12}>
          <Card title="最近分析批次">
            {batches.length ? batches.map((b: any) => (
              <div key={b.id} style={{ padding: '8px 0', borderBottom: '1px solid #f0f0f0' }}>
                <div style={{ fontWeight: 500 }}>{b.name}</div>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {formatConversationTime(b.created_at) || '—'} · {b.status} · {b.issue_count ?? 0}个问题
                </Text>
              </div>
            )) : <Text type="secondary">还没有分析批次。先去上传文件。</Text>}
          </Card>
        </Col>
      </Row>
    </div>
  )
}
