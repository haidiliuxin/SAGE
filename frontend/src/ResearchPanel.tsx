import { useEffect, useRef, useState } from 'react'
import { api } from './api/client'
import type { Metrics, ResearchEvent, ResearchPage, ResearchSummary, RunPage } from './research-types'
import type { RunResult } from './types'

const names: Record<string, string> = {
  fixed: '固定顺序', round_robin: '轮询', heuristic_bandit: '启发式 Bandit', ucb: 'UCB', cost_aware_ucb: '成本感知 UCB',
  running: '执行中', paused: '已暂停', completed: '已完成', failed: '失败', cancelled: '已取消',
  run_started: '运行开始／恢复', decision_started: '批次开始', decision_completed: '批次完成', decision_interrupted: '批次中断，反馈未知', run_finished: '运行结束', checkpoint: '检查点',
  initial_selection: '首次选择', exploration: '探索', policy_selection: '调度策略选择', same_arm: '继续当前单元', arm_unavailable: '前一单元不可用',
  candidate_budget: '候选预算耗尽', time_budget: '时间预算耗尽', candidates_exhausted: '候选已耗尽', strategy_budgets: '策略预算耗尽', all_targets_recovered: '全部目标已恢复',
  shutdown: '服务关闭', launch_failed: '启动失败', execution_failed: '执行失败', internal_error: '内部错误', logging_failed: '日志写入失败', outcome_unknown: '反馈未知',
  score: '最终评分', mean_reward: '平均学习收益', exploration_bonus: '探索项', upper_bound: '收益上界', predicted_seconds: '预计本批耗时（秒）',
  success_probability: '成功率估计', recent_gain: '近期收益', transfer: '迁移项', cost: '成本项', untried: '尚未尝试',
  cost_samples: '完整成本样本', censored_samples: '不完整样本', cost_confidence: '估计质量（0–1）', recent_throughput: '近期吞吐（候选/秒）', last_batch_throughput: '上一批吞吐（候选/秒）',
  estimated_startup: '启动开销（秒）', estimated_seconds_per_candidate: '单候选耗时（秒）',
  recovery_gain: '恢复收益', time_penalty: '时间惩罚', candidate_penalty: '候选消耗惩罚', duplicate_penalty: '重复惩罚', total: '统一评价奖励',
}
const label = (value?: string | null) => value ? names[value] ?? value : '—'
const num = (value: unknown) => typeof value === 'number' ? new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 6 }).format(value) : '—'
const errorText = (error: unknown) => error instanceof Error ? error.message : '读取失败'

function Values({ values, keys }: { values?: Metrics; keys: string[] }) {
  return <dl className="research-values">{keys.map(key => <div key={key}><dt>{label(key)}</dt><dd>{num(values?.[key])}</dd></div>)}</dl>
}

function EventRow({ event }: { event: ResearchEvent }) {
  const p = event.payload
  return <details className="research-event">
    <summary>第 {event.round_index} 轮 · {label(event.event_type)} {p.decision && `· ${p.decision.arm_id}`} {p.feedback && `· 新增恢复 ${p.feedback.recovered}`} {p.reward != null && `· 奖励 ${num(p.reward)}`}</summary>
    <p>事件 #{event.sequence} · 执行尝试 {event.attempt_id} · 切换：{label(p.switch_reason)} · 停止：{label(p.stop_reason)}</p>
    <p>提交 {num(p.feedback?.candidate_count)} · 测试 {num(p.feedback?.tested)} · 耗时 {num(p.feedback?.duration)} 秒</p>
    <details><summary>完整脱敏记录（含决策前后状态）</summary><pre>{JSON.stringify(event, null, 2)}</pre></details>
  </details>
}

