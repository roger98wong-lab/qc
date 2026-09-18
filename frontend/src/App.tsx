import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import * as React from 'react'
import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { authApi } from './api'
import { ROLE_ROUTES, useAuthStore, type Role } from './store/auth'
import { Alert, Spin } from 'antd'
import Login from './pages/Login'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import Upload from './pages/Upload'
import Report from './pages/Report'
import History from './pages/History'
import Admin from './pages/Admin'
import AdminAudit from './pages/AdminAudit'
import ReviewAssignments from './pages/ReviewAssignments'
import MyReviews from './pages/MyReviews'
import ReviewWorkloadDashboard from './pages/ReviewWorkloadDashboard'

function RequireAuth({ children }: { children: React.ReactNode }) {
  const token = useAuthStore(s => s.token); const user = useAuthStore(s => s.user); const setAuth = useAuthStore(s => s.setAuth); const logout = useAuthStore(s => s.logout)
  const [checking, setChecking] = React.useState(Boolean(token))
  React.useEffect(() => {
    let active = true
    if (!token) { setChecking(false); return }
    setChecking(true)
    authApi.me().then(response => { if (active && token) setAuth(token, response.data) }).catch(() => { if (active) logout() }).finally(() => active && setChecking(false))
    return () => { active = false }
  }, [token])
  if (!token) return <Navigate to="/login" replace />
  if (checking || !user) return <div style={{ minHeight: 240, display: 'grid', placeItems: 'center' }}><Spin tip="正在验证登录状态" /></div>
  return <>{children}</>
}

function RequireRole({ roles, children }: { roles: Role[]; children: React.ReactNode }) {
  const user = useAuthStore(s => s.user)
  if (!user || !roles.includes(user.role as Role)) return <Alert type="error" showIcon message="无权限访问" description="当前账号没有访问此页面的权限。" />
  return <>{children}</>
}

function ProtectedRoute({ path, children }: { path: string; children: React.ReactNode }) {
  return <RequireRole roles={ROLE_ROUTES[path] || []}>{children}</RequireRole>
}

export default function App() {
  return (
    <ConfigProvider locale={zhCN} theme={{ token: {
      colorPrimary: '#1d4ed8', colorInfo: '#1d4ed8', colorBgLayout: '#f4f7fb',
      colorText: '#172033', colorTextSecondary: '#64748b', borderRadius: 10,
      fontFamily: 'Inter, "PingFang SC", "Microsoft YaHei", system-ui, sans-serif',
    } }}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/" element={<RequireAuth><Layout /></RequireAuth>}>
            <Route index element={<Navigate to="/dashboard" replace />} />
            <Route path="dashboard" element={<ProtectedRoute path="/dashboard"><Dashboard /></ProtectedRoute>} />
            <Route path="dashboard/review-workload" element={<ProtectedRoute path="/dashboard/review-workload"><ReviewWorkloadDashboard /></ProtectedRoute>} />
            <Route path="upload" element={<ProtectedRoute path="/upload"><Upload /></ProtectedRoute>} />
            <Route path="report" element={<ProtectedRoute path="/report"><Report /></ProtectedRoute>} />
            <Route path="history" element={<ProtectedRoute path="/history"><History /></ProtectedRoute>} />
            <Route path="admin" element={<ProtectedRoute path="/admin"><Admin /></ProtectedRoute>} />
            <Route path="admin/audit" element={<ProtectedRoute path="/admin/audit"><AdminAudit /></ProtectedRoute>} />
            <Route path="admin/reviews" element={<ProtectedRoute path="/admin/reviews"><ReviewAssignments /></ProtectedRoute>} />
            <Route path="my-reviews" element={<ProtectedRoute path="/my-reviews"><MyReviews /></ProtectedRoute>} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </ConfigProvider>
  )
}
