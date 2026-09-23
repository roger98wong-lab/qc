import { useState } from 'react'
import { Form, Input, Button, Card, message, Typography, Alert } from 'antd'
import { LockOutlined, UserOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { authApi } from '../api'
import { useAuthStore } from '../store/auth'

const { Title } = Typography

export default function Login() {
  const [loading, setLoading] = useState(false)
  const [forceChange, setForceChange] = useState(false)
  const navigate = useNavigate()
  const setAuth = useAuthStore(s => s.setAuth)
  const token = useAuthStore(s => s.token)
  const user = useAuthStore(s => s.user)
  const logout = useAuthStore(s => s.logout)
  const shouldForceChange = forceChange || !!(token && user?.must_change_password)

  const finishLogin = (data: any) => {
    setAuth(data.access_token, {
      id: data.user_id,
      username: data.username,
      email: '',
      role: data.role,
      must_change_password: !!data.must_change_password,
      status: data.status,
    })
    if (data.must_change_password) {
      setForceChange(true)
      message.warning('请先修改密码后再使用系统')
      return
    }
    message.success('登录成功')
    navigate('/dashboard')
  }

  const onFinish = async (values: { username: string; password: string }) => {
    setLoading(true)
    try {
      const res = await authApi.login(values.username, values.password)
      finishLogin(res.data)
    } catch (err: any) {
      message.error(err.response?.data?.detail || '登录失败，请检查用户名和密码')
    } finally {
      setLoading(false)
    }
  }

  const onChangePassword = async (values: { old_password: string; new_password: string }) => {
    setLoading(true)
    try {
      await authApi.changePassword(values.old_password, values.new_password)
      if (token && user) setAuth(token, { ...user, must_change_password: false })
      message.success('密码已修改，正在进入系统')
      navigate('/dashboard')
    } catch (err: any) {
      message.error(err.response?.data?.detail || '修改密码失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{
      minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: 'linear-gradient(135deg, #1e3a8a 0%, #1d4ed8 50%, #3b82f6 100%)',
    }}>
      <Card style={{ width: 400, borderRadius: 12, boxShadow: '0 20px 60px rgba(0,0,0,.3)' }}>
        <div style={{ textAlign: 'center', marginBottom: 32 }}>
          <Title level={3} style={{ margin: 0, color: '#1e3a8a' }}>AI问题案例审核</Title>
        </div>
        {shouldForceChange ? (
          <>
            <Alert type="warning" showIcon style={{ marginBottom: 16 }} message="管理员已重置密码，请先修改后再进入系统" />
            <Form layout="vertical" onFinish={onChangePassword} size="large">
              <Form.Item name="old_password" rules={[{ required: true, message: '请输入当前密码' }]}>
                <Input.Password prefix={<LockOutlined />} placeholder="当前密码" />
              </Form.Item>
              <Form.Item name="new_password" rules={[{ required: true, min: 6, message: '新密码至少 6 位' }]}>
                <Input.Password prefix={<LockOutlined />} placeholder="新密码" />
              </Form.Item>
              <Form.Item style={{ marginBottom: 8 }}>
                <Button type="primary" htmlType="submit" block loading={loading}>修改密码并进入</Button>
              </Form.Item>
              <Button type="link" block onClick={() => { logout(); setForceChange(false) }}>返回登录</Button>
            </Form>
          </>
        ) : (
          <Form layout="vertical" onFinish={onFinish} size="large">
            <Form.Item name="username" rules={[{ required: true, message: '请输入用户名' }]}>
              <Input prefix={<UserOutlined />} placeholder="用户名" />
            </Form.Item>
            <Form.Item name="password" rules={[{ required: true, message: '请输入密码' }]}>
              <Input.Password prefix={<LockOutlined />} placeholder="密码" />
            </Form.Item>
            <Form.Item style={{ marginBottom: 0 }}>
              <Button type="primary" htmlType="submit" block loading={loading}>登录</Button>
            </Form.Item>
          </Form>
        )}
      </Card>
    </div>
  )
}
