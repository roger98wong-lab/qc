export const BACKEND_HANDOFF_REASON = '需要人工权限或后台处理'

// Keep the read/display whitelist identical to backend/handoff_taxonomy.py.
// Whitespace and unknown legacy values belong to 未细分, just as in TOP aggregation.
export const HANDOFF_DETAIL_TYPES = [
  '充值金额查询',
  'VIP认证',
  '兑换社区互动码',
  '普通礼包码咨询',
  'VIP专属福利码',
  '合服转服跨服',
  '功能或数值Bug',
  '活动排期或资格咨询',
  '玩法建议或功能请求',
  '活动奖励未发放需核实后台日志',
  '账号绑定找回解绑',
  '确认规则机制',
  '充值未到账或支付异常',
  '客户端闪退或无法进入',
  '信息不足无法判断',
  '资源操作',
  '角色或道具数据异常需核实后台日志',
  '举报他人违规作弊',
  '记录转达跟进',
  '其他需后台处理'
] as const

const detailTypes = new Set<string>(HANDOFF_DETAIL_TYPES)

type HandoffDisplay = {
  reason_type?: string
  detail_type?: unknown
  decision?: string
  needs_manual_review?: boolean
  manual_review_reason?: unknown
}

export function handoffDetailLabel(handoff?: HandoffDisplay | null): string {
  if (handoff?.reason_type !== BACKEND_HANDOFF_REASON) return '—'
  return typeof handoff.detail_type === 'string' && detailTypes.has(handoff.detail_type)
    ? handoff.detail_type
    : '未细分'
}

export function handoffReviewLabel(handoff?: HandoffDisplay | null): string | null {
  if (handoff?.needs_manual_review !== true) return null
  const classificationReview = handoff.reason_type === BACKEND_HANDOFF_REASON
    && handoff.detail_type === '信息不足无法判断'
  if (classificationReview && handoff.decision === 'manual_review') return '分类及转人工待复核'
  return classificationReview ? '分类待复核' : '转人工待复核'
}
