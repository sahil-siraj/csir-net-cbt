const API_BASE_URL = (
  import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'
).replace(/\/+$/, '')

const TOKEN_KEY = 'csir_jwt_token'

export function getStoredToken() {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function setStoredToken(token) {
  try {
    if (token) {
      localStorage.setItem(TOKEN_KEY, token)
    } else {
      localStorage.removeItem(TOKEN_KEY)
    }
  } catch {
    // ignore
  }
}

let onUnauthorizedCallback = null
export function setUnauthorizedHandler(fn) {
  onUnauthorizedCallback = fn
}

async function request(path, options = {}) {
  const token = getStoredToken()
  const headers = {
    'Content-Type': 'application/json',
    ...(options.headers || {}),
  }
  if (token) {
    headers.Authorization = `Bearer ${token}`
  }

  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers,
  })

  let data = null
  const text = await res.text()
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      data = { detail: text }
    }
  }

  if (!res.ok) {
    if (res.status === 401 && !options.skipAuthRedirect) {
      setStoredToken(null)
      if (onUnauthorizedCallback) onUnauthorizedCallback()
    }
    const err = new Error(data?.detail || `Request failed (${res.status})`)
    err.status = res.status
    err.data = data
    throw err
  }

  return data
}

export const api = {
  health: () => request('/api/health', { skipAuthRedirect: true }),
  register: (payload) =>
    request('/api/auth/register', {
      method: 'POST',
      body: JSON.stringify(payload),
      skipAuthRedirect: true,
    }),
  login: (payload) =>
    request('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify(payload),
      skipAuthRedirect: true,
    }),
  me: () => request('/api/auth/me'),

  getTestsMeta: () => request('/api/tests/meta'),
  createTest: (payload) =>
    request('/api/tests/create', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  getTest: (testId) => request(`/api/tests/${testId}`),
  saveResponse: (testId, payload) =>
    request(`/api/tests/${testId}/response`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  checkAnswer: (testId, payload) =>
    request(`/api/tests/${testId}/check-answer`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  submitTest: (testId) =>
    request(`/api/tests/${testId}/submit`, {
      method: 'POST',
    }),

  getAnalytics: () => request('/api/analytics/overview'),
  toggleBookmark: (payload) =>
    request('/api/bookmarks/toggle', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  getBookmarks: () => request('/api/bookmarks'),
  flagQuestion: (payload) =>
    request('/api/flags', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
}
