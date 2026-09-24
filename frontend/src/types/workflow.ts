/** Shared contract consumed by QC detail views and the external workflow. */
export type MessageRole = 'player' | 'ai' | 'human_agent' | 'system' | 'unknown'
export type MessageType = 'text' | 'image' | 'file' | 'audio' | 'video' | 'mixed' | 'unsupported'
export type TranslationStatus = 'pending' | 'processing' | 'success' | 'not_required' | 'uncertain' | 'failed'
export type AnalysisStatus = 'partial' | 'completed' | 'failed'
export type PlayerFeedbackType = 'explicit_acceptance' | 'weak_acceptance' | 'rejection' | 'no_feedback' | 'ambiguous'
export type CandidateType = 'new' | 'supplement' | 'correction' | 'covered' | 'reject' | 'manual_review'
export type CandidateCategory = 'gameplay_mechanics' | 'account' | 'recharge_payment' | 'item_reward' | 'event' | 'vip' | 'technical_issue' | 'game_progression' | 'social_guild' | 'policy_rule' | 'other'
export type Coverage = 'none' | 'partial' | 'full' | 'conflict' | 'unknown'

/** Final single-slice protocol. The input deliberately has no language field:
 * MaaS must detect language from the supplied messages itself. */
export type SliceIssueType = '答非所问' | '意图识别错误' | '无效回复' | '严重语法/乱码' | '语言错误' | '语气问题' | '疑似错误承诺'
export type SliceSeverity = '严重' | '中级' | '一般' | '需人工复核'
export type SliceKnowledgeDecision = 'candidate_ready' | 'candidate_needs_enrichment' | 'candidate_pending_feedback' | 'not_candidate' | 'manual_review' | 'no_human_answer'
export type SliceValidationStatus = 'player_validated' | 'unvalidated' | 'not_applicable'
export type SliceHandoffDecision = 'handoff_required' | 'handoff_reasonable' | 'handoff_not_required' | 'handoff_unreasonable' | 'manual_review'
export type SliceKnowledgeCategory = '游戏玩法' | '引导流程' | '活动规则' | '道具与奖励' | '账号与登录'
export type SliceCandidateType = SliceKnowledgeDecision
export type SliceSpeaker = 'player' | 'ai' | 'human_agent' | 'system' | 'unknown'
export interface SliceMessage {
  message_id: string
  speaker: SliceSpeaker
  speaker_source?: string | null
  content_type?: string | null
  event_type?: string | null
  event_label?: string | null
  text: string
  raw_content?: string | null
  structured_content?: Record<string, unknown> | unknown[] | null
  created_at?: string | null
  sequence?: number
}
export interface SliceInput {
  schema_version: '1.0.0'
  slice_id: string
  channel: string | null
  game: string | null
  region: string | null
  messages: SliceMessage[]
}
export interface SliceTextPair { original: string | null; zh_cn: string | null }
export interface QualityIssue {
  issue_id: string
  issue_type: SliceIssueType
  severity: SliceSeverity
  confidence: number
  ai_message_ids: string[]
  evidence_message_ids: string[]
  player_question: SliceTextPair
  ai_answer: SliceTextPair
  reason: string
  suggestion: string
  revised_reply: string | null
  revised_reply_zh_cn: string | null
  needs_manual_review: boolean
  manual_review_reason: string | null
}
export interface QualityCheck {
  has_issue: boolean
  issues: QualityIssue[]
}
export interface HumanHandoff {
  decision: SliceHandoffDecision
  handoff_occurred: boolean
  reason_type: string
  confidence: number
  evidence_message_ids: string[]
  reason: string
  needs_manual_review: boolean
  manual_review_reason: string | null
}
export interface KnowledgeSuggestion {
  answer_source: 'human_agent'
  decision: SliceKnowledgeDecision
  is_candidate: boolean
  validation_status: SliceValidationStatus
  confidence: number
  category: SliceKnowledgeCategory | null
  question_message_ids: string[]
  answer_message_ids: string[]
  feedback_message_ids: string[]
  evidence_message_ids: string[]
  title: string | null
  standard_questions: string[]
  standard_answer: string | null
  applicable_scope: { channel: string | null; game: string | null; region: string | null }
  reason: string
  reject_reason: string | null
  needs_manual_review: boolean
  manual_review_reason: string | null
}
export interface TermSuggestion {
  text: string
  zh_cn: string
}
export interface TermSuggestions {
  has_terms: boolean
  terms: TermSuggestion[]
}
export interface SliceAnalysisResult {
  schema_version: '1.0.0' | '2.0.0'
  slice_id: string
  analysis_status: AnalysisStatus
  messages: SliceMessage[]
  quality_check: QualityCheck
  human_handoff: HumanHandoff
  knowledge_suggestion: KnowledgeSuggestion
  term_suggestions: TermSuggestions
  warnings: string[]
  errors: string[]
}

