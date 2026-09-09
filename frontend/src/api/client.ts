import axios from 'axios'

export const NORMAL_REQUEST_TIMEOUT_MS = 15 * 60 * 1000
export const SYNCHRONOUS_AI_TIMEOUT_MS = 2 * 60 * 60 * 1000

export const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || '/api/v1',
  timeout: NORMAL_REQUEST_TIMEOUT_MS
})

api.interceptors.request.use((config) => {
  const key = localStorage.getItem('gw_ap_api_key')
  if (key) config.headers['X-API-Key'] = key
  return config
})

api.interceptors.response.use(response => response, error => {
  if (error.response?.status === 401) window.dispatchEvent(new Event('gwap-auth-expired'))
  return Promise.reject(error)
})
