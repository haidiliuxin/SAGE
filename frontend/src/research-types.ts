export type Metrics = Record<string, number | null>
export interface ResearchState {
  arms: Record<string, Metrics>
  remaining_candidates: number
  remaining_time: number
}
export interface DecisionPayload {
  available_arms?: string[]
  available_batches?: Record<string, number>
  prior_state?: ResearchState
  updated_state?: ResearchState
  state?: ResearchState
  decision?: { arm_id: string; strategy_id: string; candidate_limit: number; time_limit: number; exploration: boolean }
  scores?: Record<string, Metrics>
  scores_used_for_selection?: boolean
  selection_rule?: string
  switch_reason?: string | null
  stop_reason?: string | null
  feedback?: { candidate_count: number; tested: number; recovered: number; duration: number; duplicate_count: number | null; status: string } | null
  reward?: number | null
  learning_reward?: number
  reward_breakdown?: Record<string, number | boolean | string | null>
}
export interface ResearchEvent {
  sequence: number
  schema_version: number
  run_id: string
  attempt_id: string
  round_index: number
  event_type: string
  payload: DecisionPayload
}
export interface ResearchSummary {
  schema_version: number
  run_id: string
  task_id: string
  status: string
  available: boolean
  configuration: {
    mode: string; policy_type: string; policy_version: string; reward_version: string
    learning_reward: string | null; policy_parameters: Metrics; reward_weights: Metrics
    reward_context: { initial_targets: number; candidate_budget: number; time_budget: number }
  } | null
  latest_decision: ResearchEvent | null
  latest_completed: ResearchEvent | null
  latest_state: ResearchState | null
  stop_reason: string | null
  totals: { completed_rounds: number; submitted_candidates: number; tested_candidates: number; recovered_targets: number; duration: number; evaluation_reward: number }
  through_sequence: number
}
export interface ResearchPage {
  items: ResearchEvent[]
  next_after_sequence: number
  next_before_sequence: number | null
  has_more: boolean
}
export interface RunReference {
  run_id: string; task_id: string; mode: string; status: string
  started_at: string | null; finished_at: string | null
}
export interface RunPage { items: RunReference[]; total: number; limit: number; offset: number }