export function validateSliceResult(result: SliceAnalysisResult, input?: SliceInput): string[] {
  const errors: string[] = []
  const sourceMessages = input?.messages ?? result.messages
  const ids = new Set(sourceMessages.map(m => m.message_id))
  const roles = new Map(sourceMessages.map(m => [m.message_id, m.speaker]))
  if (!result.slice_id) errors.push('slice_id is required')
  if (result.quality_check.issues.length > 3) errors.push('at most 3 quality issues are allowed')
  if (result.quality_check.has_issue !== (result.quality_check.issues.length > 0)) errors.push('has_issue must match issues length')
  const checkIds = (field: string, values: string[], role?: SliceSpeaker) => values.forEach(id => {
    if (!ids.has(id)) errors.push(`${field} references unknown message_id: ${id}`)
    else if (role && roles.get(id) !== role) errors.push(`${field} message ${id} must have role ${role}`)
  })
  result.quality_check.issues.forEach(issue => {
    if (!isConfidence(issue.confidence)) errors.push(`${issue.issue_id}: confidence must be between 0 and 1`)
    checkIds(`${issue.issue_id}.ai_message_ids`, issue.ai_message_ids, 'ai')
    checkIds(`${issue.issue_id}.evidence_message_ids`, issue.evidence_message_ids)
  })
  const suggestion = result.knowledge_suggestion
  if (suggestion.answer_source !== 'human_agent') errors.push('knowledge suggestion answer_source must be human_agent')
  if (!isConfidence(suggestion.confidence)) errors.push('knowledge confidence must be between 0 and 1')
  checkIds('knowledge.question_message_ids', suggestion.question_message_ids, 'player')
  checkIds('knowledge.answer_message_ids', suggestion.answer_message_ids, 'human_agent')
  checkIds('knowledge.evidence_message_ids', suggestion.evidence_message_ids)
  if (suggestion.is_candidate !== ['candidate_ready', 'candidate_needs_enrichment', 'candidate_pending_feedback'].includes(suggestion.decision)) errors.push('is_candidate must match decision')
  checkIds('knowledge.feedback_message_ids', suggestion.feedback_message_ids || [], 'player')
  if (suggestion.decision === 'candidate_ready' && (!suggestion.title || !suggestion.standard_answer || !suggestion.standard_questions.length)) errors.push('candidate_ready requires title, standard_questions and standard_answer')
  if (suggestion.decision === 'candidate_needs_enrichment' && !suggestion.title) errors.push('candidate_needs_enrichment requires title')
  if (suggestion.decision === 'not_candidate' && !suggestion.reject_reason) errors.push('not_candidate requires reject_reason')
  if (suggestion.decision === 'manual_review' && !suggestion.manual_review_reason) errors.push('manual_review requires manual_review_reason')
  if (suggestion.decision === 'no_human_answer' && suggestion.answer_message_ids.length) errors.push('no_human_answer cannot reference answer messages')
  return errors
}

