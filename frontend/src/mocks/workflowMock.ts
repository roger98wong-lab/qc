import type { AnalysisResult, CandidateCategory, Message, QASegment } from '../types/workflow.js'

const msg = (id: string, sequence: number, role: Message['role'], text: string, sourceLanguage: string | null, status: Message['translation']['status'] = sourceLanguage === 'zh-CN' ? 'not_required' : 'success', translated: string | null = null): Message => ({
  message_id: id, sequence, speaker_source: role, role, created_at: `2026-09-10T08:0${sequence}:00Z`, message_type: 'text', original_text: text, source_language: sourceLanguage,
  translation: { target_language: 'zh-CN', translated_text: translated ?? (sourceLanguage === 'zh-CN' ? null : text), status, translation_version: status === 'not_required' ? null : 'v1' },
})

const classification = (primary_category: CandidateCategory) => ({ primary_category, secondary_categories: [] as CandidateCategory[], category_confidence: 0.88, tags: [], classification_reason: '根据问题主题判断' })

export const workflowMock: AnalysisResult = {
  schema_version: '1.0.0', request_id: 'mock-request-001', conversation_id: 'conv-demo-001', analysis_status: 'partial', analysis_version: 'mock-analysis-1', model: null, prompt_version: 'mock-prompt-1',
  conversation: { conversation_id: 'conv-demo-001', business_line: '游戏', game_code: 'adventure', game_name: '冒险大作战', region: '欧美', channel: 'VK', started_at: '2026-09-10T08:01:00Z', ended_at: '2026-09-10T08:12:00Z' },
  messages: [
    msg('m1', 1, 'player', 'Wie kann ich den Bonus erhalten?', 'de', 'success', '我如何领取这个奖励？'),
    msg('m2', 2, 'ai', 'Öffnen Sie das Event und tippen Sie auf „Abholen“.', 'de', 'success', '打开活动并点击“领取”。'),
    msg('m3', 3, 'player', 'Danke, jetzt habe ich es verstanden.', 'de', 'success', '谢谢，我明白了。'),
    msg('m4', 4, 'player', 'Aber der Bonus ist noch nicht angekommen.', 'de', 'success', '但是奖励还没有到账。'),
    msg('m5', 5, 'human_agent', 'Ich prüfe den Kontostatus für Sie.', 'de', 'processing', null),
    msg('m6', 6, 'system', '订单查询处理中', 'zh-CN'),
    msg('m7', 7, 'unknown', '...', null, 'failed', null),
    msg('m8', 8, 'player', '充值失败怎么办？', 'zh-CN'),
    msg('m9', 9, 'ai', '请检查支付方式后重试。', 'zh-CN'),
  ],
  knowledge_retrieval_results: [{ query_id: 'q1', query_message_ids: ['m1'], retrieval_status: 'success', items: [{ knowledge_id: 'kb-1', title: '活动奖励领取', similarity: 0.62, relevant_excerpt: '活动页点击领取', knowledge_version: '2026.08', coverage: 'partial' }] }, { query_id: 'q2', query_message_ids: ['m8'], retrieval_status: 'failed', items: [] }],
  frequency_context: { available: false, similar_question_count: null, time_window_days: null },
  qa_segments: [
    { segment_id: 'seg-1', question_message_ids: ['m1'], answer_message_ids: ['m2'], supporting_context_message_ids: [], feedback_message_ids: ['m3', 'm4'], question: { original_text: 'Wie kann ich den Bonus erhalten?', zh_cn: '我如何领取这个奖励？' }, answer: { original_text: 'Öffnen Sie das Event und tippen Sie auf „Abholen“.', zh_cn: '打开活动并点击“领取”。' }, player_feedback: { type: 'rejection', confidence: 0.96, evidence_message_ids: ['m4'], reason: '感谢后继续表示奖励未到账' }, answer_quality: { status: 'needs_review', confidence: 0.68, answered_question: true, factually_supported: false, requires_backend_lookup: true, issues: ['可能需要查询订单状态'] }, knowledge_candidate: { is_candidate: true, candidate_type: 'supplement', confidence: 0.78, reason_codes: ['partial_coverage'], reason: '已有领取流程，缺少到账延迟说明', evidence_message_ids: ['m1', 'm2', 'm4'], classification: classification('item_reward'), related_knowledge: [], proposed_knowledge: { title: '活动奖励未到账排查', standard_questions: ['活动奖励为什么没有到账？'], standard_answer: '请确认领取成功后等待到账；仍未到账时提供订单信息。', applicable_scope: { games: ['冒险大作战'], regions: ['欧美'] }, time_sensitive: false, expires_at: null }, manual_review: { required: false, reason: null } } },
    { segment_id: 'seg-2', question_message_ids: ['m8'], answer_message_ids: ['m9'], supporting_context_message_ids: [], feedback_message_ids: [], question: { original_text: '充值失败怎么办？', zh_cn: '充值失败怎么办？' }, answer: { original_text: '请检查支付方式后重试。', zh_cn: '请检查支付方式后重试。' }, player_feedback: { type: 'no_feedback', confidence: 0.9, evidence_message_ids: [], reason: '没有后续玩家消息' }, answer_quality: { status: 'pass', confidence: 0.9, answered_question: true, factually_supported: true, requires_backend_lookup: false, issues: [] }, knowledge_candidate: { is_candidate: true, candidate_type: 'new', confidence: 0.84, reason_codes: ['generic_reusable'], reason: '通用充值失败排查流程', evidence_message_ids: ['m8', 'm9'], classification: classification('recharge_payment'), related_knowledge: [], proposed_knowledge: { title: '通用充值失败排查', standard_questions: ['充值失败如何处理？'], standard_answer: '请检查支付方式、网络及支付限额后重试。', applicable_scope: {}, time_sensitive: false, expires_at: null }, manual_review: { required: false, reason: null } } },
    { segment_id: 'seg-3', question_message_ids: ['m1'], answer_message_ids: [], supporting_context_message_ids: ['m7'], feedback_message_ids: [], question: { original_text: '账号被封怎么办？', zh_cn: '账号被封怎么办？' }, answer: { original_text: '', zh_cn: '' }, player_feedback: { type: 'ambiguous', confidence: 0.4, evidence_message_ids: [], reason: '缺少AI回复' }, answer_quality: { status: 'fail', confidence: 0.95, answered_question: false, factually_supported: false, requires_backend_lookup: true, issues: ['无AI答案'] }, knowledge_candidate: { is_candidate: false, candidate_type: 'reject', confidence: 0.91, reason_codes: ['personal_account_case'], reason: '具体账号封禁需人工核验，不沉淀为通用知识', evidence_message_ids: ['m1'], classification: null, related_knowledge: [], proposed_knowledge: null, manual_review: { required: true, reason: '需要核验账号状态' } } },
  ],
  unmatched_message_ids: ['m5', 'm6'], warnings: ['知识库检索失败：q2'], errors: [],
}

