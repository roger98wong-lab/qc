import { useEffect, useState, type Key } from 'react'
import { useSearchParams } from 'react-router-dom'
import dayjs, { type Dayjs } from 'dayjs'
import {
  Card, Table, Button, Form, Input, Select, Modal, message, DatePicker,
  Tabs, Descriptions, Alert, Typography, Space, Tag, Switch, Popconfirm, Tooltip, Collapse, Empty, Badge,
} from 'antd'
import { PlusOutlined, SaveOutlined, EditOutlined, DeleteOutlined, SearchOutlined, ReloadOutlined, FilterOutlined, DownloadOutlined, SyncOutlined } from '@ant-design/icons'
import { authApi, adminApi } from '../api'
import { useAuthStore } from '../store/auth'
import { formatConversationTime } from '../components/Conversation'

const { Title, Text, Paragraph } = Typography
const { RangePicker } = DatePicker
const AUDIT_WINDOW_DAYS = 30
const defaultAuditRange = (): [Dayjs, Dayjs] => [dayjs().subtract(AUDIT_WINDOW_DAYS, 'day'), dayjs()]
const dash = (value?: string | null) => {
  const text = value == null ? '' : String(value).trim()
  return text || '—'
}
const filenameFromDisposition = (header?: string) => {
  const match = String(header || '').match(/filename="?([^";]+)"?/)
  return match ? decodeURIComponent(match[1]) : `操作日志_${dayjs().format('YYYYMMDD_HHmmss')}.xlsx`
}

const MATCH_FIELD_OPTIONS = [
  { value: 'none', label: '无' },
  { value: 'page_id', label: '页面 ID' },
  { value: 'language', label: '语言' },
  { value: 'gameProductId', label: '游戏产品 ID' },
]

function matchFieldLabel(value?: string) {
  return MATCH_FIELD_OPTIONS.find(option => option.value === value)?.label || value || '无'
}

function matchFieldOptions(values: string[] = []) {
  const seen = new Set(MATCH_FIELD_OPTIONS.map(option => option.value))
  return [
    ...MATCH_FIELD_OPTIONS,
    ...values.filter(value => value && !seen.has(value)).map(value => ({ value, label: value })),
  ]
}

