import { useCallback, useEffect, useMemo, useState } from 'react'
import { Alert, Button, Card, DatePicker, Empty, Select, Spin, Table, Tag, Typography } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import dayjs, { type Dayjs } from 'dayjs'
import { adminApi } from '../api'
import MultiFilterSelect from '../components/MultiFilterSelect'

const { Text } = Typography
const { RangePicker } = DatePicker

const HANDOFF_DECISION_LABELS: Record<string, string> = {
  handoff_required: '需要转人工',
  handoff_reasonable: '转人工合理',
  handoff_not_required: '无需转人工',
  handoff_unreasonable: '转人工不合理',
  manual_review: '需人工复核',
}

const SCOPE_OPTIONS = [
  { value: 'occurred', label: '已转人工' },
  { value: 'required', label: '应转未转' },
  { value: 'unreasonable', label: '不合理转人工' },
]

type ReasonRow = {
  reason_type: string
  count: number
  share: number
  reasonable: number
  unreasonable: number
  required: number
  is_positive: boolean
}

type SliceRow = {
  id: number
  slice_id: string
  batch_name?: string
  channel?: string
  game?: string
  region?: string
  completed_at?: string
  human_handoff?: {
    decision?: string
    handoff_occurred?: boolean
    reason_type?: string
    reason?: string
  }
}

type Payload = {
  scope: string
  selected_reason_type: string | null
  summary: {
    analyzed: number
    occurred: number
    reasonable: number
    unreasonable: number
    required: number
  }
  scoped_count: number
  reasons: ReasonRow[]
  slices: SliceRow[]
  total: number
  page: number
  page_size: number
  filters: {
    games: string[]
    channels: string[]
    regions: string[]
    batches: { id: number; name: string }[]
  }
}

function percent(share: number) {
  return `${((share || 0) * 100).toFixed(1)}%`
}

