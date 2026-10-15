import { BACKEND_HANDOFF_REASON, HANDOFF_DETAIL_TYPES, handoffDetailLabel, handoffReviewLabel } from './handoffDetail.js'

function equal(actual: unknown, expected: unknown) {
  if (actual !== expected) throw new Error('Expected ' + JSON.stringify(expected) + ', got ' + JSON.stringify(actual))
}

for (const detail_type of HANDOFF_DETAIL_TYPES) {
  equal(handoffDetailLabel({ reason_type: BACKEND_HANDOFF_REASON, detail_type }), detail_type)
}
for (const detail_type of [undefined, null, '', ' ', '旧的未知标签', ' 充值金额查询 ', [], {}, 42, true]) {
  equal(handoffDetailLabel({ reason_type: BACKEND_HANDOFF_REASON, detail_type }), '未细分')
}
equal(handoffDetailLabel(undefined), '—')
equal(handoffDetailLabel({ reason_type: '玩家明确要求人工', detail_type: '充值金额查询' }), '—')

const classificationReview = {
  reason_type: BACKEND_HANDOFF_REASON,
  detail_type: '信息不足无法判断',
  decision: 'handoff_reasonable',
  needs_manual_review: true,
}
equal(handoffReviewLabel(classificationReview), '分类待复核')
equal(classificationReview.decision, 'handoff_reasonable')
equal(handoffReviewLabel({ ...classificationReview, decision: 'manual_review' }), '分类及转人工待复核')
equal(handoffReviewLabel({ decision: 'manual_review', needs_manual_review: true }), '转人工待复核')
equal(handoffReviewLabel({ ...classificationReview, needs_manual_review: false }), null)
equal(handoffReviewLabel({}), null)
equal(handoffReviewLabel(undefined), null)
console.log('Handoff detail compatibility and review display checks passed.')