export const workflowMockFailed: AnalysisResult = { ...workflowMock, request_id: 'mock-request-failed', conversation_id: 'conv-failed', analysis_status: 'failed', qa_segments: [], unmatched_message_ids: workflowMock.messages.map(m => m.message_id), warnings: [], errors: ['外部工作流超时'] }

// Small deterministic variants keep the contract fixture broad without another UI-only data shape.
const variant = (request_id: string, candidateType: QASegment['knowledge_candidate']['candidate_type'], category: CandidateCategory, feedback: QASegment['player_feedback']['type']): AnalysisResult => {
  const base = JSON.parse(JSON.stringify(workflowMock)) as AnalysisResult
  base.request_id = request_id; base.conversation_id = `${request_id}-conversation`
  const q = msg(`${request_id}-q`, 1, 'player', 'Can I recover my account?', 'en', 'success', '我可以找回账号吗？')
  const a = msg(`${request_id}-a`, 2, 'ai', 'Follow the standard account recovery steps.', 'en', 'success', '请按标准账号找回流程操作。')
  base.messages = [q, a]; base.unmatched_message_ids = []; base.qa_segments = [base.qa_segments[0]]
  const segment = base.qa_segments[0]
  segment.segment_id = `${request_id}-segment`; segment.question_message_ids = [q.message_id]; segment.answer_message_ids = [a.message_id]; segment.feedback_message_ids = []
  segment.player_feedback.evidence_message_ids = []; segment.knowledge_candidate.evidence_message_ids = [q.message_id, a.message_id]
  segment.player_feedback.type = feedback; segment.player_feedback.evidence_message_ids = []; segment.knowledge_candidate.candidate_type = candidateType; segment.knowledge_candidate.is_candidate = !['covered', 'reject'].includes(candidateType); segment.knowledge_candidate.classification = segment.knowledge_candidate.is_candidate ? classification(category) : null
  segment.knowledge_candidate.proposed_knowledge = ['new', 'supplement'].includes(candidateType) ? { title: '通用账号找回流程', standard_questions: ['账号如何找回？'], standard_answer: '请使用绑定信息按流程找回账号。', applicable_scope: {}, time_sensitive: false, expires_at: null } : null
  segment.knowledge_candidate.manual_review = { required: candidateType === 'correction' || candidateType === 'manual_review', reason: candidateType === 'correction' ? '疑似现有知识错误' : candidateType === 'manual_review' ? '信息不足，需人工确认' : null }
  return base
}

export const workflowMocks = [
  workflowMock,
  workflowMockFailed,
  variant('mock-gameplay', 'covered', 'gameplay_mechanics', 'explicit_acceptance'),
  variant('mock-account', 'new', 'account', 'weak_acceptance'),
  variant('mock-refund', 'reject', 'recharge_payment', 'rejection'),
  variant('mock-correction', 'correction', 'technical_issue', 'ambiguous'),
  variant('mock-manual-review', 'manual_review', 'other', 'no_feedback'),
]
