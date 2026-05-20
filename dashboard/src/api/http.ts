import axios, { AxiosError } from 'axios'
import { ElMessage } from 'element-plus'

export const http = axios.create({ baseURL: '', timeout: 30000 })

export interface ApiError {
  type: 'NOT_FOUND' | 'SERVER_ERROR' | 'NETWORK_ERROR' | 'BAD_REQUEST' | 'UNKNOWN'
  detail?: string
}

http.interceptors.response.use(
  (resp) => resp,
  (err: AxiosError<{ detail?: string }>) => {
    let apiErr: ApiError
    if (!err.response) {
      apiErr = { type: 'NETWORK_ERROR', detail: err.message }
      ElMessage.error('网络错误，请检查后端服务')
    } else if (err.response.status === 404) {
      apiErr = { type: 'NOT_FOUND', detail: err.response.data?.detail }
    } else if (err.response.status === 400) {
      apiErr = { type: 'BAD_REQUEST', detail: err.response.data?.detail }
      ElMessage.error(`请求错误：${apiErr.detail ?? ''}`)
    } else if (err.response.status >= 500) {
      apiErr = { type: 'SERVER_ERROR', detail: err.response.data?.detail }
      ElMessage.error('服务异常，请稍后重试')
    } else {
      apiErr = { type: 'UNKNOWN', detail: err.message }
    }
    return Promise.reject(apiErr)
  },
)
