import { Outlet, useNavigate, useLocation } from 'react-router-dom'
import { useState } from 'react'
import { Layout as AntLayout, Button, Avatar, Dropdown, Space, Typography, Modal, Tooltip } from 'antd'
import { DashboardOutlined, UploadOutlined, FileTextOutlined, HistoryOutlined, SettingOutlined, LogoutOutlined, UserOutlined, ThunderboltOutlined, BarChartOutlined, FieldTimeOutlined, QuestionCircleOutlined } from '@ant-design/icons'
import { ROLE_LABELS, useAuthStore } from '../store/auth'
import KnowledgePoolDrawer from './KnowledgePoolDrawer'
import HelpDrawer from './HelpDrawer'

const { Header, Content } = AntLayout
const { Text } = Typography

export default function Layout() {
  const navigate = useNavigate(); const location = useLocation(); const { user, logout } = useAuthStore(); const [knowledgePoolOpen, setKnowledgePoolOpen] = useState(false); const [helpOpen, setHelpOpen] = useState(false)
  const isAdmin = user?.role === 'admin'
  const go = (path: string) => navigate(path)
  const nav = [
    { path: '/dashboard', label: '概览', icon: <DashboardOutlined />, visible: true },
    { path: '/report', label: '问题列表', icon: <FileTextOutlined />, visible: true },
    { path: '/history', label: '历史记录', icon: <HistoryOutlined />, visible: isAdmin },
  ]
  const utility = [
    { key: 'knowledge-pool', label: '一键 QA', icon: <ThunderboltOutlined />, visible: true },
    { key: 'review-workload', label: '数据看板', icon: <BarChartOutlined />, visible: true },
    { key: 'lifecycle', label: '生命周期', icon: <FieldTimeOutlined />, visible: isAdmin },
  ]
  const menu = { items: [...(isAdmin ? [{ key: 'admin', icon: <SettingOutlined />, label: '系统管理' }] : []), { type: 'divider' as const }, { key: 'logout', icon: <LogoutOutlined />, label: '退出登录', danger: true }], onClick: ({ key }: { key: string }) => key === 'admin' ? go('/admin') : key === 'logout' && (logout(), go('/login')) }
  return <AntLayout className="qc-layout"><Header className="qc-header"><div className="qc-brand" aria-label="AI问题案例审核"><span className="qc-brand-mark">AI</span><div><Typography.Text strong className="qc-brand-title">AI问题案例审核</Typography.Text></div></div><div className="qc-nav-scroll"><Space className="qc-nav" size={4}>{nav.filter(item => item.visible).map(item => <Button key={item.path} type={location.pathname === item.path ? 'primary' : 'text'} size="small" icon={item.icon} onClick={() => go(item.path)}>{item.label}</Button>)}{utility.filter(item => item.visible).map(item => <Button key={item.key} type={item.key === 'review-workload' && location.pathname === '/dashboard/review-workload' ? 'primary' : 'text'} size="small" icon={item.icon} onClick={() => item.key === 'knowledge-pool' ? setKnowledgePoolOpen(true) : item.key === 'review-workload' ? go('/dashboard/review-workload') : Modal.info({ title: item.label, content: '功能开发中，敬请期待。', okText: '知道了' })}>{item.label}</Button>)}{isAdmin && <Button type={location.pathname === '/admin/audit' ? 'primary' : 'text'} size="small" icon={<SettingOutlined />} onClick={() => go('/admin/audit')}>分析审计</Button>}{isAdmin && <Button type={location.pathname === '/upload' ? 'primary' : 'default'} size="small" icon={<UploadOutlined />} onClick={() => go('/upload')}>上传文件</Button>}</Space></div><div className="qc-user-menu"><Tooltip title="使用说明"><Button type="text" className="qc-help-trigger" icon={<QuestionCircleOutlined />} aria-label="使用说明" onClick={() => setHelpOpen(true)} /></Tooltip><Dropdown menu={menu} trigger={['click']}><Space className="qc-user-trigger"><Avatar size="small" style={{ background: '#1d4ed8' }} icon={<UserOutlined />} /><Text>{user?.username}</Text><Text type="secondary" style={{ fontSize: 12 }}>{ROLE_LABELS[(user?.role || 'analyst') as 'admin' | 'analyst']}</Text></Space></Dropdown></div></Header><Content className="qc-content"><Outlet /></Content><KnowledgePoolDrawer open={knowledgePoolOpen} onClose={() => setKnowledgePoolOpen(false)} isAdmin={isAdmin} /><HelpDrawer open={helpOpen} onClose={() => setHelpOpen(false)} role={(user?.role === 'admin' ? 'admin' : 'analyst')} /></AntLayout>
}