export interface Translation { target_language: 'zh-CN'; translated_text: string | null; status: TranslationStatus; translation_version: string | null }
export interface Message { message_id: string; sequence: number; speaker_source: string | null; role: MessageRole; created_at: string | null; message_type: MessageType; original_text: string; source_language: string | null; translation: Translation }
export interface Conversation { conversation_id: string; business_line: string | null; game_code: string | null; game_name: string | null; region: string | null; channel: string | null; started_at: string | null; ended_at: string | null }
export interface RetrievalItem { knowledge_id: string; title: string | null; similarity: number; relevant_excerpt: string | null; knowledge_version: string | null; coverage: Coverage }
export interface KnowledgeRetrievalResult { query_id: string; query_message_ids: string[]; retrieval_status: 'success' | 'empty' | 'failed'; items: RetrievalItem[] }
export interface FrequencyContext { available: boolean; similar_question_count: number | null; time_window_days: number | null }
export interface PlayerFeedback { type: PlayerFeedbackType; confidence: number; evidence_message_ids: string[]; reason: string }
export interface AnswerQuality { status: 'pass' | 'needs_review' | 'fail'; confidence: number; answered_question: boolean; factually_supported: boolean; requires_backend_lookup: boolean; issues: string[] }
export interface CandidateClassification { primary_category: CandidateCategory; secondary_categories: CandidateCategory[]; category_confidence: number; tags: string[]; classification_reason: string }
export interface RelatedKnowledge { knowledge_id: string; title: string | null; similarity: number; coverage: Coverage; relevant_excerpt: string | null; knowledge_version: string | null; knowledge_gap: string | null }
export interface ProposedKnowledge { title: string; standard_questions: string[]; standard_answer: string; applicable_scope: { business_line?: string | null; games?: string[]; regions?: string[]; version_range?: string | null }; time_sensitive: boolean; expires_at: string | null }
export interface ManualReview { required: boolean; reason: string | null; risks?: string[]; items_to_confirm?: string[] }
export interface KnowledgeCandidate { is_candidate: boolean; candidate_type: CandidateType; confidence: number; reason_codes: string[]; reason: string; evidence_message_ids: string[]; classification: CandidateClassification | null; related_knowledge: RelatedKnowledge[]; proposed_knowledge: ProposedKnowledge | null; manual_review: ManualReview }
export interface QASegment { segment_id: string; question_message_ids: string[]; answer_message_ids: string[]; supporting_context_message_ids: string[]; feedback_message_ids: string[]; question: { original_text: string; zh_cn: string }; answer: { original_text: string; zh_cn: string }; player_feedback: PlayerFeedback; answer_quality: AnswerQuality; knowledge_candidate: KnowledgeCandidate }
export interface AnalysisResult { schema_version: '1.0.0'; request_id: string; conversation_id: string; analysis_status: AnalysisStatus; analysis_version: string; model: string | null; prompt_version: string; qa_segments: QASegment[]; unmatched_message_ids: string[]; warnings: string[]; errors: string[]; conversation: Conversation; messages: Message[]; knowledge_retrieval_results: KnowledgeRetrievalResult[]; frequency_context: FrequencyContext }

export const isConfidence = (value: number) => Number.isFinite(value) && value >= 0 && value <= 1

/** Lightweight referential checks for mock data and UI adapters. */
export function validateAnalysisResult(result: AnalysisResult): string[] {
  const errors: string[] = []
  const ids = new Map(result.messages.map(m => [m.message_id, m.role]))
  const checkIds = (field: string, values: string[], role?: MessageRole) => values.forEach(id => {
    if (!ids.has(id)) errors.push(`${field} references unknown message_id: ${id}`)
    else if (role && ids.get(id) !== role) errors.push(`${field} message ${id} must have role ${role}`)
  })
  result.messages.forEach(m => { if (!m.message_id || !m.original_text) errors.push('message_id and original_text are required'); if (m.translation.target_language !== 'zh-CN') errors.push(`unsupported translation target on ${m.message_id}`) })
  result.qa_segments.forEach(segment => {
    checkIds('question_message_ids', segment.question_message_ids, 'player'); checkIds('answer_message_ids', segment.answer_message_ids, 'ai'); checkIds('feedback_message_ids', segment.feedback_message_ids, 'player'); checkIds('supporting_context_message_ids', segment.supporting_context_message_ids); checkIds('evidence_message_ids', segment.knowledge_candidate.evidence_message_ids); checkIds('player_feedback.evidence_message_ids', segment.player_feedback.evidence_message_ids)
    if (!isConfidence(segment.player_feedback.confidence) || !isConfidence(segment.answer_quality.confidence) || !isConfidence(segment.knowledge_candidate.confidence)) errors.push(`${segment.segment_id}: confidence must be between 0 and 1`)
    const candidate = segment.knowledge_candidate
    if (candidate.is_candidate && !candidate.classification) errors.push(`${segment.segment_id}: classification required for candidate`)
    if ((candidate.candidate_type === 'new' || candidate.candidate_type === 'supplement') && !candidate.proposed_knowledge) errors.push(`${segment.segment_id}: proposed_knowledge required`)
    if ((candidate.candidate_type === 'covered' || candidate.candidate_type === 'reject') && candidate.proposed_knowledge) errors.push(`${segment.segment_id}: proposed_knowledge must be null`)
    if (candidate.candidate_type === 'correction' && !candidate.manual_review.required) errors.push(`${segment.segment_id}: correction requires manual review`)
  })
  return errors
}
