import { useCallback, useEffect, useState } from 'react'
import { Alert, Button, Card, Form, Input, Modal, Select, Space, Typography, message } from 'antd'
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons'
import { dictionaryApi, reviewProcessingApi } from '../api'
import { useAuthStore } from '../store/auth'

const SCORE_OPTIONS = [60, 70, 80, 90].map(value => ({ value, label: String(value) }))
const EMPTY_DICTS = { knowledge_category: [] as Option[], knowledge_base: [] as Option[], issue_tag: [] as Option[], issue_type: [] as Option[] }
type Option = { value: string; label: string }

function displayName(value: unknown) {
  return value == null || value === '' ? '无' : String(value)
}

function kbPairs(row: any) {
  const pairs = row?.human_qa_pairs
  if (Array.isArray(pairs) && pairs.length) {
    return pairs.filter((item: any) => item && typeof item === 'object').map((item: any) => ({
      question: String(item.question || ''), answer: String(item.answer || ''),
    }))
  }
  const questions = Array.isArray(row?.maas_standard_questions) ? row.maas_standard_questions : []
  const answer = String(row?.maas_standard_answer || '')
  return questions.filter((item: unknown) => String(item || '').trim()).map((item: unknown) => ({ question: String(item), answer }))
}

function issuePairs(row: any, fallbackQuestion?: string, fallbackAnswer?: string) {
  const pairs = row?.human_qa_pairs
  if (Array.isArray(pairs) && pairs.length) {
    return pairs.filter((item: any) => item && typeof item === 'object').map((item: any) => ({
      question: String(item.question || ''), answer: String(item.answer || ''),
    }))
  }
  const questions = Array.isArray(row?.human_standard_questions) ? row.human_standard_questions : []
  if (questions.length) {
    return questions.map((item: unknown) => ({ question: String(item || ''), answer: String(row?.human_standard_answer || '') }))
  }
  return [{ question: String(fallbackQuestion || ''), answer: String(fallbackAnswer || '') }]
}

function scopeDefaults(row: any) {
  const human = row?.human_applicable_scope && typeof row.human_applicable_scope === 'object' ? row.human_applicable_scope : {}
  const hasHuman = ['channel', 'game', 'region'].some(field => String(human?.[field] || '').trim())
  if (hasHuman) {
    return {
      channel: String(human.channel || ''),
      game: String(human.game || ''),
      region: String(human.region || ''),
    }
  }
  return {
    channel: String(row?.channel || ''),
    game: String(row?.game || ''),
    region: String(row?.region || ''),
  }
}

function formValues(row: any, isIssue: boolean, fallbackQuestion?: string, fallbackAnswer?: string, categoryOptions: Option[] = []) {
  const questions = row?.human_standard_questions
  const hasHumanQuestions = Array.isArray(questions) ? questions.some((item: unknown) => String(item || '').trim()) : Boolean(String(questions || '').trim())
  const savedCategory = String(row?.human_category || '').trim()
  const maasCategory = String(row?.maas_category || (!isIssue ? row?.category : '') || '').trim()
  const allowed = new Set(categoryOptions.map(item => item.value))
  const defaultCategory = savedCategory || (allowed.has(maasCategory) ? maasCategory : '')
  return {
    ...row,
    human_category: isIssue ? row?.human_category : defaultCategory,
    human_applicable_scope: scopeDefaults(row),
    human_qa_pairs: isIssue ? issuePairs(row, hasHumanQuestions ? undefined : fallbackQuestion, fallbackAnswer) : kbPairs(row),
  }
}

function QaPairList() {
  return (
    <Form.List
      name="human_qa_pairs"
      rules={[{
        validator: async (_, value) => {
          if (!Array.isArray(value) || !value.length) throw new Error('请至少填写一组完整 QA 对')
        },
      }]}
    >
      {(fields, { add, remove }, { errors }) => (
        <>
          <div className="qc-qa-pair-list">
            {fields.map((field, index) => {
              const showGroupChrome = fields.length > 1
              return (
                <div key={field.key} className={showGroupChrome ? 'qc-qa-pair-card qc-qa-pair-card--grouped' : 'qc-qa-pair-card qc-qa-pair-card--plain'}>
                  {showGroupChrome ? (
                    <div className="qc-qa-pair-head">
                      <span>第 {index + 1} 组</span>
                      <Button type="text" danger size="small" icon={<DeleteOutlined />} onClick={() => remove(field.name)}>删除</Button>
                    </div>
                  ) : null}
                  <Form.Item label="问题（Q）" name={[field.name, 'question']} rules={[{ required: true, whitespace: true, message: `第 ${index + 1} 组 QA 的问题不能为空` }]}>
                    <Input.TextArea autoSize={{ minRows: 1, maxRows: 6 }} />
                  </Form.Item>
                  <Form.Item label="答案（A）" name={[field.name, 'answer']} rules={[{ required: true, whitespace: true, message: `第 ${index + 1} 组 QA 的答案不能为空` }]}>
                    <Input.TextArea autoSize={{ minRows: 2, maxRows: 8 }} />
                  </Form.Item>
                </div>
              )
            })}
          </div>
          <Form.ErrorList errors={errors} />
          <Button icon={<PlusOutlined />} onClick={() => add({ question: '', answer: '' })}>新增 QA</Button>
        </>
      )}
    </Form.List>
  )
}

