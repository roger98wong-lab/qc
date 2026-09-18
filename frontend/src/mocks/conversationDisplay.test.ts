import { resolveTransferFormPresentation } from '../components/conversationDisplay.js'

const started = resolveTransferFormPresentation({
  content_type: '1',
  event_type: 'transfer_form_started',
  event_label: '转人工表单-引导填写',
  text: '[转人工表单-引导填写] text：请填写角色名',
  raw_content: '{"text":"请填写角色名"}',
  structured_content: { text: '请填写角色名' },
})
if (started.eventLabel !== '转人工表单-引导填写') throw new Error('contentType=1 should show the transfer-form label')
if (!started.entries.some(entry => entry.key === 'text' && entry.value === '请填写角色名')) throw new Error('contentType=1 should render readable fields')
if (started.displayText.includes('{')) throw new Error('contentType=1 should not display raw JSON as the only body')
if (!started.showRawToggle || !started.rawContent.includes('请填写角色名')) throw new Error('contentType=1 should keep inspectable raw content')

const submitted = resolveTransferFormPresentation({
  content_type: '3.0',
  event_type: 'transfer_form_submitted',
  text: '[转人工表单-已提交并生成工单] inquiries：账号无法登录',
  raw_content: '{"inquiries":"账号无法登录","ticketId":"123"}',
  structured_content: { inquiries: '账号无法登录', ticketId: '123' },
})
if (submitted.eventLabel !== '转人工表单-已提交并生成工单') throw new Error('contentType=3 should show the submitted-form label')
if (!submitted.entries.some(entry => entry.key === 'ticketId' && entry.value === '123')) throw new Error('contentType=3 should render dynamic form fields')
if (submitted.rawContent.split('\n').length < 2) throw new Error('raw JSON should be pretty-printed')

const legacy = resolveTransferFormPresentation({
  text: '普通消息',
})
if (legacy.eventLabel) throw new Error('old messages without new fields must stay unlabeled')
if (legacy.entries.length) throw new Error('old messages must not be treated as form fields')
if (legacy.displayText !== '普通消息') throw new Error('old messages should keep original text')
if (legacy.showRawToggle) throw new Error('old messages should not show a raw-content toggle')

const longField = resolveTransferFormPresentation({
  content_type: 1,
  structured_content: { 问题类型: '账号问题', 描述: 'x'.repeat(240) },
  raw_content: '{"问题类型":"账号问题"}',
  text: '[转人工表单-引导填写]',
})
if (longField.entries[1].value.length !== 240) throw new Error('long fields must be preserved for wrapping in the UI')

console.log('Validated transfer-form conversation display cases.')
