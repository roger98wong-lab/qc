import { useCallback, useEffect, useMemo, useState } from 'react'
import dayjs, { type Dayjs } from 'dayjs'
import { Alert, Button, Card, DatePicker, Empty, Select, Space, Spin, Tag, Typography, message } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { dashboardApi } from '../api'
import { useAuthStore } from '../store/auth'

const { Title, Text } = Typography
const { RangePicker } = DatePicker
type Payload = { filters: { start_date: string; end_date: string; assignee_id: number | null; assignees: Array<{ id: number; username: string }> }; summary: { unprocessed: number; processing: number; pending_entry: number; processed: number; tagged_cases: number; knowledge_pending_entry: number; top_tag: { name: string | null; count: number }; top_handler: { name: string | null; count: number } }; tag_trend: Array<{ date: string; tags: Record<string, number> }>; tag_totals: Array<{ tag: string; count: number }> }
const COLORS = ['#1d4ed8', '#0f766e', '#9333ea', '#c2410c', '#be123c', '#0369a1', '#4d7c0f', '#7c3aed']
const colorOf = (name: string) => { let hash = 0; for (const ch of name) hash = (hash * 31 + ch.charCodeAt(0)) | 0; return COLORS[Math.abs(hash) % COLORS.length] }
const errorText = (e: any) => e?.response?.data?.detail || '加载数据看板失败，请检查权限或网络后重试'

function yTicks(max: number) {
  if (max <= 1) return [0, 1]
  if (max <= 5) return Array.from({ length: max + 1 }, (_, i) => i)
  const step = Math.max(1, Math.ceil(max / 4))
  const ticks = [0]
  for (let value = step; value < max; value += step) ticks.push(value)
  if (ticks[ticks.length - 1] !== max) ticks.push(max)
  return ticks
}

function xTickIndexes(count: number) {
  if (count <= 8) return Array.from({ length: count }, (_, i) => i)
  const maxLabels = 6
  const step = Math.ceil((count - 1) / (maxLabels - 1))
  const ticks = [0]
  for (let i = step; i < count - 1; i += step) ticks.push(i)
  if (ticks[ticks.length - 1] !== count - 1) ticks.push(count - 1)
  return ticks
}

function formatAxisDate(value: string) {
  const match = value.match(/(\d{4})-(\d{2})-(\d{2})/)
  if (!match) return value
  return `${Number(match[2])}/${Number(match[3])}`
}

function dayTotal(day: Payload['tag_trend'][number], tags: string[]) {
  return tags.reduce((sum, tag) => sum + (day.tags[tag] || 0), 0)
}

function Trend({ data }: { data: Payload }) {
  const tags = data.tag_totals.map(item => item.tag)
  if (!tags.length) return <Empty className="qc-workload-empty" description="当前筛选范围内没有人工打标数据" />
  const width = 720, height = 168, left = 28, right = 8, top = 8, bottom = 28
  const plotW = width - left - right
  const plotH = height - top - bottom
  const days = data.tag_trend
  const max = Math.max(1, ...days.map(day => dayTotal(day, tags)))
  const slot = plotW / Math.max(days.length, 1)
  const barW = Math.max(2.5, Math.min(9, slot * 0.62))
  const x = (i: number) => left + slot * i + slot / 2
  const y = (v: number) => top + plotH * (1 - v / max)
  const vertical = yTicks(max)
  const horizontal = xTickIndexes(days.length)
  return (
    <div className="qc-workload-trend">
      <div className="qc-workload-chart">
        <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="按人工问题标签统计的每日打标案例数量">
          {vertical.map(value => (
            <g key={value}>
              <line x1={left} x2={width - right} y1={y(value)} y2={y(value)} className="qc-workload-grid-line" />
              <text x={left - 6} y={y(value) + 3} textAnchor="end" className="qc-workload-axis-text">{value}</text>
            </g>
          ))}
          <line x1={left} x2={width - right} y1={y(0)} y2={y(0)} className="qc-workload-baseline" />
          {days.map((day, i) => {
            let offset = 0
            return (
              <g key={day.date}>
                {tags.map(tag => {
                  const count = day.tags[tag] || 0
                  if (!count) return null
                  const y1 = y(offset + count)
                  const h = y(offset) - y1
                  offset += count
                  return (
                    <rect key={tag} x={x(i) - barW / 2} y={y1} width={barW} height={Math.max(h, 1.5)} rx="1.2" fill={colorOf(tag)}>
                      <title>{`${formatAxisDate(day.date)} ${tag} ${count}`}</title>
                    </rect>
                  )
                })}
              </g>
            )
          })}
          {horizontal.map(i => (
            <text key={days[i].date} x={x(i)} y={height - 8} textAnchor="middle" className="qc-workload-axis-text qc-workload-axis-date">{formatAxisDate(days[i].date)}</text>
          ))}
        </svg>
      </div>
      <div className="qc-workload-legend" aria-label="标签图例">
        {data.tag_totals.map(item => (
          <span key={item.tag} className="qc-workload-legend-item">
            <i style={{ backgroundColor: colorOf(item.tag) }} aria-hidden="true" />
            {item.tag}
            <b>{item.count}</b>
          </span>
        ))}
      </div>
    </div>
  )
}