function KnowledgeFields({ options, loading, categoryAfterQa = false }: { options: typeof EMPTY_DICTS; loading: boolean; categoryAfterQa?: boolean }) {
  const emptyCategory = !loading && !options.knowledge_category.length
  const emptyBase = !loading && !options.knowledge_base.length
  const categoryField = (
    <Form.Item label="知识分类" name="human_category" rules={[{ required: true, message: '请选择知识分类' }]}>
      <Select loading={loading} disabled={emptyCategory} options={options.knowledge_category} placeholder="选择人工审核分类" />
    </Form.Item>
  )
  const qaBlock = (
    <>
      <Typography.Title level={5}>待录入 Q/A</Typography.Title>
      <QaPairList />
    </>
  )
  return (
    <section className="qc-knowledge-qa-section" aria-labelledby="knowledge-qa-title">
      <Typography.Title level={5} id="knowledge-qa-title">知识库优化信息</Typography.Title>
      {(emptyBase || emptyCategory) && (
        <Alert className="qc-dictionary-state" type="warning" showIcon message="现在没有可选分类" description="请让管理员先配好知识库选项和知识分类后再保存。" />
      )}
      <Form.Item label="知识库选项" name="human_knowledge_base" rules={[{ required: true, message: '请选择知识库选项' }]}>
        <Select loading={loading} disabled={emptyBase} options={options.knowledge_base} placeholder="选择知识库分组" />
      </Form.Item>
      {categoryAfterQa ? <>{qaBlock}{categoryField}</> : <>{categoryField}{qaBlock}</>}
      <section className="qc-scope-section" aria-labelledby="scope-title">
        <Typography.Text strong id="scope-title">适用范围</Typography.Text>
        <div className="qc-scope-grid">
          <Form.Item label="渠道" name={['human_applicable_scope', 'channel']} rules={[{ required: true, whitespace: true, message: '请填写渠道' }]}>
            <Input />
          </Form.Item>
          <Form.Item label="游戏" name={['human_applicable_scope', 'game']} rules={[{ required: true, whitespace: true, message: '请填写游戏' }]}>
            <Input />
          </Form.Item>
          <Form.Item label="地区" name={['human_applicable_scope', 'region']} rules={[{ required: true, whitespace: true, message: '请填写地区' }]}>
            <Input />
          </Form.Item>
        </div>
      </section>
    </section>
  )
}

