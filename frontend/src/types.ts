export type TaskStatus =
  | 'created'
  | 'analyzed'
  | 'planned'
  | 'running'
  | 'paused'
  | 'completed'
  | 'failed'
  | 'cancelled'

export type TargetType = 'hash' | 'zip' | 'pdf' | 'office' | 'unknown'
export type PlannerType = 'mock' | 'rule' | 'llm' | 'adaptive'
export type ExecutionMode = 'mock' | 'real'
export type StrategyId = 'S1' | 'S2' | 'S3' | 'S4' | 'S5'

export interface TaskInput {
  name: string
  target: {
    type: TargetType
    content: string | null
    file_id: string | null
  }
  known_algorithm: string | null
  time_budget: number
  candidate_budget: number
  context: {
    keywords: string[]
    years: number[]
    region: string
    organization: string
    description: string
  }
}

export interface TaskCreated {
  task_id: string
  status: TaskStatus
  created_at: string
}

export interface TaskDetail extends TaskCreated {
  name: string
  target: {
    type: TargetType
    content: string | null
    file_id: string | null
  }
  known_algorithm: string | null
  time_budget: number
  candidate_budget: number
  context: {
    keywords: string[]
    years: number[]
    region: string | null
    organization: string | null
    description: string | null
  }
  updated_at: string
}

export interface TaskListResponse {
  items: TaskDetail[]
  total: number
  limit: number
  offset: number
}

export interface FileDetail {
  file_id: string
  filename: string
  content_type: string | null
  size: number
  sha256: string
  created_at: string
}

export interface PRIR {
  task_id: string
  target_type: TargetType
  algorithm: string
  salt: boolean | null
  verification_cost: 'low' | 'medium' | 'high' | 'unknown'
  context_available: boolean
  candidate_space: number | null
  time_budget: number
  candidate_budget: number
  status: TaskStatus
  confidence: number
  warnings: string[]
}

export interface Strategy {
  strategy_id: StrategyId
  strategy_name: string
  priority: number
  time_budget: number
  candidate_budget: number
  reason: string
  parameters: Record<string, unknown>
}

export interface StrategyPlan {
  task_id: string
  planner_type: PlannerType
  total_time_budget: number
  strategies: Strategy[]
  status: TaskStatus
  warnings: string[]
}

export interface RunCreated {
  task_id: string
  run_id: string
  status: TaskStatus
  started_at: string
}

export interface RunStatus {
  task_id: string
  run_id: string
  status: TaskStatus
  progress: number
  current_strategy: string | null
  elapsed_time: number
  tested: number
  recovered: number
  message: string
}

export interface StrategyResult {
  strategy_id: string
  time: number
  tested: number
  recovered: number
  success_rate: number
}

export interface RunResult {
  task_id: string
  run_id: string
  status: TaskStatus
  total_time: number
  total_tested: number
  total_recovered: number
  strategy_results: StrategyResult[]
  finished_at: string
  recovered_items: Array<{ target: string; plaintext: string }>
  message: string | null
}

export interface FlowSnapshot {
  input: TaskInput
  task: TaskCreated | null
  prir: PRIR | null
  plan: StrategyPlan | null
  run: RunCreated | null
  status: RunStatus | null
  result: RunResult | null
}