export default function ReviewWorkloadDashboard() {
  const user = useAuthStore(s => s.user), admin = user?.role === 'admin'
  const [range, setRange] = useState<[Dayjs, Dayjs]>([dayjs().subtract(29, 'day'), dayjs()]); const [assignee, setAssignee] = useState<number>(); const [data, setData] = useState<Payload>(); const [loading, setLoading] = useState(false); const [error, setError] = useState('')
  const load = useCallback(async () => { setLoading(true); setError(''); try { const result = await dashboardApi.reviewWorkload({ start_date: range[0].format('YYYY-MM-DD'), end_date: range[1].format('YYYY-MM-DD'), ...(admin && assignee ? { assignee_id: assignee } : {}) }); setData(result.data); if (!admin) setAssignee(result.data.filters.assignee_id || user?.id) } catch (e) { const text = errorText(e); setError(text); message.error(text) } finally { setLoading(false) } }, [admin, assignee, range, user?.id])
  useEffect(() => { void load() }, [load])
  const cards = useMemo(() => { const s = data?.summary; return [['未处理', s?.unprocessed ?? 0, '当前存量'], ['处理中', s?.processing ?? 0, '当前存量'], ['待录入', s?.pending_entry ?? 0, '质检员 QA 当前存量'], ['已处理', s?.processed ?? 0, '当前存量'], ['已打标案例', s?.tagged_cases ?? 0, '筛选范围内'], ['知识库待录入', s?.knowledge_pending_entry ?? 0, '人工客服 QA 当前存量'], ['最多标签', s?.top_tag.name ? `${s.top_tag.name} ${s.top_tag.count}` : '—', '筛选范围内人工标签'], ['负责人 / 处理量', s?.top_handler.name ? `${s.top_handler.name} ${s.top_handler.count}` : '—', '筛选范围内已完成']] }, [data])
  return <main className="qc-workload-page"><div className="qc-workload-heading"><div><Title level={3}>数据看板</Title></div></div><Card className="qc-workload-filter-card" size="small"><Space className="qc-workload-filters" wrap size={[12,12]} align="end"><label><span>人工打标时间</span><RangePicker value={range} allowClear={false} onChange={v => v?.[0] && v?.[1] && setRange([v[0],v[1]])}/></label><label><span>负责人</span><Select value={admin ? assignee : (data?.filters.assignee_id || user?.id)} disabled={!admin} allowClear={admin} placeholder="全部负责人" onChange={setAssignee} options={(data?.filters.assignees || []).map(p => ({value:p.id,label:p.username}))}/></label><Button type="primary" icon={<ReloadOutlined/>} onClick={() => void load()} loading={loading} disabled={loading}>刷新看板</Button></Space></Card>{error && <Alert className="qc-workload-alert" type="error" showIcon message="看板加载失败" description={error}/>}<section className="qc-workload-summary" aria-label="审核工作量指标">{cards.map(([label,value,hint]) => <Card key={label} size="small" className="qc-workload-metric"><Text className="qc-workload-metric-label">{label}</Text><div className="qc-workload-metric-value" title={String(value)}>{value}</div><Tag>{hint}</Tag></Card>)}</section><Card className="qc-workload-trend-card" title="每日打标趋势" extra={<Text type="secondary">按人工问题标签统计</Text>}><Spin spinning={loading}>{data ? <Trend data={data}/> : <Empty description="还没有数据，点上面的刷新。"/>}</Spin></Card><Text className="qc-workload-note" type="secondary">上面几个数字是当前积压，趋势按你选的打标时间统计。</Text></main>
}