export default function Admin() {
  const user = useAuthStore(s => s.user)
  const isAdmin = user?.role === 'admin'
  const [searchParams, setSearchParams] = useSearchParams()

  const [users, setUsers] = useState<any[]>([])
  const [config, setConfig] = useState<any>({})
  const [backups, setBackups] = useState<any[]>([])
  const [mappings, setMappings] = useState<any[]>([])
  const [mappingLoading, setMappingLoading] = useState(false)
  const [mappingError, setMappingError] = useState<string | null>(null)
  const [mappingOptionsError, setMappingOptionsError] = useState<string | null>(null)
  const [mappingOptionsLoading, setMappingOptionsLoading] = useState(false)
  const [mappingOptions, setMappingOptions] = useState<any>({ raw_regions: [], raw_channels: [], games: [], match_fields: [], target_channels: [], target_regions: [], statuses: [] })
  const [mappingFilters, setMappingFilters] = useState<Record<string, any>>({})
  const [mappingPagination, setMappingPagination] = useState({ current: 1, pageSize: 20, total: 0 })
  const [selectedMappingKeys, setSelectedMappingKeys] = useState<Key[]>([])
  const [mappingRowLoadingId, setMappingRowLoadingId] = useState<number | null>(null)
  const [applyMappingLoading, setApplyMappingLoading] = useState(false)
  const [dictionaries, setDictionaries] = useState<any[]>([])
  const [dictionaryLoading, setDictionaryLoading] = useState(false)
  const [dictionaryError, setDictionaryError] = useState<string | null>(null)
  const [dictionaryGroup, setDictionaryGroup] = useState<string>('knowledge_category')
  const [dictionaryModal, setDictionaryModal] = useState(false)
  const [dictionaryEditing, setDictionaryEditing] = useState<any>(null)
  const [addModal, setAddModal] = useState(false)
  const [mappingModal, setMappingModal] = useState(false)
  const [mappingEditing, setMappingEditing] = useState<any>(null)
  const [pwdModal, setPwdModal] = useState(false)
  const [editUserModal, setEditUserModal] = useState(false)
  const [editingUser, setEditingUser] = useState<any>(null)
  const [resetPwdModal, setResetPwdModal] = useState(false)
  const [resetPwdUser, setResetPwdUser] = useState<any>(null)
  const [form] = Form.useForm()
  const [pwdForm] = Form.useForm()
  const [editUserForm] = Form.useForm()
  const [resetPwdForm] = Form.useForm()
  const [mappingForm] = Form.useForm()
  const mappingMatchField = Form.useWatch('match_field', mappingForm)
  const isGameProductMapping = mappingMatchField === 'gameProductId'
  const [mappingFilterForm] = Form.useForm()
  const [configForm] = Form.useForm()
  const [dictionaryForm] = Form.useForm()
  const [pendingCount, setPendingCount] = useState(0)
  const [adminTab, setAdminTab] = useState(searchParams.get('tab') || 'profile')
  const [auditLogs, setAuditLogs] = useState<any[]>([])
  const [auditLoading, setAuditLoading] = useState(false)
  const [auditExporting, setAuditExporting] = useState(false)
  const [auditError, setAuditError] = useState<string | null>(null)
  const [auditOptions, setAuditOptions] = useState<{ actions: any[]; target_types: any[]; operators: any[] }>({ actions: [], target_types: [], operators: [] })
  const [auditFilters, setAuditFilters] = useState<{ start?: string; end?: string; operator_id?: number; action?: string; target_type?: string }>({})
  const [auditPagination, setAuditPagination] = useState({ current: 1, pageSize: 20, total: 0 })
  const [auditForm] = Form.useForm()
  const [aiConfigs, setAiConfigs] = useState<any[]>([])
  const [aiConfigLoading, setAiConfigLoading] = useState(false)
  const [aiConfigError, setAiConfigError] = useState<string | null>(null)
  const [aiConfigOptions, setAiConfigOptions] = useState<any>({ games: [], regions: [], analysis_statuses: [], statuses: [] })
  const [aiConfigFilters, setAiConfigFilters] = useState<Record<string, any>>({})
  const [aiConfigPagination, setAiConfigPagination] = useState({ current: 1, pageSize: 20, total: 0 })
  const [aiConfigModal, setAiConfigModal] = useState(false)
  const [aiConfigEditing, setAiConfigEditing] = useState<any>(null)
  const [aiConfigRowLoadingId, setAiConfigRowLoadingId] = useState<number | null>(null)
  const [aiConfigForm] = Form.useForm()
  const [aiConfigFilterForm] = Form.useForm()

  const dictionaryGroups = [
    { value: 'knowledge_category', label: '知识分类' }, { value: 'knowledge_base', label: '知识库选项' },
    { value: 'issue_tag', label: '问题标签' }, { value: 'issue_type', label: '问题类型' },
  ]

  const loadDictionaries = async () => {
    setDictionaryLoading(true)
    setDictionaryError(null)
    try { setDictionaries((await adminApi.listDictionaries(dictionaryGroup)).data || []) }
    catch (err: any) {
      setDictionaries([])
      setDictionaryError(err.response?.data?.detail || '配置项加载失败，请稍后重试。')
    } finally { setDictionaryLoading(false) }
  }

  const auditQueryParams = (filters = auditFilters, page = auditPagination.current, pageSize = auditPagination.pageSize) => {
    const range = defaultAuditRange()
    return {
      start: filters.start || range[0].format('YYYY-MM-DDTHH:mm:ss'),
      end: filters.end || range[1].format('YYYY-MM-DDTHH:mm:ss'),
      operator_id: filters.operator_id,
      action: filters.action,
      target_type: filters.target_type,
      page,
      page_size: pageSize,
    }
  }

  const loadAuditOptions = async () => {
    try { setAuditOptions((await adminApi.getAuditLogOptions()).data) }
    catch { /* keep last known options */ }
  }

  const loadAuditLogs = async (filters = auditFilters, page = auditPagination.current, pageSize = auditPagination.pageSize) => {
    setAuditLoading(true)
    setAuditError(null)
    try {
      const response = await adminApi.listAuditLogs(auditQueryParams(filters, page, pageSize))
      const data = response.data || {}
      setAuditLogs(data.items || [])
      setAuditPagination({ current: data.page || page, pageSize: data.page_size || pageSize, total: data.total || 0 })
    } catch (err: any) {
      setAuditLogs([])
      setAuditPagination(current => ({ ...current, current: page, pageSize, total: 0 }))
      setAuditError(err.response?.data?.detail || '操作日志加载失败，请检查权限或网络后重试。')
    } finally { setAuditLoading(false) }
  }

  const handleAuditSearch = async (values: Record<string, any>) => {
    const range: [Dayjs, Dayjs] | undefined = values.range
    const filters = {
      start: range?.[0]?.format('YYYY-MM-DDTHH:mm:ss'),
      end: range?.[1]?.format('YYYY-MM-DDTHH:mm:ss'),
      operator_id: values.operator_id,
      action: values.action,
      target_type: values.target_type,
    }
    setAuditFilters(filters)
    await loadAuditLogs(filters, 1, auditPagination.pageSize)
  }

  const resetAuditFilters = async () => {
    const range = defaultAuditRange()
    auditForm.setFieldsValue({ range, operator_id: undefined, action: undefined, target_type: undefined })
    const filters = { start: range[0].format('YYYY-MM-DDTHH:mm:ss'), end: range[1].format('YYYY-MM-DDTHH:mm:ss') }
    setAuditFilters(filters)
    await loadAuditLogs(filters, 1, auditPagination.pageSize)
  }

  const exportAuditLogs = async () => {
    if (auditExporting) return
    setAuditExporting(true)
    try {
      const params = auditQueryParams()
      delete (params as any).page
      delete (params as any).page_size
      const response = await adminApi.exportAuditLogs(params)
      const blob = new Blob([response.data], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' })
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = filenameFromDisposition(response.headers?.['content-disposition'])
      link.click()
      URL.revokeObjectURL(url)
      message.success('已导出操作日志')
    } catch (err: any) {
      let detail = err.response?.data?.detail
      if (err.response?.data instanceof Blob) {
        try { detail = JSON.parse(await err.response.data.text()).detail } catch { detail = undefined }
      }
      message.error(detail || '导出失败，请检查权限或缩小筛选范围后重试')
    } finally { setAuditExporting(false) }
  }

  const loadPendingCount = async () => {
    try {
      const data = (await adminApi.getMappingPendingCount()).data
      const count = Number(data?.count)
      if (Number.isFinite(count)) setPendingCount(count)
    } catch { /* keep last known count */ }
  }

  const loadMappingOptions = async () => {
    setMappingOptionsLoading(true)
    setMappingOptionsError(null)
    try { setMappingOptions((await adminApi.getMappingOptions()).data) }
    catch (err: any) { setMappingOptionsError(err.response?.data?.detail || '筛选选项加载失败，请稍后重试。') }
    finally { setMappingOptionsLoading(false) }
  }

  const loadMappings = async (filters = mappingFilters, page = mappingPagination.current, pageSize = mappingPagination.pageSize) => {
    if (mappingLoading) return
    setMappingLoading(true)
    setMappingError(null)
    try {
      const response = await adminApi.listMappings({ ...filters, page, page_size: pageSize })
      const data = response.data
      setMappings(Array.isArray(data) ? data : (data.items || []))
      setMappingPagination({ current: Array.isArray(data) ? 1 : data.page, pageSize: Array.isArray(data) ? pageSize : data.page_size, total: Array.isArray(data) ? data.length : data.total })
      setSelectedMappingKeys([])
    } catch (err: any) {
      setMappings([])
      setMappingPagination(current => ({ ...current, current: page, pageSize, total: 0 }))
      setMappingError(err.response?.data?.detail || '映射规则查询失败，请检查网络后重试。')
    } finally { setMappingLoading(false) }
  }

  const loadAiConfigOptions = async () => {
    try { setAiConfigOptions((await adminApi.getGameAiConfigOptions()).data) }
    catch { /* keep last known options */ }
  }

  const loadAiConfigs = async (filters = aiConfigFilters, page = aiConfigPagination.current, pageSize = aiConfigPagination.pageSize) => {
    if (aiConfigLoading) return
    setAiConfigLoading(true)
    setAiConfigError(null)
    try {
      const response = await adminApi.listGameAiConfigs({ ...filters, page, page_size: pageSize })
      const data = response.data
      setAiConfigs(Array.isArray(data) ? data : (data.items || []))
      setAiConfigPagination({ current: Array.isArray(data) ? 1 : data.page, pageSize: Array.isArray(data) ? pageSize : data.page_size, total: Array.isArray(data) ? data.length : data.total })
    } catch (err: any) {
      setAiConfigs([])
      setAiConfigPagination(current => ({ ...current, current: page, pageSize, total: 0 }))
      setAiConfigError(err.response?.data?.detail || 'AI 配置查询失败，请检查网络后重试。')
    } finally { setAiConfigLoading(false) }
  }

  const handleAiConfigSearch = async (values: Record<string, any>) => {
    const filters = Object.fromEntries(Object.entries(values).filter(([, value]) => value !== undefined && value !== null && value !== ''))
    setAiConfigFilters(filters)
    await loadAiConfigs(filters, 1, aiConfigPagination.pageSize)
  }

  const resetAiConfigFilters = async () => {
    aiConfigFilterForm.resetFields()
    setAiConfigFilters({})
    await loadAiConfigs({}, 1, aiConfigPagination.pageSize)
  }

  const openAiConfigModal = (row?: any) => {
    setAiConfigEditing(row || null)
    aiConfigForm.setFieldsValue(row ? { ...row, analysis_enabled: row.analysis_enabled !== false, enabled: row.enabled !== false } : { analysis_enabled: true, enabled: true })
    setAiConfigModal(true)
  }

  const handleSaveAiConfig = async (values: any) => {
    const payload = { ...values, analysis_enabled: values.analysis_enabled !== false, enabled: values.enabled !== false }
    try {
      if (aiConfigEditing) await adminApi.updateGameAiConfig(aiConfigEditing.id, payload)
      else await adminApi.createGameAiConfig(payload)
      setAiConfigModal(false)
      aiConfigForm.resetFields()
      await Promise.all([loadAiConfigs(), loadAiConfigOptions()])
      message.success(aiConfigEditing ? 'AI 配置已更新，仅对后续新分析任务生效' : 'AI 配置已新增，仅对后续新分析任务生效')
    } catch (err: any) {
      message.error(err.response?.data?.detail || '保存 AI 配置失败')
    }
  }

  const toggleAiConfigField = async (row: any, field: 'analysis_enabled' | 'enabled', value: boolean) => {
    setAiConfigRowLoadingId(row.id)
    try {
      await adminApi.updateGameAiConfig(row.id, { ...row, [field]: value })
      await loadAiConfigs(aiConfigFilters, aiConfigPagination.current, aiConfigPagination.pageSize)
      message.success('已保存，仅对后续新分析任务生效，不会重跑历史切片')
    } catch (err: any) {
      message.error(err.response?.data?.detail || '更新 AI 配置失败')
    } finally { setAiConfigRowLoadingId(null) }
  }

  const handleDeleteAiConfig = async (row: any) => {
    try {
      await adminApi.deleteGameAiConfig(row.id)
      const remainingOnPage = aiConfigs.length - 1
      const nextPage = remainingOnPage === 0 && aiConfigPagination.current > 1 ? aiConfigPagination.current - 1 : aiConfigPagination.current
      await Promise.all([loadAiConfigs(aiConfigFilters, nextPage, aiConfigPagination.pageSize), loadAiConfigOptions()])
      message.success('AI 配置已删除，历史切片状态不会改变')
    } catch (err: any) { message.error(err.response?.data?.detail || '删除 AI 配置失败') }
  }

  useEffect(() => {
    if (isAdmin) {
      authApi.listUsers().then(r => setUsers(r.data))
      adminApi.getConfig().then(r => setConfig(r.data)).catch(() => message.error('读取系统配置失败，请刷新后重试'))
      adminApi.listBackups().then(r => setBackups(r.data))
      loadMappingOptions()
      loadPendingCount()
      const pending = searchParams.get('pending') === '1' || searchParams.get('tab') === 'mappings' && searchParams.get('pending') === '1'
      const initialFilters = pending ? { enabled: false, pending: true } : {}
      if (pending) {
        mappingFilterForm.setFieldsValue({ enabled: false })
        setMappingFilters(initialFilters)
        setAdminTab('mappings')
      }
      loadMappings(initialFilters, 1, mappingPagination.pageSize)
      loadAiConfigOptions()
      loadAiConfigs({}, 1, aiConfigPagination.pageSize)
      loadDictionaries()
    }
  }, [isAdmin, dictionaryGroup])

  useEffect(() => {
    if (!isAdmin || adminTab !== 'logs') return
    loadAuditOptions()
    const range = defaultAuditRange()
    auditForm.setFieldsValue({ range, operator_id: undefined, action: undefined, target_type: undefined })
    const auditInit = { start: range[0].format('YYYY-MM-DDTHH:mm:ss'), end: range[1].format('YYYY-MM-DDTHH:mm:ss') }
    setAuditFilters(auditInit)
    void loadAuditLogs(auditInit, 1, auditPagination.pageSize)
  }, [isAdmin, adminTab])

  const handleMappingSearch = async (values: Record<string, any>) => {
    const filters = Object.fromEntries(Object.entries(values).filter(([, value]) => value !== undefined && value !== null && value !== ''))
    setMappingFilters(filters)
    await loadMappings(filters, 1, mappingPagination.pageSize)
  }

  const resetMappingFilters = async () => {
    mappingFilterForm.resetFields()
    setMappingFilters({})
    setSelectedMappingKeys([])
    await loadMappings({}, 1, mappingPagination.pageSize)
  }

  const isPendingMapping = (row: any) => !row?.enabled && String(row?.remark || '').startsWith('[待补充映射]')

  const reportMappingBatch = (enabled: boolean, data: any) => {
    const success = Number(data?.success_count || 0)
    const skipped = Number(data?.skipped_count || 0)
    const failed = Number(data?.failed_count || 0)
    const verb = enabled ? '启用' : '停用'
    if (failed === 0 && skipped === 0) {
      message.success(`已${verb} ${success} 条`)
      return
    }
    message.warning(`成功 ${success} · 跳过 ${skipped} · 失败 ${failed}`)
    const reasons = (data?.results || []).filter((row: any) => !row.success && row.reason).slice(0, 5)
    reasons.forEach((row: any) => message.warning(`#${row.id} ${row.reason}`))
    if ((data?.results || []).filter((row: any) => !row.success && row.reason).length > 5) {
      message.warning('其余见结果')
    }
  }

  const applyMappingEnabled = async (ids: number[], enabled: boolean) => {
    if (!ids.length) return
    if (ids.length === 1) setMappingRowLoadingId(ids[0])
    const singleRow = ids.length === 1 ? mappings.find(row => row.id === ids[0]) : null
    try {
      const data = (await adminApi.batchSetMappingEnabled(ids, enabled)).data
      reportMappingBatch(enabled, data)
      await Promise.all([loadMappings(mappingFilters, mappingPagination.current, mappingPagination.pageSize), loadPendingCount()])
      if (
        enabled
        && ids.length === 1
        && Number(data?.success_count || 0) === 1
        && singleRow?.match_field === 'gameProductId'
        && String(singleRow.match_value || '').trim()
      ) {
        Modal.confirm({
          title: '是否立刻回写该产品 ID？',
          content: `只改游戏仍等于 ${String(singleRow.match_value).trim()} 的记录；覆盖游戏名和标准地区；不重跑分析。选否只影响后续上传。`,
          okText: '立刻回写',
          cancelText: '稍后再说',
          onOk: () => applyGameProductMappings([String(singleRow.match_value).trim()]),
        })
      }
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '更新映射状态失败')
    } finally {
      setMappingRowLoadingId(null)
    }
  }

  const confirmMappingEnabled = (ids: number[], enabled: boolean, single = false) => {
    if (!ids.length) return
    const count = ids.length
    Modal.confirm({
      title: enabled ? (single ? '确认启用该映射？' : `确认启用选中的 ${count} 条映射？`) : (single ? '确认停用该映射？' : `确认停用选中的 ${count} 条映射？`),
      content: single ? (enabled ? '启用后立即对后续上传生效。' : '停用后不再匹配后续上传。') : '仅对勾选行生效；已是目标状态的会跳过。',
      okText: enabled ? '启用' : '停用',
      okButtonProps: enabled ? { className: 'qc-mapping-enable-ok' } : { danger: true },
      cancelText: '取消',
      onOk: () => applyMappingEnabled(ids, enabled),
    })
  }

  const toggleMappingRow = (row: any) => {
    if (mappingRowLoadingId) return
    if (!row.enabled && isPendingMapping(row)) {
      message.warning('请先编辑补全后再启用')
      return
    }
    confirmMappingEnabled([row.id], !row.enabled, true)
  }

  useEffect(() => {
    if (Object.keys(config).length > 0) configForm.setFieldsValue({ maas_base_url: config.maas_base_url || '', concurrency: config.concurrency ?? 5 })
  }, [config, configForm])

  const reloadUsers = () => authApi.listUsers().then(r => setUsers(r.data))

  const handleAddUser = async (values: any) => {
    try {
      await authApi.createUser(values)
      message.success('用户创建成功')
      setAddModal(false)
      form.resetFields()
      await reloadUsers()
    } catch (err: any) {
      message.error(err.response?.data?.detail || '创建失败')
    }
  }

  const openEditUser = (row: any) => {
    setEditingUser(row)
    editUserForm.setFieldsValue({ username: row.username, email: row.email, role: row.role })
    setEditUserModal(true)
  }

  const handleEditUser = async (values: any) => {
    if (!editingUser) return
    try {
      const res = await authApi.updateUser(editingUser.id, values)
      if (res.data?.relogin_required) {
        message.warning('角色已更新，请重新登录后生效')
      } else {
        message.success('用户已更新')
      }
      setEditUserModal(false)
      await reloadUsers()
    } catch (err: any) {
      message.error(err.response?.data?.detail || '保存用户失败')
    }
  }

  const handleToggle = async (row: any) => {
    try {
      const res = await authApi.toggleUser(row.id)
      message.success(res.data?.message || '状态已更新')
      await reloadUsers()
    } catch (err: any) {
      message.error(err.response?.data?.detail || '更新状态失败')
    }
  }

  const openResetPwd = (row: any) => {
    setResetPwdUser(row)
    resetPwdForm.resetFields()
    setResetPwdModal(true)
  }

  const handleResetPwd = async (values: any) => {
    if (!resetPwdUser) return
    if (values.new_password !== values.confirm_password) {
      message.error('两次输入的密码不一致')
      return
    }
    try {
      await authApi.resetPassword(resetPwdUser.id, values.new_password)
      message.success('密码已重置，该用户下次登录必须先改密')
      setResetPwdModal(false)
    } catch (err: any) {
      message.error(err.response?.data?.detail || '重置密码失败')
    }
  }

  const handleDeleteUser = async (row: any) => {
    try {
      const res = await authApi.deleteUser(row.id)
      message.success('用户已删除')
      if (res.data?.self || row.id === user?.id) {
        localStorage.removeItem('token')
        localStorage.removeItem('user')
        window.location.href = '/login'
        return
      }
      await reloadUsers()
    } catch (err: any) {
      message.error(err.response?.data?.detail || '删除用户失败')
    }
  }

  const userStatusTag = (row: any) => {
    if (row.status === 'pending_disable') {
      const at = formatConversationTime(row.disable_effective_at)
      return <Tag color="warning">将于 {at ? at.slice(11, 16) : '稍后'} 停用</Tag>
    }
    if (row.status === 'disabled' || row.is_active === false) return <Tag>已停用</Tag>
    return <Tag color="success">启用</Tag>
  }

  const handleChangePwd = async (values: any) => {
    try {
      await authApi.changePassword(values.old_password, values.new_password)
      message.success('密码修改成功')
      setPwdModal(false)
    } catch (err: any) {
      message.error(err.response?.data?.detail || '修改失败')
    }
  }

  const handleBackup = async () => {
    try {
      const r = await adminApi.backup()
      message.success(r.data.message)
      adminApi.listBackups().then(r => setBackups(r.data))
    } catch {
      message.error('备份失败')
    }
  }

  const waitForBackend = async (timeoutMs = 30000) => {
    const started = Date.now()
    await new Promise(resolve => window.setTimeout(resolve, 1500))
    while (Date.now() - started < timeoutMs) {
      try {
        const response = await fetch('/api/health', { cache: 'no-store' })
        if (response.ok) return true
      } catch {
        // process is still down
      }
      await new Promise(resolve => window.setTimeout(resolve, 800))
    }
    return false
  }

  const restartBackend = async () => {
    message.loading({ content: '正在重启后端，请稍候…', key: 'backend-restart', duration: 0 })
    try {
      await adminApi.restart()
    } catch {
      // The current process exits right after responding; a dropped request is expected.
    }
    const up = await waitForBackend()
    if (!up) {
      message.error({ content: '重启超时。请手动刷新页面，或重新运行 start.bat。', key: 'backend-restart' })
      return
    }
    try {
      const r = await adminApi.getConfig()
      setConfig(r.data)
      message.success({ content: '后端已重启，新配置已生效', key: 'backend-restart' })
    } catch {
      message.success({ content: '后端已重启，请刷新页面确认配置', key: 'backend-restart' })
    }
  }

  const handleSaveConfig = async (values: any) => {
    try {
      const payload: Record<string, unknown> = { concurrency: values.concurrency }
      const baseUrl = String(values.maas_base_url || '').trim()
      const appKey = String(values.maas_app_key || '').trim()
      if (baseUrl) payload.maas_base_url = baseUrl
      if (appKey) payload.maas_app_key = appKey
      const r = await adminApi.updateConfig(payload)
      setConfig(r.data)
      configForm.setFieldsValue({ maas_base_url: r.data.maas_base_url || '', maas_app_key: '', concurrency: r.data.concurrency })
      message.success(r.data.message || '配置已保存并立即生效')
    } catch (err: any) { message.error(err.response?.data?.detail || '保存系统配置失败') }
  }

  const openMappingModal = (mapping?: any) => {
    setMappingEditing(mapping || null)
    const next = mapping
      ? { ...mapping, match_field: mapping.match_field || 'none', enabled: mapping.enabled !== false }
      : { match_field: 'none', enabled: true }
    if (next.match_field === 'gameProductId') next.raw_region = undefined
    mappingForm.setFieldsValue(next)
    setMappingModal(true)
  }

  const reportApplyMapping = (data: any) => {
    const slices = Number(data?.slices_updated || 0)
    const sessions = Number(data?.sessions_updated || 0)
    const issues = Number(data?.issues_updated || 0)
    const kb = Number(data?.kb_updated || 0)
    message.success(`已回写切片 ${slices} 条，会话 ${sessions}，问题 ${issues}，知识库 ${kb}`)
  }

  const applyGameProductMappings = async (matchValues?: string[]) => {
    setApplyMappingLoading(true)
    try {
      const data = (await adminApi.applyGameProductIds(matchValues)).data
      reportApplyMapping(data)
      await Promise.all([loadMappings(mappingFilters, mappingPagination.current, mappingPagination.pageSize), loadMappingOptions(), loadPendingCount()])
      return data
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '回写已有切片失败')
      throw err
    } finally {
      setApplyMappingLoading(false)
    }
  }

  const confirmApplyGameProductMappings = () => {
    Modal.confirm({
      title: '按当前映射回写已有切片？',
      content: '只改游戏仍等于产品 ID 的记录；覆盖游戏名和标准地区；不重跑分析；已审核/分析中也只改展示字段。',
      okText: '开始回写',
      cancelText: '取消',
      onOk: () => applyGameProductMappings(),
    })
  }

  const promptApplySavedProductId = (matchValue?: string | null) => {
    const productId = String(matchValue || '').trim()
    if (!productId) return
    Modal.confirm({
      title: '是否立刻回写该产品 ID？',
      content: `只改游戏仍等于 ${productId} 的记录；覆盖游戏名和标准地区；不重跑分析。选否只影响后续上传。`,
      okText: '立刻回写',
      cancelText: '稍后再说',
      onOk: () => applyGameProductMappings([productId]),
    })
  }

  const handleSaveMapping = async (values: any) => {
    const payload = Object.fromEntries(Object.entries(values).map(([key, value]) => [key, value === '' ? null : value]))
    if (payload.match_field === 'gameProductId') payload.raw_region = null
    try {
      if (mappingEditing) await adminApi.updateMapping(mappingEditing.id, payload)
      else await adminApi.createMapping(payload)
      setMappingModal(false)
      mappingForm.resetFields()
      await Promise.all([loadMappings(), loadMappingOptions(), loadPendingCount()])
      message.success(mappingEditing ? '映射已更新' : '映射已新增')
      if (payload.match_field === 'gameProductId' && payload.enabled !== false) {
        promptApplySavedProductId(String(payload.match_value || ''))
      }
    } catch (err: any) {
      message.error(err.response?.data?.detail || '保存映射失败')
    }
  }

  const handleDeleteMapping = async (row: any) => {
    try {
      await adminApi.deleteMapping(row.id)
      const remainingOnPage = mappings.length - 1
      const nextPage = remainingOnPage === 0 && mappingPagination.current > 1 ? mappingPagination.current - 1 : mappingPagination.current
      await Promise.all([loadMappings(mappingFilters, nextPage, mappingPagination.pageSize), loadMappingOptions(), loadPendingCount()])
      message.success('映射已删除')
    } catch (err: any) { message.error(err.response?.data?.detail || '删除映射失败') }
  }

  const openDictionaryModal = (row?: any) => {
    setDictionaryEditing(row || null)
    dictionaryForm.setFieldsValue(row ? { ...row, group: row.group } : { group: dictionaryGroup, sort_order: 10, enabled: true })
    setDictionaryModal(true)
  }

  const handleSaveDictionary = async (values: any) => {
    try {
      const payload = { ...values, sort_order: Number(values.sort_order), enabled: values.enabled !== false }
      if (dictionaryEditing) await adminApi.updateDictionary(dictionaryEditing.id, payload)
      else await adminApi.createDictionary(payload)
      setDictionaryModal(false); dictionaryForm.resetFields(); await loadDictionaries()
      message.success(dictionaryEditing ? '配置项已更新' : '配置项已新增')
    } catch (err: any) { message.error(err.response?.data?.detail || '保存配置项失败') }
  }

  const disableDictionary = async (row: any) => {
    try { await adminApi.disableDictionary(row.id); await loadDictionaries(); message.success('配置项已停用；历史记录仍保留原值') }
    catch (err: any) { message.error(err.response?.data?.detail || '停用配置项失败') }
  }

  const deleteDictionary = async (row: any) => {
    try { await adminApi.deleteDictionary(row.id); await loadDictionaries(); message.success('配置项已删除') }
    catch (err: any) { message.error(err.response?.data?.detail || '删除配置项失败') }
  }

  const userColumns = [
    { title: '用户名', dataIndex: 'username' },
    { title: '邮箱', dataIndex: 'email' },
    { title: '角色', dataIndex: 'role', render: (v: string) => <Tag color={v === 'admin' ? 'red' : 'blue'}>{v === 'admin' ? '管理员' : '质检员'}</Tag> },
    { title: '状态', dataIndex: 'status', width: 140, render: (_: any, row: any) => userStatusTag(row) },
    { title: '更新时间', dataIndex: 'updated_at', width: 170, render: (v: string) => formatConversationTime(v) || '—' },
    {
      title: '操作', width: 280, render: (_: any, row: any) => (
        <Space size={4} wrap>
          <Button type="link" size="small" icon={<EditOutlined />} onClick={() => openEditUser(row)}>编辑</Button>
          <Button type="link" size="small" onClick={() => openResetPwd(row)}>重置密码</Button>
          <Button type="link" size="small" onClick={() => handleToggle(row)}>{(row.status === 'pending_disable' || row.status === 'disabled' || row.is_active === false) ? '启用' : '停用'}</Button>
          <Popconfirm
            title={`确认删除用户「${row.username}」？`}
            description={row.id === user?.id
              ? '将删除你自己的账号，成功后立即退出登录。如果这是最后一个管理员，系统将没有人能继续管理。此操作不可恢复。'
              : row.role === 'admin'
                ? '将删除管理员账号。如果这是最后一个管理员，系统将没有人能继续管理。此操作不可恢复，历史审核记录不会被删除。'
                : '删除后不可恢复。历史审核记录、切片和操作日志不会被删除。'}
            okText="删除"
            cancelText="取消"
            okButtonProps={{ danger: true }}
            onConfirm={() => handleDeleteUser(row)}
          >
            <Button type="link" danger size="small" icon={<DeleteOutlined />}>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  const tabs: any[] = [
    {
      key: 'profile', label: '个人设置',
      children: (
        <div style={{ maxWidth: 400 }}>
          <Descriptions column={1} style={{ marginBottom: 24 }}>
            <Descriptions.Item label="用户名">{user?.username}</Descriptions.Item>
            <Descriptions.Item label="角色">{user?.role === 'admin' ? '管理员' : '质检员'}</Descriptions.Item>
          </Descriptions>
          <Button onClick={() => setPwdModal(true)}>修改密码</Button>
        </div>
      ),
    },
  ]

  if (isAdmin) {
    tabs.push(
      {
        key: 'users', label: '用户管理',
        children: (
          <div>
            <div className="qc-mapping-toolbar">
              <Text className="qc-mapping-help">可编辑、删除其他管理员和自己。停用后 5 分钟内仍可登录；重置密码后对方下次登录必须先改密。</Text>
              <Button type="primary" icon={<PlusOutlined />} onClick={() => setAddModal(true)}>新建用户</Button>
            </div>
            <Table className="qc-mapping-table" rowKey="id" dataSource={users} columns={userColumns} size="small" scroll={{ x: 900 }} />
          </div>
        ),
      },
      {
        key: 'system', label: '系统配置',
        children: (
          <div className="qc-system-config-page">
            <Alert
              className="qc-system-config-alert"
              type={config.maas_configured ? 'success' : 'warning'}
              message={config.maas_configured ? '分析服务已配置，可以正常分析' : '分析服务未配置，无法进行 AI 分析'}
              description={
                <div>
                  <Paragraph>如需配置，请在当前运行环境的 <Text code>{config.env_path || 'backend/.env'}</Text> 文件中填写：</Paragraph>
                  <pre>
{`MAAS_BASE_URL=分析服务地址
MAAS_APP_KEY=分析密钥`}
                  </pre>
                  <Text type="secondary">保存后立即写入 backend/.env 并在当前后端进程生效。App Key 留空表示保持现有密钥，不会清空。</Text>
                </div>
              }
              style={{ marginBottom: 24 }}
            />
            <div className="qc-system-config-grid">
              <Card size="small" className="qc-system-config-form" title="连接与分析参数">
                <Form form={configForm} layout="vertical" onFinish={handleSaveConfig} initialValues={{ maas_base_url: config.maas_base_url || '', maas_app_key: '', concurrency: config.concurrency || 5 }}>
                  <Form.Item label="分析服务地址" name="maas_base_url"><Input placeholder="填写分析服务地址，例如公司内部的接口地址" /></Form.Item>
                  <Form.Item label="分析密钥" name="maas_app_key" extra={<span className="qc-system-config-help">不会回显现有 Key。需要更换时输入新 Key；留空表示保持现有配置。请勿将 Key 粘贴到截图、日志或工单中。</span>}>
                    <Input.Password autoComplete="new-password" placeholder="请输入新的分析密钥" />
                  </Form.Item>
                  <Form.Item label="并发分析数" name="concurrency" extra="取值范围 1–100，数字越大吞吐越高，也会增加分析压力。" getValueFromEvent={(event) => Number(event.target.value)} rules={[{ required: true, type: 'number', min: 1, max: 100, message: '请输入 1-100 的整数' }]}>
                    <Input type="number" min={1} max={100} />
                  </Form.Item>
                  <Space align="center" wrap>
                    <Button type="primary" htmlType="submit">保存配置</Button>
                    <Text type="secondary">空的 App Key 表示保持现有密钥，不会清空。</Text>
                  </Space>
                </Form>
              </Card>
              <Card size="small" className="qc-system-config-summary" title="当前配置状态">
                <Descriptions column={1} bordered>
                  <Descriptions.Item label="分析服务地址">{config.maas_base_url || '未配置'}</Descriptions.Item>
                  <Descriptions.Item label="分析密钥已设置">{config.maas_key_set ? '是' : '否'}</Descriptions.Item>
                  <Descriptions.Item label="并发分析数">{config.concurrency}</Descriptions.Item>
                </Descriptions>
                <Text type="secondary">密钥仅显示是否已设置，不会在页面回显。</Text>
              </Card>
            </div>
          </div>
        ),
      },
      {
        key: 'mappings', label: <span className="qc-mapping-tab-label">地区渠道映射{pendingCount > 0 ? <span className="qc-mapping-tab-count">{pendingCount > 99 ? '99+' : pendingCount}</span> : null}</span>,
        children: (
          <div className="qc-mapping-page">
            <div className="qc-mapping-toolbar">
              <Text className="qc-mapping-help">空白条件表示任意值；规则按列表顺序匹配，新增后立即对后续上传生效。</Text>
              <Space wrap>
                <Button className="qc-mapping-batch-enable" disabled={!selectedMappingKeys.length} onClick={() => confirmMappingEnabled(selectedMappingKeys.map(Number), true)}>批量启用</Button>
                <Button danger disabled={!selectedMappingKeys.length} onClick={() => confirmMappingEnabled(selectedMappingKeys.map(Number), false)}>批量停用</Button>
                <Button className="qc-mapping-pending-btn" onClick={async () => {
                  const filters = { enabled: false, pending: true }
                  mappingFilterForm.setFieldsValue({ enabled: false, raw_region: undefined, raw_channel: undefined, game: undefined, match_field: undefined, match_value: undefined, target_channel: undefined, target_region: undefined })
                  setMappingFilters(filters)
                  await loadMappings(filters, 1, mappingPagination.pageSize)
                }}>待补充{pendingCount > 0 ? <span className="qc-mapping-tab-count">{pendingCount > 99 ? '99+' : pendingCount}</span> : null}</Button>
                <Button icon={<SyncOutlined />} loading={applyMappingLoading} onClick={confirmApplyGameProductMappings}>按当前映射回写已有切片</Button>
                <Button type="primary" icon={<PlusOutlined />} onClick={() => openMappingModal()}>新增映射</Button>
              </Space>
            </div>
            {mappingOptionsError && <Alert className="qc-mapping-state" type="warning" showIcon message="筛选选项加载失败" description={mappingOptionsError} action={<Button size="small" onClick={loadMappingOptions}>重新加载</Button>} />}
            <Collapse
              className="qc-mapping-filter-collapse"
              defaultActiveKey={['filters']}
              items={[{
                key: 'filters',
                label: <Space><FilterOutlined /><span>筛选条件</span>{Object.keys(mappingFilters).length > 0 && <Tag color="blue">已生效 {Object.keys(mappingFilters).length} 项</Tag>}</Space>,
                children: <Form form={mappingFilterForm} layout="vertical" onFinish={handleMappingSearch} className="qc-mapping-filter-form">
                  <div className="qc-mapping-filter-grid">
                    <Form.Item label="原始地区" name="raw_region"><Select allowClear showSearch optionFilterProp="label" loading={mappingOptionsLoading} placeholder="全部地区" notFoundContent={mappingOptionsLoading ? '加载中…' : '暂无可选地区'} options={(mappingOptions.raw_regions || []).map((value: string) => ({ value, label: value }))} /></Form.Item>
                    <Form.Item label="原始渠道" name="raw_channel"><Select allowClear showSearch optionFilterProp="label" loading={mappingOptionsLoading} placeholder="全部渠道" notFoundContent={mappingOptionsLoading ? '加载中…' : '暂无可选渠道'} options={(mappingOptions.raw_channels || []).map((value: string) => ({ value, label: value }))} /></Form.Item>
                    <Form.Item label="游戏" name="game"><Input allowClear placeholder="输入游戏名称，支持模糊搜索" onPressEnter={() => mappingFilterForm.submit()} /></Form.Item>
                    <Form.Item label="匹配字段" name="match_field"><Select allowClear loading={mappingOptionsLoading} placeholder="全部字段" notFoundContent={mappingOptionsLoading ? '加载中…' : '暂无可选字段'} options={matchFieldOptions(mappingOptions.match_fields)} /></Form.Item>
                    <Form.Item label="匹配值" name="match_value"><Input allowClear placeholder="输入匹配值，支持模糊搜索" onPressEnter={() => mappingFilterForm.submit()} /></Form.Item>
                    <Form.Item label="标准渠道" name="target_channel"><Select allowClear showSearch optionFilterProp="label" loading={mappingOptionsLoading} placeholder="全部渠道" notFoundContent={mappingOptionsLoading ? '加载中…' : '暂无可选渠道'} options={(mappingOptions.target_channels || []).map((value: string) => ({ value, label: value }))} /></Form.Item>
                    <Form.Item label="标准地区" name="target_region"><Select allowClear showSearch optionFilterProp="label" loading={mappingOptionsLoading} placeholder="全部地区" notFoundContent={mappingOptionsLoading ? '加载中…' : '暂无可选地区'} options={(mappingOptions.target_regions || []).map((value: string) => ({ value, label: value }))} /></Form.Item>
                    <Form.Item label="状态" name="enabled"><Select allowClear loading={mappingOptionsLoading} placeholder="全部状态" options={mappingOptions.statuses || []} /></Form.Item>
                  </div>
                  <div className="qc-mapping-filter-actions">
                    <Button type="primary" htmlType="submit" icon={<SearchOutlined />} loading={mappingLoading} disabled={mappingLoading}>查询</Button>
                    <Button icon={<ReloadOutlined />} onClick={resetMappingFilters} disabled={mappingLoading}>重置</Button>
                  </div>
                </Form>,
              }]}
            />
            {mappingError && <Alert className="qc-mapping-state" type="error" showIcon message="映射规则查询失败" description={mappingError} action={<Button size="small" onClick={() => loadMappings()}>重试</Button>} />}
            <Table className="qc-mapping-table" rowKey="id" dataSource={mappings} loading={mappingLoading} size="small" scroll={{ x: 1100 }} rowSelection={{ selectedRowKeys: selectedMappingKeys, onChange: setSelectedMappingKeys }} pagination={{ current: mappingPagination.current, pageSize: mappingPagination.pageSize, total: mappingPagination.total, showSizeChanger: true, showTotal: total => `共 ${total} 条`, onChange: (page, pageSize) => loadMappings(mappingFilters, page, pageSize) }} locale={{ emptyText: mappingError ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="查询失败，请点击上方“重试”" /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前筛选条件没有匹配规则"><Button type="link" onClick={resetMappingFilters}>重置筛选</Button></Empty> }} columns={[
              { title: '原始地区', dataIndex: 'raw_region', render: (v: string) => v || <Tag className="qc-any-tag">任意</Tag> },
              { title: '原始渠道', dataIndex: 'raw_channel', render: (v: string) => v || <Tag className="qc-any-tag">任意</Tag> },
              { title: '游戏', dataIndex: 'game', render: (v: string) => v || <Tag className="qc-any-tag">任意</Tag> },
              { title: '匹配字段', dataIndex: 'match_field', render: (v: string) => matchFieldLabel(v) },
              { title: '匹配值', dataIndex: 'match_value', render: (v: string) => v || <Tag className="qc-any-tag">任意</Tag> },
              { title: '标准渠道', dataIndex: 'target_channel' },
              { title: '标准地区', dataIndex: 'target_region', render: (v: string) => v || '—' },
              { title: '状态', dataIndex: 'enabled', width: 96, render: (v: boolean, row: any) => (
                <Tooltip title={v ? '点击停用' : (isPendingMapping(row) ? '请先编辑补全后再启用' : '点击启用')}>
                  <Button
                    size="small"
                    className={v ? 'qc-mapping-status-on' : 'qc-mapping-status-off'}
                    loading={mappingRowLoadingId === row.id}
                    onClick={() => toggleMappingRow(row)}
                  >{v ? '启用' : '停用'}</Button>
                </Tooltip>
              ) },
              { title: '操作', fixed: 'right' as const, width: 130, render: (_: any, row: any) => <Space size={4}><Button type="link" size="small" icon={<EditOutlined />} onClick={() => openMappingModal(row)}>编辑</Button><Popconfirm title="确认删除这条映射？" description="删除后不可恢复，且不会影响已处理的历史数据。" onConfirm={() => handleDeleteMapping(row)} okText="删除" cancelText="取消" okButtonProps={{ danger: true }}><Button type="link" danger size="small" icon={<DeleteOutlined />}>删除</Button></Popconfirm></Space> },
            ]} />
          </div>
        ),
      },
      {
        key: 'game-ai', label: '游戏 AI 分析',
        children: (
          <div className="qc-mapping-page">
            <Alert className="qc-mapping-state" type="info" showIcon message="按标准游戏 + 标准地区控制是否调用 MaaS" description="保存配置不会重跑历史切片、不会修改审核结论，也不会中断正在分析的任务。未匹配或关闭的游戏地区在新上传时跳过 AI 分析，原始切片和对话仍会保留。" />
            <div className="qc-mapping-toolbar">
              <Text className="qc-mapping-help">匹配切片已经标准化后的 game / region，不使用原始游戏 ID、raw_region 或渠道。渠道不同但标准游戏+地区相同，共用一条配置。</Text>
              <Button type="primary" icon={<PlusOutlined />} onClick={() => openAiConfigModal()}>新增配置</Button>
            </div>
            <Collapse className="qc-mapping-filter-collapse" defaultActiveKey={['filters']} items={[{
              key: 'filters',
              label: <span><FilterOutlined /> 筛选</span>,
              children: (
                <Form form={aiConfigFilterForm} layout="vertical" onFinish={handleAiConfigSearch}>
                  <div className="qc-mapping-filter-grid">
                    <Form.Item label="标准游戏" name="game"><Select allowClear showSearch optionFilterProp="label" placeholder="全部游戏" options={(aiConfigOptions.games || []).map((value: string) => ({ value, label: value }))} /></Form.Item>
                    <Form.Item label="标准地区" name="region"><Select allowClear showSearch optionFilterProp="label" placeholder="全部地区" options={(aiConfigOptions.regions || []).map((value: string) => ({ value, label: value }))} /></Form.Item>
                    <Form.Item label="AI 分析" name="analysis_enabled"><Select allowClear placeholder="全部" options={aiConfigOptions.analysis_statuses || []} /></Form.Item>
                    <Form.Item label="配置状态" name="enabled"><Select allowClear placeholder="全部状态" options={aiConfigOptions.statuses || []} /></Form.Item>
                  </div>
                  <div className="qc-mapping-filter-actions">
                    <Button type="primary" htmlType="submit" icon={<SearchOutlined />} loading={aiConfigLoading}>查询</Button>
                    <Button icon={<ReloadOutlined />} onClick={resetAiConfigFilters} disabled={aiConfigLoading}>重置</Button>
                  </div>
                </Form>
              ),
            }]} />
            {aiConfigError && <Alert className="qc-mapping-state" type="error" showIcon message="AI 配置查询失败" description={aiConfigError} action={<Button size="small" onClick={() => loadAiConfigs()}>重试</Button>} />}
            <Table className="qc-mapping-table" rowKey="id" dataSource={aiConfigs} loading={aiConfigLoading} size="small" scroll={{ x: 900 }} pagination={{ current: aiConfigPagination.current, pageSize: aiConfigPagination.pageSize, total: aiConfigPagination.total, showSizeChanger: true, showTotal: total => `共 ${total} 条`, onChange: (page, pageSize) => loadAiConfigs(aiConfigFilters, page, pageSize) }} locale={{ emptyText: aiConfigError ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="查询失败，请点击上方“重试”" /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有 AI 分析配置"><Button type="link" onClick={() => openAiConfigModal()}>新增配置</Button></Empty> }} columns={[
              { title: '标准游戏', dataIndex: 'game' },
              { title: '标准地区', dataIndex: 'region' },
              { title: 'AI 分析', dataIndex: 'analysis_enabled', width: 120, render: (v: boolean, row: any) => (
                <Switch checked={!!v} checkedChildren="开启" unCheckedChildren="关闭" loading={aiConfigRowLoadingId === row.id} onChange={checked => toggleAiConfigField(row, 'analysis_enabled', checked)} />
              ) },
              { title: '配置状态', dataIndex: 'enabled', width: 120, render: (v: boolean, row: any) => (
                <Switch checked={!!v} checkedChildren="启用" unCheckedChildren="停用" loading={aiConfigRowLoadingId === row.id} onChange={checked => toggleAiConfigField(row, 'enabled', checked)} />
              ) },
              { title: '更新时间', dataIndex: 'updated_at', width: 170, render: (v: string) => formatConversationTime(v) || '—' },
              { title: '操作', fixed: 'right' as const, width: 130, render: (_: any, row: any) => <Space size={4}><Button type="link" size="small" icon={<EditOutlined />} onClick={() => openAiConfigModal(row)}>编辑</Button><Popconfirm title="确认删除这条 AI 配置？" description="删除后不可恢复，且不会影响已处理的历史数据。" onConfirm={() => handleDeleteAiConfig(row)} okText="删除" cancelText="取消" okButtonProps={{ danger: true }}><Button type="link" danger size="small" icon={<DeleteOutlined />}>删除</Button></Popconfirm></Space> },
            ]} />
          </div>
        ),
      },
      {
        key: 'dictionaries', label: '业务配置',
        children: (
          <div className="qc-dictionary-page">
            <Alert className="qc-dictionary-intro" type="info" showIcon message="审核表单业务配置" description="停用项不会出现在新的审核表单中，但历史记录会继续显示原值；已被历史记录使用的配置项只能停用，不能删除。" />
            {dictionaryError && <Alert className="qc-dictionary-intro" type="error" showIcon message="业务配置加载失败" description={dictionaryError} action={<Button size="small" onClick={loadDictionaries}>重新加载</Button>} />}
            <div className="qc-mapping-toolbar">
              <Space wrap>
                <Text strong>配置组</Text>
                <Select value={dictionaryGroup} onChange={setDictionaryGroup} options={dictionaryGroups} style={{ minWidth: 180 }} />
              </Space>
              <Button type="primary" icon={<PlusOutlined />} onClick={() => openDictionaryModal()}>新增配置项</Button>
            </div>
            <Table className="qc-mapping-table" rowKey="id" dataSource={dictionaries} loading={dictionaryLoading} size="small" scroll={{ x: 900 }} locale={{ emptyText: dictionaryError ? '配置加载失败，请点击上方“重新加载”。' : '当前配置组没有配置项，请新增后再用于审核表单。' }} columns={[
              { title: '配置项值', dataIndex: 'value', width: 180 },
              { title: '展示名称', dataIndex: 'display_name', width: 180 },
              { title: '排序号', dataIndex: 'sort_order', width: 90 },
              { title: '状态', dataIndex: 'enabled', width: 100, render: (v: boolean) => <Tag color={v ? 'success' : 'default'}>{v ? '启用' : '停用'}</Tag> },
              { title: '历史引用', dataIndex: 'reference_count', width: 125, render: (v: number) => v ? <Tag color="warning">已使用 {v} 条</Tag> : <Tag>未使用</Tag> },
              { title: '更新时间', dataIndex: 'updated_at', width: 160, render: (v: string) => formatConversationTime(v) || '—' },
              { title: '操作', fixed: 'right' as const, width: 190, render: (_: any, row: any) => <Space size={2}>
                <Button type="link" size="small" icon={<EditOutlined />} onClick={() => openDictionaryModal(row)}>编辑</Button>
                {row.enabled && <Popconfirm title="停用配置项？" description="停用后新建和编辑表单不再提供该选项，历史记录不会被修改。" okText="停用" cancelText="取消" onConfirm={() => disableDictionary(row)}><Button type="link" size="small">停用</Button></Popconfirm>}
                <Tooltip title={row.reference_count ? `已被 ${row.reference_count} 条历史记录使用，只能停用` : '删除未使用的配置项'}><span><Popconfirm disabled={!!row.reference_count} title="删除配置项？" description="删除后不可恢复。" okText="删除" cancelText="取消" onConfirm={() => deleteDictionary(row)}><Button type="link" danger size="small" icon={<DeleteOutlined />} disabled={!!row.reference_count}>删除</Button></Popconfirm></span></Tooltip>
              </Space> },
            ]} />
          </div>
        ),
      },
      {
        key: 'backup', label: '数据备份',
        children: (
          <div>
            <Alert
              type="info"
              message="数据备份说明"
              description={`系统数据存储在 ${config.db_path || 'backend/qc.db'} 文件中。建议定期备份，也可将此文件直接复制到其他位置保存。`}
              style={{ marginBottom: 16 }}
            />
            <Button icon={<SaveOutlined />} type="primary" onClick={handleBackup} style={{ marginBottom: 16 }}>
              立即备份
            </Button>
            <Table
              rowKey="filename" dataSource={backups} size="small"
              columns={[
                { title: '文件名', dataIndex: 'filename' },
                { title: '大小', dataIndex: 'size_kb', render: (v: number) => `${v} KB` },
                { title: '备份时间', dataIndex: 'modified_at', render: (v: string) => v?.slice(0, 19) },
              ]}
            />
          </div>
        ),
      },
      {
        key: 'logs', label: '操作日志',
        children: (
          <div className="qc-audit-logs-page">
            <Alert type="info" showIcon style={{ marginBottom: 16 }} message="近 30 天操作日志" description="只展示登录、权限、分派/释放、审核提交与改状态、知识库排除/恢复等已有记录。打开本页、筛选和翻页不会写入新日志。" />
            <Form form={auditForm} layout="inline" onFinish={handleAuditSearch} style={{ marginBottom: 16, rowGap: 12 }} initialValues={{ range: defaultAuditRange() }}>
              <Form.Item name="range" label="时间范围">
                <RangePicker
                  showTime
                  allowClear={false}
                  disabledDate={current => !current || current.isBefore(dayjs().subtract(AUDIT_WINDOW_DAYS, 'day'))}
                />
              </Form.Item>
              <Form.Item name="operator_id" label="操作人">
                <Select allowClear placeholder="全部" style={{ minWidth: 160 }} options={(auditOptions.operators || []).map((row: any) => ({ value: row.id, label: row.username }))} />
              </Form.Item>
              <Form.Item name="action" label="动作">
                <Select allowClear placeholder="全部" style={{ minWidth: 180 }} options={auditOptions.actions || []} />
              </Form.Item>
              <Form.Item name="target_type" label="对象类型">
                <Select allowClear placeholder="全部" style={{ minWidth: 180 }} options={auditOptions.target_types || []} />
              </Form.Item>
              <Form.Item>
                <Space>
                  <Button type="primary" htmlType="submit" icon={<SearchOutlined />} loading={auditLoading}>查询</Button>
                  <Button icon={<ReloadOutlined />} onClick={() => void resetAuditFilters()}>重置</Button>
                  <Button icon={<DownloadOutlined />} loading={auditExporting} onClick={() => void exportAuditLogs()}>导出 Excel</Button>
                </Space>
              </Form.Item>
            </Form>
            {auditError && <Alert type="error" showIcon style={{ marginBottom: 16 }} message="操作日志加载失败" description={auditError} action={<Button size="small" onClick={() => void loadAuditLogs()}>重试</Button>} />}
            <Table
              rowKey="id"
              size="small"
              loading={auditLoading}
              dataSource={auditLogs}
              scroll={{ x: 1100 }}
              pagination={{
                current: auditPagination.current,
                pageSize: auditPagination.pageSize,
                total: auditPagination.total,
                showSizeChanger: true,
                pageSizeOptions: ['20', '50'],
                showTotal: total => `共 ${total} 条`,
                onChange: (page, pageSize) => void loadAuditLogs(auditFilters, page, pageSize),
              }}
              locale={{ emptyText: auditError ? '加载失败，请点击上方“重试”。' : '近 30 天没有操作记录' }}
              columns={[
                { title: '时间', dataIndex: 'created_at', width: 170, render: (v: string) => formatConversationTime(v) || '—' },
                { title: '操作人', dataIndex: 'operator_name', width: 140, render: dash },
                { title: '动作', dataIndex: 'action_label', width: 150, render: (v: string, row: any) => v || row.action || '—' },
                { title: '对象', dataIndex: 'target_label', width: 180, render: dash },
                { title: '改前', dataIndex: 'from_value', ellipsis: true, render: dash },
                { title: '改后', dataIndex: 'to_value', ellipsis: true, render: dash },
                { title: '原因', dataIndex: 'reason', ellipsis: true, render: dash },
              ]}
            />
          </div>
        ),
      }
    )
  }

  return (
    <div className="qc-admin-page">
      <Title level={4} className="page-heading">系统管理</Title>
      <Card>
        <Tabs activeKey={adminTab} onChange={key => { setAdminTab(key); const next = new URLSearchParams(searchParams); next.set('tab', key); if (key !== 'mappings') next.delete('pending'); setSearchParams(next, { replace: true }); if (key === 'mappings') void loadPendingCount() }} items={tabs} />
      </Card>

      <Modal title="修改密码" open={pwdModal} onCancel={() => setPwdModal(false)} footer={null}>
        <Form form={pwdForm} layout="vertical" onFinish={handleChangePwd}>
          <Form.Item label="原密码" name="old_password" rules={[{ required: true }]}>
            <Input.Password />
          </Form.Item>
          <Form.Item label="新密码" name="new_password" rules={[{ required: true, min: 6 }]}>
            <Input.Password />
          </Form.Item>
          <Button type="primary" htmlType="submit" block>确认修改</Button>
        </Form>
      </Modal>

      <Modal title={editingUser ? '编辑用户' : '编辑用户'} open={editUserModal} onCancel={() => setEditUserModal(false)} footer={null} destroyOnClose>
        <Alert type="info" showIcon style={{ marginBottom: 16 }} message="保存后立即按最新用户名和邮箱登录" description={editingUser?.id === user?.id ? '如果修改了自己的角色，当前页的管理权限会立即失效，请重新登录后生效。' : '修改角色会释放该用户未完成的审核任务，已完成任务不变。'} />
        <Form form={editUserForm} layout="vertical" onFinish={handleEditUser}>
          <Form.Item label="用户名" name="username" rules={[{ required: true, whitespace: true, message: '请输入用户名' }]} extra="用户名必须唯一；已存在时请手动加 123，系统不会自动改名。"><Input /></Form.Item>
          <Form.Item label="邮箱" name="email" rules={[{ required: true, type: 'email', message: '请输入有效邮箱' }]}><Input /></Form.Item>
          <Form.Item label="角色" name="role" rules={[{ required: true }]}><Select options={[{ value: 'analyst', label: '质检员' }, { value: 'admin', label: '管理员' }]} /></Form.Item>
          <Space style={{ width: '100%', justifyContent: 'flex-end' }}><Button onClick={() => setEditUserModal(false)}>取消</Button><Button type="primary" htmlType="submit">保存</Button></Space>
        </Form>
      </Modal>

      <Modal title={resetPwdUser ? `重置「${resetPwdUser.username}」的密码` : '重置密码'} open={resetPwdModal} onCancel={() => setResetPwdModal(false)} footer={null} destroyOnClose>
        <Alert type="warning" showIcon style={{ marginBottom: 16 }} message="不会回显旧密码" description="重置后该用户下次登录必须先修改密码，才能使用其他功能。" />
        <Form form={resetPwdForm} layout="vertical" onFinish={handleResetPwd}>
          <Form.Item label="新密码" name="new_password" rules={[{ required: true, min: 6, message: '密码至少 6 位' }]}><Input.Password /></Form.Item>
          <Form.Item label="确认密码" name="confirm_password" rules={[{ required: true, min: 6, message: '请再次输入新密码' }]}><Input.Password /></Form.Item>
          <Space style={{ width: '100%', justifyContent: 'flex-end' }}><Button onClick={() => setResetPwdModal(false)}>取消</Button><Button type="primary" htmlType="submit">重置密码</Button></Space>
        </Form>
      </Modal>

      <Modal title="新建用户" open={addModal} onCancel={() => setAddModal(false)} footer={null}>
        <Form form={form} layout="vertical" onFinish={handleAddUser}>
          <Form.Item label="用户名" name="username" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item label="邮箱" name="email" rules={[{ required: true, type: 'email' }]}>
            <Input />
          </Form.Item>
          <Form.Item label="初始密码" name="password" rules={[{ required: true, min: 6 }]}>
            <Input.Password />
          </Form.Item>
          <Form.Item label="角色" name="role" initialValue="analyst">
            <Select options={[{ value: 'analyst', label: '质检员' }, { value: 'admin', label: '管理员' }]} />
          </Form.Item>
          <Button type="primary" htmlType="submit" block>创建</Button>
        </Form>
      </Modal>

      <Modal rootClassName="qc-mapping-modal" title={aiConfigEditing ? '编辑游戏 AI 分析配置' : '新增游戏 AI 分析配置'} open={aiConfigModal} onCancel={() => setAiConfigModal(false)} footer={null} destroyOnClose width={520}>
        <Alert type="info" showIcon style={{ marginBottom: 16 }} message="保存后只影响后续新分析任务" description="不会调用 MaaS，也不会修改已完成、分析中、已审核或已分派的历史切片。" />
        <Form form={aiConfigForm} layout="vertical" onFinish={handleSaveAiConfig}>
          <Form.Item label="标准游戏" name="game" rules={[{ required: true, whitespace: true, message: '请输入标准游戏名' }]}><Input placeholder="例如：冒险大作战" /></Form.Item>
          <Form.Item label="标准地区" name="region" rules={[{ required: true, whitespace: true, message: '请输入标准地区' }]}><Input placeholder="例如：东南亚、欧美、港台、日本、全球" /></Form.Item>
          <Form.Item name="analysis_enabled" valuePropName="checked"><Switch checkedChildren="AI 分析开启" unCheckedChildren="AI 分析关闭" /></Form.Item>
          <Form.Item name="enabled" valuePropName="checked"><Switch checkedChildren="配置启用" unCheckedChildren="配置停用" /></Form.Item>
          <Space style={{ width: '100%', justifyContent: 'flex-end' }}><Button onClick={() => setAiConfigModal(false)}>取消</Button><Button type="primary" htmlType="submit">保存配置</Button></Space>
        </Form>
      </Modal>

      <Modal rootClassName="qc-mapping-modal" title={mappingEditing ? '编辑地区渠道映射' : '新增地区渠道映射'} open={mappingModal} onCancel={() => setMappingModal(false)} footer={null} destroyOnClose width={560}>
        <Form form={mappingForm} layout="vertical" onFinish={handleSaveMapping}>
          <Form.Item
            label="原始地区"
            name="raw_region"
            extra={isGameProductMapping ? '产品 ID 规则不按原始地区匹配，将保存为空' : undefined}
          >
            <Input placeholder="例如：英语区；留空表示任意" disabled={isGameProductMapping} />
          </Form.Item>
          <Form.Item label="原始渠道" name="raw_channel"><Input placeholder="例如：M后台；留空表示任意" /></Form.Item>
          <Form.Item label="游戏" name="game"><Input placeholder="留空表示任意游戏" /></Form.Item>
          <Form.Item label="匹配字段" name="match_field"><Select options={matchFieldOptions(mappingOptions.match_fields)} onChange={value => { if (value === 'gameProductId') mappingForm.setFieldValue('raw_region', undefined) }} /></Form.Item>
          <Form.Item label="匹配值" name="match_value"><Input placeholder="例如：韩语或页面 ID；留空表示任意" /></Form.Item>
          <Form.Item label="标准渠道" name="target_channel" rules={[{ required: true, message: '请输入标准渠道' }]}><Input placeholder="例如：M后台、VIP后台" /></Form.Item>
          <Form.Item label="标准地区" name="target_region"><Input placeholder="例如：欧美、东南亚；VIP后台可留空" /></Form.Item>
          <Form.Item label="备注" name="remark"><Input.TextArea rows={2} placeholder="记录规则来源或适用范围" /></Form.Item>
          <Form.Item name="enabled" valuePropName="checked" initialValue><Switch checkedChildren="启用" unCheckedChildren="停用" /></Form.Item>
          <Space style={{ width: '100%', justifyContent: 'flex-end' }}><Button onClick={() => setMappingModal(false)}>取消</Button><Button type="primary" htmlType="submit">保存映射</Button></Space>
        </Form>
      </Modal>

      <Modal title={dictionaryEditing ? '编辑配置项' : '新增配置项'} open={dictionaryModal} onCancel={() => setDictionaryModal(false)} footer={null} destroyOnClose>
        <Form form={dictionaryForm} layout="vertical" onFinish={handleSaveDictionary}>
          <Form.Item label="配置组" name="group" rules={[{ required: true, message: '请选择配置组' }]}><Select disabled={!!dictionaryEditing} options={dictionaryGroups} /></Form.Item>
          <Form.Item label="配置项值" name="value" extra={dictionaryEditing?.reference_count ? '该值已被历史记录使用，只能修改展示名称、排序或停用。' : '同一配置组内不能重复。'} rules={[{ required: true, whitespace: true, message: '请输入配置项值' }]}><Input disabled={!!dictionaryEditing?.reference_count} placeholder="例如：游戏玩法" /></Form.Item>
          <Form.Item label="展示名称" name="display_name" rules={[{ required: true, whitespace: true, message: '请输入展示名称' }]}><Input placeholder="表单中显示给审核员的名称" /></Form.Item>
          <Form.Item label="排序号" name="sort_order" rules={[{ required: true, message: '请输入排序号' }]}><Input type="number" min={0} /></Form.Item>
          <Form.Item name="enabled" valuePropName="checked"><Switch checkedChildren="启用" unCheckedChildren="停用" /></Form.Item>
          <Space style={{ width: '100%', justifyContent: 'flex-end' }}><Button onClick={() => setDictionaryModal(false)}>取消</Button><Button type="primary" htmlType="submit">保存配置项</Button></Space>
        </Form>
      </Modal>
    </div>
  )
}
