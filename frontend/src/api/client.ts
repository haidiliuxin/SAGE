import type {
  FileDetail,
  PRIR,
  RunCreated,
  RunResult,
  RunStatus,
  StrategyPlan,
  TaskCreated,
  TaskDetail,
  TaskListResponse,
  TaskInput,
  TaskStatus,
} from '../types'
const baseUrl = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

export class ApiError extends Error {
  code: string
  details?: unknown

  constructor(message: string, code = 'NETWORK_ERROR', details?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.details = details
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    headers: {
      ...(init?.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...init?.headers,
    },
  })

  const body = await response.json().catch(() => null)
  if (!response.ok) {
    const error = body?.error
    throw new ApiError(error?.message ?? `请求失败（HTTP ${response.status}）`, error?.code, error?.details)
  }
  return body as T
}

export const api = {
  health() {
    return request<{ status: string }>('/health')
  },

  uploadFile(file: File) {
    const data = new FormData()
    data.append('file', file)
    return request<{ file_id: string }>('/api/files', { method: 'POST', body: data })
  },

  createTask(input: TaskInput) {
    return request<TaskCreated>('/api/tasks', { method: 'POST', body: JSON.stringify(input) })
  },

  listTasks() {
    return request<TaskListResponse>('/api/tasks?limit=100&offset=0')
  },

  getTask(taskId: string) {
    return request<TaskDetail>(`/api/tasks/${taskId}`)
  },

  updateTaskStatus(taskId: string, status: TaskStatus) {
    return request<TaskDetail>(`/api/tasks/${taskId}/status`, {
      method: 'PATCH',
      body: JSON.stringify({ status }),
    })
  },

  getFile(fileId: string) {
    return request<FileDetail>(`/api/files/${fileId}`)
  },

  analyze(taskId: string) {
    return request<PRIR>(`/api/tasks/${taskId}/analyze`, { method: 'POST' })
  },

  plan(taskId: string) {
    return request<StrategyPlan>(`/api/tasks/${taskId}/plan`, { method: 'POST' })
  },

  execute(taskId: string) {
    return request<RunCreated>(`/api/tasks/${taskId}/execute`, {
      method: 'POST',
      body: JSON.stringify({ mode: 'mock' }),
    })
  },

  getStatus(runId: string) {
    return request<RunStatus>(`/api/runs/${runId}/status`)
  },

  getResult(runId: string) {
    return request<RunResult>(`/api/runs/${runId}/result`)
  },
}
