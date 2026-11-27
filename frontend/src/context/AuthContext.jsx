import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import {
  api,
  getStoredToken,
  setStoredToken,
  setUnauthorizedHandler,
} from '../api/client'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [token, setToken] = useState(() => getStoredToken())
  const [loading, setLoading] = useState(true)

  const logout = useCallback(() => {
    setStoredToken(null)
    setToken(null)
    setUser(null)
  }, [])

  useEffect(() => {
    setUnauthorizedHandler(logout)
  }, [logout])

  useEffect(() => {
    let active = true
    async function hydrate() {
      const existingToken = getStoredToken()
      if (!existingToken) {
        if (active) {
          setUser(null)
          setLoading(false)
        }
        return
      }
      try {
        const res = await api.me()
        if (active) {
          setUser(res.user)
          setToken(existingToken)
        }
      } catch {
        if (active) {
          setStoredToken(null)
          setToken(null)
          setUser(null)
        }
      } finally {
        if (active) setLoading(false)
      }
    }
    hydrate()
    return () => {
      active = false
    }
  }, [])

  const login = async (credentials) => {
    const res = await api.login(credentials)
    setStoredToken(res.access_token)
    setToken(res.access_token)
    setUser(res.user)
    return res.user
  }

  const register = async (payload) => {
    const res = await api.register(payload)
    setStoredToken(res.access_token)
    setToken(res.access_token)
    setUser(res.user)
    return res.user
  }

  return (
    <AuthContext.Provider
      value={{
        user,
        token,
        loading,
        isAuthenticated: Boolean(user && token),
        login,
        register,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider')
  return ctx
}