export default function ReviewProcessingPanel({
  itemType, itemId, initial, defaultQualityQuestionZhCn, defaultQualityAnswerZhCn, onSaved,
}: {
  itemType: 'quality_issue' | 'knowledge_suggestion'
  itemId: number
  initial?: any
  defaultQualityQuestionZhCn?: string
  defaultQualityAnswerZhCn?: string
  onSaved?: (value: any) => void
}) {
  const [form] = Form.useForm()
  const [saving, setSaving] = useState(false)
  const [loading, setLoading] = useState(false)
  const [current, setCurrent] = useState<any>(initial || null)
  const [dicts, setDicts] = useState(EMPTY_DICTS)
  const [dictsLoading, setDictsLoading] = useState(true)
  const [dictError, setDictError] = useState<string | null>(null)
  const user = useAuthStore(state => state.user)
  const isIssue = itemType === 'quality_issue'
  const tags = Form.useWatch('human_tags', form) || []
  const showKnowledge = isIssue && tags.includes('知识库优化')

  const applyRow = useCallback((row: any) => {
    setCurrent(row)
    form.setFieldsValue(formValues(row, isIssue, defaultQualityQuestionZhCn, defaultQualityAnswerZhCn, dicts.knowledge_category))
  }, [form, isIssue, defaultQualityQuestionZhCn, defaultQualityAnswerZhCn, dicts.knowledge_category])

  useEffect(() => {
    let active = true
    setDictsLoading(true)
    setDictError(null)
    Promise.all((Object.keys(EMPTY_DICTS) as Array<keyof typeof EMPTY_DICTS>).map(group => dictionaryApi.list(group))).then(results => {
      if (!active) return
      const next = { ...EMPTY_DICTS }
      ;(Object.keys(EMPTY_DICTS) as Array<keyof typeof EMPTY_DICTS>).forEach((group, index) => {
        next[group] = (results[index].data || []).map((row: any) => ({ value: row.value, label: row.display_name || row.value }))
      })
      setDicts(next)
    }).catch((error: any) => {
      if (active) setDictError(error?.response?.data?.detail || '配置加载失败，请刷新后重试；当前只能显示空白可选项')
    }).finally(() => active && setDictsLoading(false))
    return () => { active = false }
  }, [])

  useEffect(() => {
    let active = true
    setLoading(true)
    reviewProcessingApi.get(itemType, itemId).then(result => {
      if (active) applyRow(result.data)
    }).catch((error: any) => {
      if (active) message.error(error?.response?.data?.detail || '审核处理数据加载失败')
    }).finally(() => active && setLoading(false))
    return () => { active = false }
  }, [itemType, itemId, applyRow])

  useEffect(() => {
    if (initial) applyRow(initial)
  }, [initial, applyRow])

  useEffect(() => {
    if (current) applyRow(current)
  }, [applyRow])

  const successToast = (row: any) => {
    const decision = String(row?.maas_decision || row?.decision || '')
    const enteredPool = Boolean(row?.entered_knowledge_pool || row?.knowledge_pool_status)
    if (!isIssue && decision === 'manual_review') return '已保存'
    if (enteredPool) return '审核已通过，已进入一键 QA'
    return '审核已通过'
  }

  const approve = async () => {
    if (saving) return
    try {
      const values = await form.validateFields()
      setSaving(true)
      const saved = await reviewProcessingApi.save(itemType, itemId, { ...values, review_action: 'approve' })
      const row = saved.data || {}
      applyRow(row)
      onSaved?.(row)
      message.success(successToast(row))
    } catch (error: any) {
      if (error?.errorFields?.length) {
        const field = error.errorFields[0]?.name
        if (field) form.scrollToField(field, { behavior: 'smooth', block: 'center' })
        message.warning('请先补全必填项')
        return
      }
      message.error(error?.response?.data?.detail || '通过审核失败')
    } finally {
      setSaving(false)
    }
  }

  const reject = () => {
    if (saving) return
    Modal.confirm({
      title: '确认不通过？',
      content: isIssue ? '将标记为审核不通过，不进入一键 QA。' : '将标记为审核不通过，不进入一键 QA 池。',
      okText: '确定',
      cancelText: '取消',
      okButtonProps: { danger: true },
      onOk: async () => {
        setSaving(true)
        try {
          const comment = form.getFieldValue('human_review_comment')
          const saved = await reviewProcessingApi.save(itemType, itemId, {
            review_action: 'reject',
            human_review_comment: comment ?? '',
          })
          const row = saved.data || {}
          applyRow(row)
          onSaved?.(row)
          message.success('已标记为不通过')
        } catch (error: any) {
          message.error(error?.response?.data?.detail || '标记不通过失败')
          throw error
        } finally {
          setSaving(false)
        }
      },
    })
  }

  return (
    <Card
      size="small"
      title="审核处理"
      extra={<Typography.Text type="secondary">当前处理人：{displayName(user?.username)}</Typography.Text>}
      className="qc-review-processing-panel"
      loading={loading}
    >
      <Form form={form} layout="vertical" initialValues={{ human_tags: [], human_review_status: 'pending_review', human_standard_questions: [], human_qa_pairs: [] }}>
        <div className="qc-review-form-body">
        {dictError && <Alert className="qc-dictionary-state" type="error" showIcon message="审核配置加载失败" description={dictError} />}
        {isIssue && (
          <Form.Item label="问题标签" name="human_tags">
            <Select mode="multiple" loading={dictsLoading} disabled={!!dictError || (!dictsLoading && !dicts.issue_tag.length)} options={dicts.issue_tag} placeholder="选择问题标签" />
          </Form.Item>
        )}
        {isIssue ? (
          <>
            <Form.Item label="AI 回复评分" name="human_ai_score" rules={[{ type: 'enum', enum: [60, 70, 80, 90], message: '请选择固定分值档位' }]}>
              <Select allowClear disabled={current?.has_ai_answer === false} options={SCORE_OPTIONS} placeholder={current?.has_ai_answer === false ? '无 AI 回复，不能评分' : '选择评分'} />
            </Form.Item>
            <Form.Item label="问题类型确认" name="human_issue_type" rules={[{ required: true, message: '请选择问题类型' }]}>
              <Select loading={dictsLoading} disabled={!!dictError || (!dictsLoading && !dicts.issue_type.length)} options={dicts.issue_type} />
            </Form.Item>
            {showKnowledge && <KnowledgeFields options={dicts} loading={dictsLoading} categoryAfterQa />}
          </>
        ) : (
          <KnowledgeFields options={dicts} loading={dictsLoading} />
        )}
        <Form.Item label="处理备注" name="human_review_comment">
          <Input.TextArea autoSize={{ minRows: 2, maxRows: 4 }} placeholder="填写补充内容、修改原因或退回原因" />
        </Form.Item>
        </div>
        <div className="qc-review-actions" role="group" aria-label="审核操作">
          <Space wrap>
            <Button className="qc-review-approve" loading={saving} disabled={saving} onClick={approve}>通过审核</Button>
            <Button danger loading={saving} disabled={saving} onClick={reject}>不通过</Button>
          </Space>
        </div>
      </Form>
    </Card>
  )
}
