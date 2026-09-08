import axios from 'axios'

export const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || '/api/v1',
  timeout: 180000
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