export function ResearchPanel({ runId }: { runId: string }) {
  const [summary, setSummary] = useState<ResearchSummary | null>(null)
  const [page, setPage] = useState<ResearchPage | null>(null)
  const [error, setError] = useState('')
  const [older, setOlder] = useState(false)
  const [loading, setLoading] = useState(false)
  const [revision, setRevision] = useState(0)
  const generation = useRef(0)
  const paging = useRef(0)
  const olderRef = useRef(false)

  useEffect(() => {
    const current = ++generation.current
    let active = true
    let timer: number | undefined
    setSummary(null); setPage(null); setError(''); setOlder(false); setLoading(false)
    olderRef.current = false
    const load = async () => {
      try {
        const data = await api.getResearch(runId)
        if (!active) return
        setSummary(data)
        const pageVersion = paging.current
        if (!olderRef.current) {
          const events = await api.getResearchEvents(runId)
          if (!active) return
          if (!olderRef.current && pageVersion === paging.current) setPage(events)
        }
        setError('')
        if (['running', 'paused'].includes(data.status) || (data.available && !data.stop_reason)) timer = window.setTimeout(load, 3000)
      } catch (caught) {
        if (!active) return
        setError(errorText(caught))
        timer = window.setTimeout(load, 5000)
      }
    }
    void load()
    return () => { active = false; window.clearTimeout(timer); if (generation.current === current) generation.current++ }
  }, [runId, revision])

  const browse = async (before?: number) => {
    const current = generation.current
    const requestId = ++paging.current
    olderRef.current = true
    setLoading(true)
    try {
      const events = await api.getResearchEvents(runId, before)
      if (current !== generation.current || requestId !== paging.current) return
      setPage(events); setOlder(before !== undefined); olderRef.current = before !== undefined; setError('')
    } catch (caught) {
      if (current === generation.current) { olderRef.current = older; setError(errorText(caught)) }
    } finally {
      if (current === generation.current) setLoading(false)
    }
  }

  const decision = summary?.latest_decision
  const p = decision?.payload
  const completed = summary?.latest_completed
  const reward = completed?.payload
  const config = summary?.configuration
  const costKeys = ['estimated_startup', 'estimated_seconds_per_candidate', 'cost_samples', 'censored_samples', 'cost_confidence', 'recent_throughput', 'last_batch_throughput']
  return <section className="panel research-panel" aria-label="运行研究详情">
    <div className="panel-head"><div><span className="section-kicker">DECISION RESEARCH</span><h2>调度与研究记录</h2><small>运行 {runId} · {label(summary?.status)}</small></div>
      <button className="button secondary" onClick={() => setRevision(v => v + 1)}>刷新</button></div>
    {error && <p role="alert" className="research-error">研究数据读取失败：{error}（已显示的数据可能不是最新）</p>}
    {!summary && !error && <p>正在读取持久化运行记录…</p>}
    {summary && !summary.available && <p>该运行暂无研究日志。Mock 或接入研究日志前的历史运行不会补造评分和奖励。</p>}
    {summary?.available && <>
      <div className="research-badges"><span>本次调度：{label(config?.policy_type)}</span><span>模式：{config?.mode ?? '—'}</span><span>决策轮次：{decision?.round_index ?? 0}</span><span>停止原因：{label(summary.stop_reason)}</span></div>
      <div className="research-badges"><span>剩余候选：{num(summary.latest_state?.remaining_candidates)}</span><span>剩余时间：{num(summary.latest_state?.remaining_time)} 秒</span><span>初始目标：{num(config?.reward_context.initial_targets)}</span></div>
      <details><summary>本次保存的运行配置</summary><pre>{JSON.stringify(config, null, 2)}</pre></details>
      <h3>结果汇总 · 已有完整反馈的轮次</h3>
      <div className="research-badges"><span>完成 {summary.totals.completed_rounds} 轮</span><span>提交 {num(summary.totals.submitted_candidates)}</span><span>测试 {num(summary.totals.tested_candidates)}</span><span>新增恢复目标 {num(summary.totals.recovered_targets)}</span><span>批次耗时 {num(summary.totals.duration)} 秒</span><span>累计评价奖励 {num(summary.totals.evaluation_reward)}</span></div>
      <h3>最近决策 {decision && `· 第 ${decision.round_index} 轮 · ${label(decision.event_type)}`}</h3>
      {decision?.event_type === 'decision_started' && !['running', 'paused'].includes(summary.status) && <p>本次运行已结束，但该批次没有完整反馈，执行结果未知。</p>}
      <p>调度单元 {p?.decision?.arm_id ?? '—'} · 策略 {p?.decision?.strategy_id ?? '—'} · 批量 {num(p?.decision?.candidate_limit)} · 时间上限 {num(p?.decision?.time_limit)} 秒 · 切换原因：{label(p?.switch_reason)}</p>
      {p?.scores_used_for_selection === false && <p>本算法按固定顺序或轮询选择，下表评分仅作诊断。</p>}
      <div className="research-table-wrap"><table className="research-table"><thead><tr><th>调度单元</th><th>可用批量</th><th>决策前已选次数</th><th>状态</th><th>评分与分解</th></tr></thead><tbody>
        {Array.from(new Set([...Object.keys(p?.available_batches ?? {}), ...Object.keys(p?.scores ?? {})])).sort().map(arm => <tr key={arm}><th>{arm}</th><td>{num(p?.available_batches?.[arm])}</td><td>{num(p?.prior_state?.arms[arm]?.pulls)}</td><td>{p?.available_arms?.includes(arm) ? '可用' : '不可用'}{p?.decision?.arm_id === arm ? ' · 已选择' : ''}</td><td>{p?.scores?.[arm] ? <Values values={p.scores[arm]} keys={Object.keys(p.scores[arm])} /> : '暂无评分'}</td></tr>)}
      </tbody></table></div>
      <h3>最近完整反馈与奖励 {completed && `· 第 ${completed.round_index} 轮`}</h3>
      <p>新增恢复 {num(reward?.feedback?.recovered)} · 提交 {num(reward?.feedback?.candidate_count)} · 测试 {num(reward?.feedback?.tested)} · 重复 {num(reward?.feedback?.duplicate_count)} · 实际耗时 {num(reward?.feedback?.duration)} 秒</p>
      <dl className="research-values">{['recovery_gain', 'time_penalty', 'candidate_penalty', 'duplicate_penalty', 'total'].map(key => <div key={key}><dt>{label(key)}</dt><dd>{num(reward?.reward_breakdown?.[key])}</dd></div>)}<div><dt>UCB 学习收益</dt><dd>{num(reward?.learning_reward)}</dd></div></dl>
      <p className="research-note">评价奖励 = 恢复收益 − 各项惩罚；学习收益另列。未知测量显示“—”。</p>
      <h3>更新后的成本估计</h3>
      {Object.entries(summary.latest_state?.arms ?? {}).filter(([, stats]) => 'cost_samples' in stats).map(([arm, stats]) => <div key={arm}><h4>{arm}</h4><Values values={stats} keys={costKeys} /></div>)}
      {!Object.values(summary.latest_state?.arms ?? {}).some(stats => 'cost_samples' in stats) && <p>本次调度没有在线成本估计。</p>}
      <p className="research-note">预计本批耗时见决策评分表；成本估计质量是启发式指标，不是统计置信概率。吞吐未知时不补零。</p>
      <div className="panel-head"><h3>决策时间线 {older ? '· 历史页' : '· 最近事件'}</h3><a className="button secondary" href={api.researchDownloadUrl(runId)}>下载完整脱敏日志</a></div>
      <p className="research-note">每页最多 50 条，按事件顺序展示。下载包含请求开始时已落盘的所有执行尝试；中断且反馈未知的批次不计入奖励汇总。</p>
      {page?.items.map(event => <EventRow key={event.sequence} event={event} />)}
      <div className="research-actions"><button className="button secondary" disabled={loading || !page?.has_more || page.next_before_sequence === null} onClick={() => void browse(page?.next_before_sequence ?? undefined)}>更早的事件</button><button className="button secondary" disabled={loading || !older} onClick={() => void browse()}>回到最新</button></div>
    </>}
  </section>
}

