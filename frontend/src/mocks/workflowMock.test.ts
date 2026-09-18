import { validateSliceResult } from '../types/workflow.js'
import { sliceInput, workflowMocks } from './sliceWorkflowMock.js'

const failures = workflowMocks.flatMap(mock => validateSliceResult(mock, mock.slice_id === sliceInput.slice_id ? sliceInput : undefined).map(error => `${mock.slice_id}: ${error}`))
if (failures.length) throw new Error(`Workflow mock contract validation failed:\n${failures.join('\n')}`)

const covered = new Set(workflowMocks.flatMap(mock => [
  mock.analysis_status,
  `issue:${mock.quality_check.has_issue}`,
  `decision:${mock.knowledge_suggestion.decision}`,
  `review:${mock.quality_check.issues.some(issue => issue.needs_manual_review) || mock.knowledge_suggestion.needs_manual_review}`,
]))

const requiredStates = [
  'completed', 'partial', 'failed', 'issue:true', 'issue:false',
  'decision:candidate_ready', 'decision:candidate_needs_enrichment', 'decision:not_candidate', 'decision:manual_review', 'decision:no_human_answer', 'review:true',
]
const missing = requiredStates.filter(state => !covered.has(state))
if (missing.length) throw new Error(`Workflow mock coverage missing: ${missing.join(', ')}`)

console.log(`Validated ${workflowMocks.length} workflow mocks; required contract states are covered.`)
