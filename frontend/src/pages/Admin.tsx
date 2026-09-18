import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  Card, Table, Button, Form, Input, Select, Modal, message,
  Tabs, Descriptions, Alert, Typography, Space, Tag, Switch, Popconfirm, Tooltip, Collapse, Empty, Badge,
} from 'antd'
import { PlusOutlined, SaveOutlined, EditOutlined, DeleteOutlined, SearchOutlined, ReloadOutlined, FilterOutlined } from '@ant-design/icons'
import { authApi, adminApi } from '../api'
import { useAuthStore } from '../store/auth'
import { formatConversationTime } from '../components/Conversation'

const { Title, Text, Paragraph } = Typography

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
  const [form] = Form.useForm()
  const [pwdForm] = Form.useForm()
  const [mappingForm] = Form.useForm()
  const [mappingFilterForm] = Form.useForm()
  const [configForm] = Form.useForm()
  const [dictionaryForm] = Form.useForm()
  const [pendingCount, setPendingCount] = useState(0)
  const [adminTab, setAdminTab] = useState(searchParams.get('tab') || 'profile')

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
    } catch (err: any) {
      setMappings([])
      setMappingPagination(current => ({ ...current, current: page, pageSize, total: 0 }))
      setMappingError(err.response?.data?.detail || '映射规则查询失败，请检查网络后重试。')
    } finally { setMappingLoading(false) }
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
      loadDictionaries()
    }
  }, [isAdmin, dictionaryGroup])

  const handleMappingSearch = async (values: Record<string, any>) => {
    const filters = Object.fromEntries(Object.entries(values).filter(([, value]) => value !== undefined && value !== null && value !== ''))
    setMappingFilters(filters)
    await loadMappings(filters, 1, mappingPagination.pageSize)
  }

  const resetMappingFilters = async () => {
    mappingFilterForm.resetFields()
    setMappingFilters({})
    await loadMappings({}, 1, mappingPagination.pageSize)
  }

  useEffect(() => {
    if (Object.keys(config).length > 0) configForm.setFieldsValue({ maas_base_url: config.maas_base_url || '', concurrency: config.concurrency ?? 5 })
  }, [config, configForm])

  const handleAddUser = async (values: any) => {
    try {
      await authApi.createUser(values)
      message.success('用户创建成功')
      setAddModal(false)
      form.resetFields()
      authApi.listUsers().then(r => setUsers(r.data))
    } catch (err: any) {
      message.error(err.response?.data?.detail || '创建失败')
    }
  }

  const handleToggle = async (id: number) => {
    await authApi.toggleUser(id)
    authApi.listUsers().then(r => setUsers(r.data))
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
    mappingForm.setFieldsValue(mapping ? { ...mapping, match_field: mapping.match_field || 'none', enabled: mapping.enabled !== false } : { match_field: 'none', enabled: true })
    setMappingModal(true)
  }

  const handleSaveMapping = async (values: any) => {
    const payload = Object.fromEntries(Object.entries(values).map(([key, value]) => [key, value === '' ? null : value]))
    try {
      if (mappingEditing) await adminApi.updateMapping(mappingEditing.id, payload)
      else await adminApi.createMapping(payload)
      setMappingModal(false)
      mappingForm.resetFields()
      await Promise.all([loadMappings(), loadMappingOptions(), loadPendingCount()])
      message.success(mappingEditing ? '映射已更新' : '映射已新增')
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
    { title: '最后登录', dataIndex: 'last_login', render: (v: string) => formatConversationTime(v) || '从未' },
    {
      title: '启用', dataIndex: 'is_active', width: 80,
      render: (v: boolean, row: any) => <Switch checked={v} onChange={() => handleToggle(row.id)} disabled={row.id === user?.id} />,
    },
  ]

  const tabs = [
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
            <Button type="primary" icon={<PlusOutlined />} style={{ marginBottom: 16 }} onClick={() => setAddModal(true)}>
              新建用户
            </Button>
            <Table rowKey="id" dataSource={users} columns={userColumns} size="small" />
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
              <Space>
                <Button className="qc-mapping-pending-btn" onClick={async () => {
                  const filters = { enabled: false, pending: true }
                  mappingFilterForm.setFieldsValue({ enabled: false, raw_region: undefined, raw_channel: undefined, game: undefined, match_field: undefined, match_value: undefined, target_channel: undefined, target_region: undefined })
                  setMappingFilters(filters)
                  await loadMappings(filters, 1, mappingPagination.pageSize)
                }}>待补充{pendingCount > 0 ? <span className="qc-mapping-tab-count">{pendingCount > 99 ? '99+' : pendingCount}</span> : null}</Button>
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
            <Table className="qc-mapping-table" rowKey="id" dataSource={mappings} loading={mappingLoading} size="small" scroll={{ x: 1000 }} pagination={{ current: mappingPagination.current, pageSize: mappingPagination.pageSize, total: mappingPagination.total, showSizeChanger: true, showTotal: total => `共 ${total} 条`, onChange: (page, pageSize) => loadMappings(mappingFilters, page, pageSize) }} locale={{ emptyText: mappingError ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="查询失败，请点击上方“重试”" /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前筛选条件没有匹配规则"><Button type="link" onClick={resetMappingFilters}>重置筛选</Button></Empty> }} columns={[
              { title: '原始地区', dataIndex: 'raw_region', render: (v: string) => v || <Tag className="qc-any-tag">任意</Tag> },
              { title: '原始渠道', dataIndex: 'raw_channel', render: (v: string) => v || <Tag className="qc-any-tag">任意</Tag> },
              { title: '游戏', dataIndex: 'game', render: (v: string) => v || <Tag className="qc-any-tag">任意</Tag> },
              { title: '匹配字段', dataIndex: 'match_field', render: (v: string) => matchFieldLabel(v) },
              { title: '匹配值', dataIndex: 'match_value', render: (v: string) => v || <Tag className="qc-any-tag">任意</Tag> },
              { title: '标准渠道', dataIndex: 'target_channel' },
              { title: '标准地区', dataIndex: 'target_region', render: (v: string) => v || '—' },
              { title: '状态', dataIndex: 'enabled', render: (v: boolean) => <Tag color={v === false ? 'default' : 'success'}>{v === false ? '停用' : '启用'}</Tag> },
              { title: '操作', fixed: 'right' as const, width: 130, render: (_: any, row: any) => <Space size={4}><Button type="link" size="small" icon={<EditOutlined />} onClick={() => openMappingModal(row)}>编辑</Button><Popconfirm title="确认删除这条映射？" description="删除后不可恢复，且不会影响已处理的历史数据。" onConfirm={() => handleDeleteMapping(row)} okText="删除" cancelText="取消" okButtonProps={{ danger: true }}><Button type="link" danger size="small" icon={<DeleteOutlined />}>删除</Button></Popconfirm></Space> },
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

      <Modal rootClassName="qc-mapping-modal" title={mappingEditing ? '编辑地区渠道映射' : '新增地区渠道映射'} open={mappingModal} onCancel={() => setMappingModal(false)} footer={null} destroyOnClose width={560}>
        <Form form={mappingForm} layout="vertical" onFinish={handleSaveMapping}>
          <Form.Item label="原始地区" name="raw_region"><Input placeholder="例如：英语区；留空表示任意" /></Form.Item>
          <Form.Item label="原始渠道" name="raw_channel"><Input placeholder="例如：M后台；留空表示任意" /></Form.Item>
          <Form.Item label="游戏" name="game"><Input placeholder="留空表示任意游戏" /></Form.Item>
          <Form.Item label="匹配字段" name="match_field"><Select options={matchFieldOptions(mappingOptions.match_fields)} /></Form.Item>
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
