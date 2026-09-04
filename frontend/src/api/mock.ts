import type {
  PRIR,
  RunCreated,
  RunResult,
  RunStatus,
  StrategyPlan,
  TaskCreated,
  TaskInput,
} from '../types'

const delay = (ms = 520) => new Promise((resolve) => window.setTimeout(resolve, ms))
const id = (prefix: string) => `${prefix}${Date.now().toString().slice(-6)}`

const runs = new Map<string, { started: number; taskId: string }>()

export const mockApi = {
  async uploadFile(file: File) {
    await delay()
    return { file_id: `F-${file.name.replace(/[^a-zA-Z0-9]/g, '').slice(0, 10) || 'UPLOAD'}` }
  },

  async createTask(input: TaskInput): Promise<TaskCreated> {
    await delay()
    return { task_id: id('T'), status: 'created', created_at: new Date().toISOString() }
  },

  async analyze(taskId: string, input: TaskInput): Promise<PRIR> {
    await delay(680)
    const isSlow = /bcrypt|argon|pbkdf/i.test(input.known_algorithm ?? '')
    return {
      task_id: taskId,
      target_type: input.target.type,
      algorithm: input.known_algorithm || (input.target.type === 'hash' ? 'unknown' : input.target.type),
      salt: isSlow ? true : null,
      verification_cost: isSlow ? 'high' : input.target.type === 'hash' ? 'low' : 'medium',
      context_available: Boolean(
        input.context.keywords.length || input.context.years.length || input.context.description,
      ),
      candidate_space: Math.max(input.candidate_budget * 10, 1_000_000),
      time_budget: input.time_budget,
      candidate_budget: input.candidate_budget,
      status: 'analyzed',
      confidence: input.known_algorithm ? 0.95 : 0.78,
      warnings: input.known_algorithm ? [] : ['未指定算法，当前为模拟识别结果。'],
    }
  },

  async plan(taskId: string, input: TaskInput): Promise<StrategyPlan> {
    await delay(620)
    const hasContext = input.context.keywords.length > 0 || input.context.years.length > 0
    const strategies: StrategyPlan['strategies'] = [
      {
        strategy_id: 'S1',
        strategy_name: 'Baseline',
        priority: 1,
        time_budget: Math.round(input.time_budget * 0.25),
        candidate_budget: Math.round(input.candidate_budget * 0.3),
        reason: '先用高频候选建立基线收益',
        parameters: {},
      },
      {
        strategy_id: hasContext ? 'S4' : 'S3',
        strategy_name: hasContext ? 'Context' : 'PCFG-lite',
        priority: 2,
        time_budget: Math.round(input.time_budget * 0.45),
        candidate_budget: Math.round(input.candidate_budget * 0.45),
        reason: hasContext ? '利用任务上下文缩小候选空间' : '利用结构概率提升候选质量',
        parameters: hasContext ? { use_years: true, use_keywords: true } : { depth: 3 },
      },
      {
        strategy_id: 'S2',
        strategy_name: 'Rule',
        priority: 3,
        time_budget: Math.round(input.time_budget * 0.2),
        candidate_budget: Math.round(input.candidate_budget * 0.2),
        reason: '对高质量种子执行常见变换规则',
        parameters: { casing: true, suffix: true },
      },
    ]
    return {
      task_id: taskId,
      planner_type: 'mock',
      total_time_budget: input.time_budget,
      strategies,
      status: 'planned',
      warnings: [],
    }
  },

  async execute(taskId: string): Promise<RunCreated> {
    await delay(500)
    const runId = id('R')
    runs.set(runId, { started: Date.now(), taskId })
    return { task_id: taskId, run_id: runId, status: 'running', started_at: new Date().toISOString() }
  },

  async getStatus(runId: string): Promise<RunStatus> {
    await delay(180)
    const run = runs.get(runId) ?? { started: Date.now() - 9000, taskId: 'T-DEMO' }
    const elapsed = (Date.now() - run.started) / 1000
    const progress = Math.min(1, elapsed / 10)
    const stage = progress < 0.3 ? 'S1' : progress < 0.75 ? 'S4' : 'S2'
    return {
      task_id: run.taskId,
      run_id: runId,
      status: progress >= 1 ? 'completed' : 'running',
      progress,
      current_strategy: progress >= 1 ? null : stage,
      elapsed_time: Number(elapsed.toFixed(1)),
      tested: Math.round(100_000 * progress),
      recovered: progress > 0.8 ? 3 : progress > 0.38 ? 2 : progress > 0.15 ? 1 : 0,
      message: progress >= 1 ? '评测完成，正在汇总结果' : `正在执行 ${stage} 策略`,
    }
  },

  async getResult(runId: string): Promise<RunResult> {
    await delay(420)
    const taskId = runs.get(runId)?.taskId ?? 'T-DEMO'
    return {
      task_id: taskId,
      run_id: runId,
      status: 'completed',
      total_time: 10.2,
      total_tested: 100_000,
      total_recovered: 3,
      strategy_results: [
        { strategy_id: 'S1', time: 2.5, tested: 30_000, recovered: 1, success_rate: 0.000033 },
        { strategy_id: 'S4', time: 4.7, tested: 45_000, recovered: 2, success_rate: 0.000044 },
        { strategy_id: 'S2', time: 3, tested: 25_000, recovered: 0, success_rate: 0 },
      ],
      finished_at: new Date().toISOString(),
    }
  },
}
