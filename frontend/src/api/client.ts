import type {
  FileDetail,
  ExecutionMode,
  PRIR,
  RunCreated,
  RunResult,
  RunStatus,
  StrategyPlan,
  SystemConfig,
  TaskCreated,
  TaskDetail,
  TaskListResponse,
  TaskInput,
  TaskStatus,
} from '../types'
import type { ResearchPage, ResearchSummary, RunPage } from '../research-types'
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
  getTaskRuns(taskId: string, offset = 0) {
    return request<RunPage>(`/api/tasks/${encodeURIComponent(taskId)}/runs?limit=20&offset=${offset}`)
  },
  getResearch(runId: string) {
    return request<ResearchSummary>(`/api/runs/${encodeURIComponent(runId)}/research`)
  },
  getResearchEvents(runId: string, before?: number) {
    const query = before === undefined ? 'latest=true' : `before_sequence=${before}`
    return request<ResearchPage>(`/api/runs/${encodeURIComponent(runId)}/research/events?limit=50&${query}`)
  },
  researchDownloadUrl(runId: string) {
    return `${baseUrl}/api/runs/${encodeURIComponent(runId)}/research/download`
  },
  health() {
    return request<{ status: string }>('/health')
  },

  uploadFile(file: File) {
    const data = new FormData()
    data.append('file', file)
    return request<FileDetail>('/api/files', { method: 'POST', body: data })
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

  execute(
    taskId: string,
    mode: ExecutionMode = 'mock',
    options: { candidates?: string[]; stop_on_hit?: boolean } = {},
  ) {
    return request<RunCreated>(`/api/tasks/${taskId}/execute`, {
      method: 'POST',
      body: JSON.stringify({
        mode,
        candidates: options.candidates ?? [],
        stop_on_hit: options.stop_on_hit,
      }),
    })
  },

  getStatus(runId: string) {
    return request<RunStatus>(`/api/runs/${runId}/status`)
  },

  getResult(runId: string) {
    return request<RunResult>(`/api/runs/${runId}/result`)
  },

  getSystemConfig() {
    return request<SystemConfig>('/api/system/config')
  },
}