export function RunResultView({ runId, mode }: { runId: string; mode?: string }) {
  const [result, setResult] = useState<RunResult | null>(null)
  const [error, setError] = useState('')
  const [retry, setRetry] = useState(0)
  const [copied, setCopied] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    setResult(null); setError('')
    api.getResult(runId).then(data => { if (active) setResult(data) }).catch(caught => { if (active) setError(errorText(caught)) })
    return () => { active = false }
  }, [runId, retry])

  const copy = async (value: string) => {
    try {
      const clipboard = globalThis.navigator?.clipboard
      if (!clipboard) throw new Error('clipboard unavailable')
      await clipboard.writeText(value)
      setCopied(value)
    } catch {
      setCopied(null)
    }
  }

  if (error) {
    return <div className="run-result"><p role="alert">结果读取失败：{error} <button className="button secondary" onClick={() => setRetry(v => v + 1)}>重试</button></p></div>
  }
  if (!result) return <div className="run-result"><p>正在读取运行结果…</p></div>

  const items = result.recovered_items ?? []
  return <div className="run-result">
    <div className="research-badges"><span>状态 {label(result.status)}</span><span>测试 {num(result.total_tested)} 个候选</span><span>恢复 {num(result.total_recovered)} 项</span><span>耗时 {num(result.total_time)} 秒</span></div>
    {items.length > 0
      ? <ul className="recovered-list">{items.map((item, index) => <li key={`${item.target}-${index}`}>
          <code className="recovered-plaintext">{item.plaintext}</code>
          <button type="button" className="button secondary" onClick={() => void copy(item.plaintext)}>{copied === item.plaintext ? '已复制' : '复制'}</button>
          <small className="recovered-target">来源目标 {item.target.length > 28 ? `${item.target.slice(0, 28)}…` : item.target}</small>
        </li>)}</ul>
      : <p>{mode === 'mock'
          ? '本次为模拟执行（Mock）：只产生模拟统计，没有真实明文。'
          : '该次真实运行没有恢复出明文（候选或预算耗尽，或口令不在计划候选内）。'}</p>}
    {result.strategy_results?.length ? <table className="research-table"><thead><tr><th>策略</th><th>测试</th><th>恢复</th><th>耗时（秒）</th></tr></thead><tbody>
      {result.strategy_results.map(item => <tr key={item.strategy_id}><th>{item.strategy_id}</th><td>{num(item.tested)}</td><td>{num(item.recovered)}</td><td>{num(item.time)}</td></tr>)}
    </tbody></table> : null}
    <p className="research-note">明文来自该次授权运行的持久化结果，仅在本地界面展示，不写入跨任务知识库。</p>
  </div>
}

