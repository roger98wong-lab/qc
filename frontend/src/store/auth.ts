import { create } from 'zustand'

interface UserInfo {
  id: number
  username: string
  email: string
  role: string
}

interface AuthState {
  token: string | null
  user: UserInfo | null
  setAuth: (token: string, user: UserInfo) => void
  logout: () => void
}

export type Role = 'admin' | 'analyst'
export const ROLE_LABELS: Record<Role, string> = { admin: '管理员', analyst: '质检员' }
export const ROLE_ROUTES: Record<string, Role[]> = {
  '/dashboard': ['admin', 'analyst'], '/dashboard/review-workload': ['admin', 'analyst'],
  '/report': ['admin', 'analyst'], '/history': ['admin'], '/upload': ['admin'],
  '/admin': ['admin'], '/admin/audit': ['admin'], '/admin/reviews': ['admin'], '/my-reviews': ['analyst'],
}

export const useAuthStore = create<AuthState>(set => ({
  token: localStorage.getItem('token'),
  user: (() => {
    try { return JSON.parse(localStorage.getItem('user') || 'null') } catch { return null }
  })(),
  setAuth: (token, user) => {
    localStorage.setItem('token', token)
    localStorage.setItem('user', JSON.stringify(user))
    set({ token, user })
  },
  logout: () => {
    localStorage.removeItem('token')
    localStorage.removeItem('user')
    set({ token: null, user: null })
  },
}))
