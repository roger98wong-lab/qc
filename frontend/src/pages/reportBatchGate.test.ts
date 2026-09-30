import {
  applyWorkbenchListTotal,
  createRequestGate,
  paginationTotalForTab,
  parseBatchIds,
  pickDefaultBatchId,
  reportBatchQuery,
  shouldFetchReportBatchData,
  workbenchExpectedKey,
  workbenchItemType,
} from './reportBatchGate.js'

if (parseBatchIds('6').join() !== '6') throw new Error('single url batch_id should parse')
if (parseBatchIds('6,5').join() !== '6,5') throw new Error('multi url batch_id should parse')
if (parseBatchIds('').length) throw new Error('empty url should have no batches')

if (shouldFetchReportBatchData([])) throw new Error('must not fetch before a batch is chosen')
if (!shouldFetchReportBatchData([6])) throw new Error('must fetch after a batch is chosen')

const defaultId = pickDefaultBatchId([
  { id: 6, status: 'uploading' },
  { id: 5, status: 'partial' },
])
if (defaultId !== 6) throw new Error('default batch should be the first non-failed row')

const calls: string[] = []
function fetchReport(batchIds: number[]) {
  if (!shouldFetchReportBatchData(batchIds)) throw new Error('first workbench/stats must not run without batch_ids')
  const query = reportBatchQuery(batchIds)
  if (!query) throw new Error('fetch must send batch_ids')
  calls.push(query)
}

let batchIds: number[] = parseBatchIds(null)
if (calls.length) throw new Error('no URL batch: must not fetch before default batch is written')
batchIds = defaultId ? [defaultId] : []
fetchReport(batchIds)
if (calls.length !== 1 || calls[0] !== '6') throw new Error('first workbench/stats must happen after default batch and include that batch_ids')

const gate = createRequestGate()
const staleAllBatches = gate.nextId()
const latestBatch = gate.nextId()
if (gate.shouldApply(staleAllBatches)) throw new Error('stale all-batch response must not be applied')
if (!gate.shouldApply(latestBatch)) throw new Error('latest batched response must be applied')

const batchQuery = reportBatchQuery([6])
if (!batchQuery) throw new Error('selected batch must produce batch_ids')
const kbKey = workbenchExpectedKey({
  batchQuery,
  itemType: workbenchItemType('kb'),
  page: 1,
  pageSize: 50,
})
const allBatchKey = workbenchExpectedKey({
  batchQuery: '',
  itemType: workbenchItemType('kb'),
  page: 1,
  pageSize: 50,
})
let totals = { issues: 0, kb: 0 }
if (applyWorkbenchListTotal({
  expectedKey: kbKey,
  actualKey: allBatchKey,
  itemType: workbenchItemType('kb'),
  dataTotal: 1042,
  previous: totals,
}) !== null) throw new Error('kb footer must ignore listItems total without matching batch_ids')

const kbApplied = applyWorkbenchListTotal({
  expectedKey: kbKey,
  actualKey: kbKey,
  itemType: workbenchItemType('kb'),
  dataTotal: 268,
  previous: totals,
})
if (!kbApplied) throw new Error('kb footer must use the matching listItems data.total')
totals = kbApplied
if (paginationTotalForTab('kb', totals) !== 268) throw new Error('kb footer total must equal that listItems data.total')
if (paginationTotalForTab('kb', totals) > 400) throw new Error('kb footer total must stay in the selected-batch range, not the all-batch scale')

const issueKey = workbenchExpectedKey({
  batchQuery,
  itemType: workbenchItemType('issues'),
  page: 1,
  pageSize: 50,
})
const issueApplied = applyWorkbenchListTotal({
  expectedKey: issueKey,
  actualKey: issueKey,
  itemType: workbenchItemType('issues'),
  dataTotal: 3,
  previous: totals,
})
if (!issueApplied) throw new Error('issues total must apply independently')
totals = issueApplied
if (paginationTotalForTab('issues', totals) !== 3) throw new Error('issues footer must use its own total')
if (paginationTotalForTab('kb', totals) !== 268) throw new Error('switching to issues must not overwrite kb total')

const staleAfterSwitch = applyWorkbenchListTotal({
  expectedKey: kbKey,
  actualKey: allBatchKey,
  itemType: workbenchItemType('kb'),
  dataTotal: 1042,
  previous: totals,
})
if (staleAfterSwitch !== null) throw new Error('switching back to kb must ignore all-batch totals')
if (paginationTotalForTab('kb', totals) === 1042) throw new Error('kb total must not return to all-batch scale')
if (paginationTotalForTab('kb', totals) !== 268) throw new Error('kb total must remain the selected-batch listItems total')

console.log('reportBatchGate ok')