export function TaskRuns({ taskId, onOpen }: { taskId: string; onOpen: (runId: string) => void }) {
  const [page, setPage] = useState<RunPage | null>(null)
  const [offset, setOffset] = useState(0)
  const [error, setError] = useState('')
  const [retry, setRetry] = useState(0)
  const [resultRunId, setResultRunId] = useState<string | null>(null)
  useEffect(() => {
    let active = true
    setPage(null); setError('')
    api.getTaskRuns(taskId, offset).then(data => { if (active) setPage(data) }).catch(caught => { if (active) setError(errorText(caught)) })
    return () => { active = false }
  }, [taskId, offset, retry])
  return <section className="research-history"><h3>历史运行与研究详情</h3>
    {error && <p role="alert">{error} <button onClick={() => setRetry(v => v + 1)}>重试</button></p>}
    {!page && !error && <p>正在读取运行记录…</p>}
    {page?.items.map(run => <div className="research-run" key={run.run_id}>
      <div><strong>{run.run_id}</strong><p>{run.mode} · {label(run.status)} · {run.started_at ? new Date(run.started_at).toLocaleString('zh-CN') : '启动时间未知'}</p></div>
      <div className="research-run-actions">
        <button className="button secondary" onClick={() => setResultRunId(current => current === run.run_id ? null : run.run_id)}>{resultRunId === run.run_id ? '收起结果' : '查看结果'}</button>
        <button className="button secondary" onClick={() => onOpen(run.run_id)}>查看研究详情</button>
      </div>
      {resultRunId === run.run_id && <RunResultView runId={run.run_id} mode={run.mode} />}
    </div>)}
    {page?.total === 0 && <p>此任务尚无运行记录。</p>}
    <div className="research-actions"><button disabled={!page || offset === 0} onClick={() => setOffset(Math.max(0, offset - 20))}>上一页</button><button disabled={!page || offset + page.items.length >= page.total} onClick={() => setOffset(offset + 20)}>下一页</button></div>
  </section>
}
