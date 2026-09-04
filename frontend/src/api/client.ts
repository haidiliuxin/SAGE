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
import { mockApi } from './mock'

const baseUrl = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')
const mockFallback = import.meta.env.VITE_ENABLE_MOCK_FALLBACK !== 'false'

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

async function withFallback<T>(remote: () => Promise<T>, local: () => Promise<T>): Promise<T> {
  try {
    return await remote()
  } catch (error) {
    if (!mockFallback || (error instanceof ApiError && error.code !== 'NETWORK_ERROR')) throw error
    return local()
  }
}

export const api = {
  isFallbackEnabled: mockFallback,

  health() {
    return request<{ status: string }>('/health')
  },

  uploadFile(file: File) {
    const data = new FormData()
    data.append('file', file)
    return withFallback(
      () => request<{ file_id: string }>('/api/files', { method: 'POST', body: data }),
      () => mockApi.uploadFile(file),
    )
  },

  createTask(input: TaskInput) {
    return withFallback(
      () => request<TaskCreated>('/api/tasks', { method: 'POST', body: JSON.stringify(input) }),
      () => mockApi.createTask(input),
    )
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

  analyze(taskId: string, input: TaskInput) {
    return withFallback(
      () => request<PRIR>(`/api/tasks/${taskId}/analyze`, { method: 'POST' }),
      () => mockApi.analyze(taskId, input),
    )
  },

  plan(taskId: string, input: TaskInput) {
    return withFallback(
      () => request<StrategyPlan>(`/api/tasks/${taskId}/plan`, { method: 'POST' }),
      () => mockApi.plan(taskId, input),
    )
  },

  execute(taskId: string) {
    return withFallback(
      () =>
        request<RunCreated>(`/api/tasks/${taskId}/execute`, {
          method: 'POST',
          body: JSON.stringify({ mode: 'mock' }),
        }),
      () => mockApi.execute(taskId),
    )
  },

  getStatus(runId: string) {
    return withFallback(
      () => request<RunStatus>(`/api/runs/${runId}/status`),
      () => mockApi.getStatus(runId),
    )
  },

  getResult(runId: string) {
    return withFallback(
      () => request<RunResult>(`/api/runs/${runId}/result`),
      () => mockApi.getResult(runId),
    )
  },
}