export default function HandoffTopTab({ onOpenDetail }: { onOpenDetail: (id: number) => void }) {
  const [range, setRange] = useState<[Dayjs, Dayjs]>([dayjs().subtract(29, 'day'), dayjs()])
  const [scope, setScope] = useState('occurred')
  const [games, setGames] = useState<string[]>([])
  const [channels, setChannels] = useState<string[]>([])
  const [regions, setRegions] = useState<string[]>([])
  const [batchIds, setBatchIds] = useState<number[]>([])
  const [reasonType, setReasonType] = useState<string>()
  const [page, setPage] = useState(1)
  const [data, setData] = useState<Payload>()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const query = useMemo(() => {
    const params: Record<string, string | number> = {
      start: range[0].startOf('day').format('YYYY-MM-DDTHH:mm:ss'),
      end: range[1].endOf('day').format('YYYY-MM-DDTHH:mm:ss'),
      scope,
      page,
      page_size: 20,
    }
    if (games.length) params.game = games.join(',')
    if (channels.length) params.channel = channels.join(',')
    if (regions.length) params.region = regions.join(',')
    if (batchIds.length) params.batch_ids = batchIds.join(',')
    if (reasonType) params.reason_type = reasonType
    return params
  }, [batchIds, channels, games, page, range, reasonType, regions, scope])

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const result = await adminApi.getHandoffTop(query)
      setData(result.data)
    } catch (err: any) {
      setData(undefined)
      setError(err?.response?.data?.detail || '转人工类型统计加载失败，请稍后重试。')
    } finally {
      setLoading(false)
    }
  }, [query])

  useEffect(() => { void load() }, [load])

  const maxCount = Math.max(1, ...(data?.reasons || []).map(item => item.count))
  const cards: [string, number][] = [
    ['已转人工', data?.summary.occurred || 0],
    ['合理转人工', data?.summary.reasonable || 0],
    ['不合理转人工', data?.summary.unreasonable || 0],
    ['应转未转', data?.summary.required || 0],
  ]
  const options = (values: string[]) => values.map(value => ({ value, label: value }))

  return (
    <div className="qc-handoff-top">
      <Card className="qc-workload-filter-card" size="small">
        <div className="qc-workload-filters">
          <label>
            <span>分析完成时间</span>
            <RangePicker value={range} allowClear={false} onChange={value => value?.[0] && value?.[1] && (setPage(1), setRange([value[0], value[1]]))} />
          </label>
          <label>
            <span>统计口径</span>
            <Select value={scope} options={SCOPE_OPTIONS} onChange={value => { setScope(value); setPage(1); setReasonType(undefined) }} />
          </label>
          <label>
            <span>游戏</span>
            <MultiFilterSelect placeholder="全部游戏" value={games} options={options(data?.filters.games || [])} onChange={value => { setGames(value); setPage(1) }} />
          </label>
          <label>
            <span>渠道</span>
            <MultiFilterSelect placeholder="全部渠道" value={channels} options={options(data?.filters.channels || [])} onChange={value => { setChannels(value); setPage(1) }} />
          </label>
          <label>
            <span>地区</span>
            <MultiFilterSelect placeholder="全部地区" value={regions} options={options(data?.filters.regions || [])} onChange={value => { setRegions(value); setPage(1) }} />
          </label>
          <label>
            <span>批次</span>
            <MultiFilterSelect placeholder="全部批次" value={batchIds} options={(data?.filters.batches || []).map(item => ({ value: item.id, label: item.name }))} onChange={value => { setBatchIds(value); setPage(1) }} />
          </label>
          <Button type="primary" icon={<ReloadOutlined />} loading={loading} onClick={() => void load()}>刷新</Button>
        </div>
      </Card>
      {error ? <Alert className="qc-workload-alert" type="error" showIcon message="看板加载失败" description={error} /> : null}
      <section className="qc-workload-summary" aria-label="转人工指标">
        {cards.map(([label, value]) => (
          <Card key={label} size="small" className="qc-workload-metric">
            <Text className="qc-workload-metric-label">{label}</Text>
            <div className="qc-workload-metric-value">{value}</div>
          </Card>
        ))}
      </section>
      <Card className="qc-workload-trend-card" title="转人工类型 TOP" extra={<Text type="secondary">{SCOPE_OPTIONS.find(item => item.value === scope)?.label} · {data?.scoped_count || 0} 条</Text>}>
        <Spin spinning={loading}>
          {data?.reasons?.length ? (
            <div className="qc-handoff-rank" role="list">
              {data.reasons.map(item => (
                <button
                  key={item.reason_type}
                  type="button"
                  className={`qc-handoff-rank-row${reasonType === item.reason_type ? ' is-active' : ''}`}
                  onClick={() => { setReasonType(current => current === item.reason_type ? undefined : item.reason_type); setPage(1) }}
                >
                  <span className="qc-handoff-rank-name">
                    {item.reason_type}
                    {item.is_positive ? null : <Tag>非正向原因</Tag>}
                  </span>
                  <span className="qc-handoff-rank-bar" aria-hidden="true"><i style={{ width: `${Math.max(6, (item.count / maxCount) * 100)}%` }} /></span>
                  <span className="qc-handoff-rank-count">{item.count} · {percent(item.share)}</span>
                </button>
              ))}
            </div>
          ) : <Empty className="qc-workload-empty" description="当前筛选范围内没有转人工数据" />}
        </Spin>
      </Card>
      <Card className="qc-workload-trend-card" title={reasonType ? `切片明细 · ${reasonType}` : '切片明细'} extra={<Text type="secondary">点击类型可筛选</Text>}>
        <Table
          rowKey="id"
          size="small"
          loading={loading}
          dataSource={data?.slices || []}
          pagination={{ current: data?.page || page, pageSize: data?.page_size || 20, total: data?.total || 0, showSizeChanger: false, onChange: setPage }}
          locale={{ emptyText: '暂无切片' }}
          columns={[
            { title: '批次', dataIndex: 'batch_name', render: (value: string) => value || '—' },
            { title: '切片ID', dataIndex: 'slice_id', ellipsis: true },
            { title: '游戏', dataIndex: 'game', render: (value: string) => value || '—' },
            { title: '渠道', dataIndex: 'channel', width: 90, render: (value: string) => value || '—' },
            { title: '地区', dataIndex: 'region', width: 90, render: (value: string) => value || '—' },
            { title: '结论', dataIndex: ['human_handoff', 'decision'], width: 120, render: (value: string) => HANDOFF_DECISION_LABELS[value] || value || '—' },
            { title: '原因类型', dataIndex: ['human_handoff', 'reason_type'], ellipsis: true },
            { title: '操作', width: 70, render: (_: unknown, row: SliceRow) => <a onClick={() => onOpenDetail(row.id)}>详情</a> },
          ]}
        />
      </Card>
      <Text className="qc-workload-note" type="secondary">默认统计已转人工切片。附件和表情贴纸按协议视为必须转人工；历史切片在重跑分析前不会出现这两类。</Text>
    </div>
  )
}