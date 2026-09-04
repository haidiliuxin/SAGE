import { FormEvent, ReactNode, useEffect, useMemo, useRef, useState } from 'react'
import { api, ApiError } from './api/client'
import { parseContextLists } from './context-input'
import type { FileDetail, FlowSnapshot, RunStatus, TargetType, TaskDetail, TaskInput, TaskStatus } from './types'

type View = 'overview' | 'new' | 'workspace' | 'tasks'
type FlowStage = 'idle' | 'uploading' | 'creating' | 'analyzing' | 'planning' | 'executing' | 'completed' | 'cancelled' | 'error'

const blankInput: TaskInput = {
  name: 'bcrypt 教学评测任务',
  target: { type: 'hash', content: '$2b$12$example.hash.for.authorized.testing', file_id: null },
  known_algorithm: 'bcrypt',
  time_budget: 300,
  candidate_budget: 100000,
  context: {
    keywords: ['学校名称', '实验室'],
    years: [2025, 2026],
    region: '北京',
    organization: '示例大学',
    description: '用于竞赛演示的已授权离线样本',
  },
}

const initialSnapshot: FlowSnapshot = {
  input: blankInput,
  task: null,
  prir: null,
  plan: null,
  run: null,
  status: null,
  result: null,
}

const stageLabels: Record<FlowStage, string> = {
  idle: '等待开始',
  uploading: '正在接入文件',
  creating: '正在创建任务',
  analyzing: '正在生成 PRIR',
  planning: '正在编排策略',
  executing: '正在模拟执行',
  completed: '评测已完成',
  cancelled: '任务已取消',
  error: '流程已中断',
}

const taskStatusLabels: Record<TaskStatus, string> = {
  created: '已创建',
  analyzed: '已分析',
  planned: '已规划',
  running: '执行中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

const workflow = [
  { key: 'creating', index: '01', title: '任务接入', caption: '统一目标与预算' },
  { key: 'analyzing', index: '02', title: 'PRIR 分析', caption: '识别类型与成本' },
  { key: 'planning', index: '03', title: '策略规划', caption: '生成可信计划' },
  { key: 'executing', index: '04', title: '动态执行', caption: '轮询进度与收益' },
  { key: 'completed', index: '05', title: '结果反馈', caption: '汇总策略表现' },
] as const

const stageOrder: FlowStage[] = ['idle', 'uploading', 'creating', 'analyzing', 'planning', 'executing', 'completed']

function Icon({ name, size = 18 }: { name: string; size?: number }) {
  const paths: Record<string, ReactNode> = {
    grid: <><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></>,
    plus: <><path d="M12 5v14M5 12h14"/></>,
    layers: <><path d="m12 2 9 5-9 5-9-5 9-5Z"/><path d="m3 12 9 5 9-5"/><path d="m3 17 9 5 9-5"/></>,
    list: <><path d="M8 6h13M8 12h13M8 18h13"/><path d="M3 6h.01M3 12h.01M3 18h.01"/></>,
    shield: <><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z"/><path d="m9 12 2 2 4-4"/></>,
    arrow: <><path d="M5 12h14M13 6l6 6-6 6"/></>,
    check: <path d="m5 12 4 4L19 6"/>,
    upload: <><path d="M12 16V4M7 9l5-5 5 5"/><path d="M4 15v5h16v-5"/></>,
    play: <path d="m8 5 11 7-11 7V5Z"/>,
    target: <><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4"/><path d="M12 3v3M21 12h-3M12 21v-3M3 12h3"/></>,
    pulse: <><path d="M3 12h4l2-6 4 12 2-6h6"/></>,
    clock: <><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>,
    menu: <><path d="M4 7h16M4 12h16M4 17h16"/></>,
    close: <><path d="m6 6 12 12M18 6 6 18"/></>,
  }
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>
}

function formatNumber(value?: number | null) {
  if (value === null || value === undefined) return '—'
  return new Intl.NumberFormat('zh-CN', { notation: value >= 1000000 ? 'compact' : 'standard' }).format(value)
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}

function App() {
  const [view, setView] = useState<View>('overview')
  const [mobileNav, setMobileNav] = useState(false)
  const [form, setForm] = useState<TaskInput>(blankInput)
  const [keywordText, setKeywordText] = useState(blankInput.context.keywords.join('，'))
  const [yearText, setYearText] = useState(blankInput.context.years.join('，'))
  const [file, setFile] = useState<File | null>(null)
  const [stage, setStage] = useState<FlowStage>('idle')
  const [snapshot, setSnapshot] = useState<FlowSnapshot>(initialSnapshot)
  const [error, setError] = useState('')
  const [tasks, setTasks] = useState<TaskDetail[]>([])
  const [tasksLoading, setTasksLoading] = useState(false)
  const [tasksError, setTasksError] = useState('')
  const [health, setHealth] = useState<'checking' | 'online' | 'offline'>('checking')
  const [selectedTask, setSelectedTask] = useState<TaskDetail | null>(null)
  const [selectedFile, setSelectedFile] = useState<FileDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [taskActionError, setTaskActionError] = useState('')
  const [cancellingTaskId, setCancellingTaskId] = useState<string | null>(null)
  const runToken = useRef(0)

  const progress = snapshot.status?.progress ?? (stage === 'completed' ? 1 : 0)
  const completedStep = useMemo(() => stageOrder.indexOf(stage), [stage])

  useEffect(() => {
    let active = true
    const check = async () => {
      try {
        const response = await api.health()
        if (active) setHealth(response.status === 'ok' ? 'online' : 'offline')
      } catch {
        if (active) setHealth('offline')
      }
    }
    void check()
    const timer = window.setInterval(check, 15000)
    return () => {
      active = false
      window.clearInterval(timer)
    }
  }, [])

  useEffect(() => {
    if (view !== 'tasks') return

    let active = true
    setTasksLoading(true)
    setTasksError('')
    api.listTasks()
      .then((response) => {
        if (active) setTasks(response.items)
      })
      .catch((caught) => {
        if (!active) return
        const message = caught instanceof ApiError
          ? `${caught.code}: ${caught.message}`
          : caught instanceof Error ? caught.message : '任务记录加载失败'
        setTasksError(message)
      })
      .finally(() => {
        if (active) setTasksLoading(false)
      })

    return () => { active = false }
  }, [view])

  const showTaskDetail = async (taskId: string) => {
    setDetailLoading(true)
    setTaskActionError('')
    setSelectedFile(null)
    try {
      const detail = await api.getTask(taskId)
      setSelectedTask(detail)
      if (detail.target.file_id) {
        setSelectedFile(await api.getFile(detail.target.file_id))
      }
    } catch (caught) {
      const message = caught instanceof ApiError
        ? `${caught.code}: ${caught.message}`
        : caught instanceof Error ? caught.message : '任务详情加载失败'
      setTaskActionError(message)
    } finally {
      setDetailLoading(false)
    }
  }

  const cancelTask = async (task: Pick<TaskDetail, 'task_id' | 'name'>) => {
    if (!window.confirm(`确定取消任务“${task.name}”吗？取消后不能恢复。`)) return
    setCancellingTaskId(task.task_id)
    setTaskActionError('')
    try {
      const updated = await api.updateTaskStatus(task.task_id, 'cancelled')
      setTasks((items) => items.map((item) => item.task_id === updated.task_id ? updated : item))
      setSelectedTask((current) => current?.task_id === updated.task_id ? updated : current)
      if (snapshot.task?.task_id === updated.task_id) {
        runToken.current += 1
        setSnapshot((current) => ({ ...current, task: { ...current.task!, status: 'cancelled' } }))
        setStage('cancelled')
      }
    } catch (caught) {
      const message = caught instanceof ApiError
        ? `${caught.code}: ${caught.message}`
        : caught instanceof Error ? caught.message : '取消任务失败'
      setTaskActionError(message)
    } finally {
      setCancellingTaskId(null)
    }
  }

  const updateForm = <K extends keyof TaskInput>(key: K, value: TaskInput[K]) =>
    setForm((current) => ({ ...current, [key]: value }))

  const updateContext = (key: keyof TaskInput['context'], value: string[] | number[] | string) =>
    setForm((current) => ({ ...current, context: { ...current.context, [key]: value } }))

  const runFlow = async (event?: FormEvent) => {
    event?.preventDefault()
    const token = ++runToken.current
    setError('')
    setSnapshot({ ...initialSnapshot, input: form })
    setView('workspace')

    try {
      let prepared = structuredClone(form)
      prepared.context = { ...prepared.context, ...parseContextLists(keywordText, yearText) }
      if (prepared.target.type !== 'hash') {
        if (!file) throw new Error('请选择一个经过授权的离线评测文件。')
        setStage('uploading')
        const uploaded = await api.uploadFile(file)
        prepared = { ...prepared, target: { ...prepared.target, content: null, file_id: uploaded.file_id } }
      }

      setStage('creating')
      const task = await api.createTask(prepared)
      if (token !== runToken.current) return
      setSnapshot((current) => ({ ...current, input: prepared, task }))

      setStage('analyzing')
      const prir = await api.analyze(task.task_id)
      if (token !== runToken.current) return
      setSnapshot((current) => ({ ...current, prir }))

      setStage('planning')
      const plan = await api.plan(task.task_id)
      if (token !== runToken.current) return
      setSnapshot((current) => ({ ...current, plan }))

      setStage('executing')
      const run = await api.execute(task.task_id)
      if (token !== runToken.current) return
      setSnapshot((current) => ({ ...current, run }))

      let current: RunStatus
      do {
        await new Promise((resolve) => window.setTimeout(resolve, 1000))
        current = await api.getStatus(run.run_id)
        if (token !== runToken.current) return
        setSnapshot((previous) => ({ ...previous, status: current }))
      } while (current.status === 'running')

      if (current.status === 'failed') throw new Error(current.message || '执行失败')
      const result = await api.getResult(run.run_id)
      if (token !== runToken.current) return
      setSnapshot((currentSnapshot) => ({ ...currentSnapshot, result }))
      setStage('completed')
    } catch (caught) {
      const message = caught instanceof ApiError
        ? `${caught.code}: ${caught.message}`
        : caught instanceof Error ? caught.message : '发生未知错误'
      setError(message)
      setStage('error')
    }
  }

  const resetFlow = () => {
    runToken.current += 1
    setStage('idle')
    setSnapshot(initialSnapshot)
    setError('')
    setView('new')
  }

  const nav = [
    { key: 'overview' as const, label: '总览', icon: 'grid' },
    { key: 'new' as const, label: '新建评测', icon: 'plus' },
    { key: 'workspace' as const, label: '执行工作台', icon: 'layers' },
    { key: 'tasks' as const, label: '任务记录', icon: 'list' },
  ]

  return (
    <div className="app-shell">
      <aside className={`sidebar ${mobileNav ? 'sidebar-open' : ''}`}>
        <div className="brand" onClick={() => setView('overview')}>
          <span className="brand-mark"><Icon name="shield" size={19} /></span>
          <span>SAGE<span>·Pass</span></span>
        </div>
        <button className="mobile-close" onClick={() => setMobileNav(false)} aria-label="关闭导航"><Icon name="close" /></button>

        <nav>
          <p className="nav-caption">控制中心</p>
          {nav.map((item) => (
            <button
              key={item.key}
              className={view === item.key ? 'nav-item active' : 'nav-item'}
              onClick={() => { setView(item.key); setMobileNav(false) }}
            >
              <Icon name={item.icon} size={17} />
              <span>{item.label}</span>
              {item.key === 'workspace' && stage === 'executing' && <i className="live-dot" />}
            </button>
          ))}
        </nav>

        <div className="sidebar-foot">
          <span className="eyebrow">SYSTEM</span>
          <strong>Mock orchestration</strong>
          <p>第一阶段 · 统一接口</p>
          <div className={`system-status ${health}`}><i /> {health === 'online' ? '后端服务正常' : health === 'offline' ? '后端服务离线' : '正在检查服务'}</div>
        </div>
      </aside>

      <main>
        <header className="topbar">
          <button className="mobile-menu" onClick={() => setMobileNav(true)} aria-label="打开导航"><Icon name="menu" /></button>
          <div className="breadcrumbs"><span>SAGE-Pass</span><b>/</b>{nav.find((item) => item.key === view)?.label}</div>
          <div className="top-actions">
            <span className="mode-pill"><i /> 真实接口模式</span>
            <button className="avatar" title="本地用户">SP</button>
          </div>
        </header>

        {view === 'overview' && (
          <section className="page overview-page">
            <div className="hero">
              <div className="hero-copy">
                <span className="section-kicker">INTELLIGENT SECURITY ASSESSMENT</span>
                <h1>让每一次评测，<br />都有清晰的决策路径。</h1>
                <p>SAGE-Pass 将异构目标统一为 PRIR，并在有限预算下完成策略生成、执行反馈与结果归因。</p>
                <div className="hero-actions">
                  <button className="button primary" onClick={() => setView('new')}>创建评测 <Icon name="arrow" size={16} /></button>
                  <button className="button text" onClick={() => setView('workspace')}>查看工作台</button>
                </div>
              </div>
              <div className="hero-visual" aria-label="SAGE-Pass 流程概览">
                <div className="visual-top"><span>Strategy Orchestration</span><span className="tiny-status"><i /> READY</span></div>
                <div className="signal-orbit"><span className="orbit-core">SAGE</span><i/><i/><i/></div>
                <div className="visual-metrics">
                  <div><small>任务画像</small><strong>PRIR</strong></div>
                  <div><small>策略空间</small><strong>S1—S5</strong></div>
                  <div><small>执行模式</small><strong>MOCK</strong></div>
                </div>
              </div>
            </div>

            <div className="section-heading">
              <div><span className="section-kicker">HOW IT WORKS</span><h2>一个可解释的评测闭环</h2></div>
              <p>每个阶段都保留结构化输出，便于联调、复盘与后续替换真实算法。</p>
            </div>
            <div className="workflow-grid">
              {workflow.map((item, index) => (
                <article key={item.key} className="workflow-card">
                  <span>{item.index}</span>
                  <div className="mini-icon"><Icon name={index === 0 ? 'target' : index === 3 ? 'pulse' : index === 4 ? 'check' : 'layers'} /></div>
                  <h3>{item.title}</h3>
                  <p>{item.caption}</p>
                </article>
              ))}
            </div>
          </section>
        )}

        {view === 'new' && (
          <section className="page form-page">
            <div className="page-title">
              <div><span className="section-kicker">NEW ASSESSMENT</span><h1>创建安全评测</h1></div>
              <p>填写目标、预算与可用上下文，系统将自动模拟完整策略链路。</p>
            </div>
            <form onSubmit={runFlow} className="assessment-form">
              <div className="form-section">
                <div className="form-section-title"><span>01</span><div><h2>任务与目标</h2><p>定义本次授权评测的基础信息</p></div></div>
                <div className="form-grid two">
                  <label><span>任务名称</span><input value={form.name} onChange={(e) => updateForm('name', e.target.value)} required /></label>
                  <label><span>目标类型</span><select value={form.target.type} onChange={(e) => updateForm('target', { ...form.target, type: e.target.value as TargetType, content: e.target.value === 'hash' ? form.target.content : null })}><option value="hash">Hash 文本</option><option value="zip">ZIP 压缩包</option><option value="pdf">PDF 文档</option><option value="office">Office 文档</option><option value="unknown">待识别</option></select></label>
                </div>
                {form.target.type === 'hash' ? (
                  <label className="wide"><span>Hash 内容</span><textarea rows={3} value={form.target.content ?? ''} onChange={(e) => updateForm('target', { ...form.target, content: e.target.value, file_id: null })} placeholder="粘贴经过授权的离线 Hash" required /></label>
                ) : (
                  <label className="upload-box">
                    <input type="file" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
                    <span className="upload-icon"><Icon name="upload" /></span>
                    <strong>{file?.name ?? '选择评测文件'}</strong>
                    <small>{file ? `${(file.size / 1024).toFixed(1)} KB` : 'ZIP、PDF 或 Office 文件，仅传递 file_id'}</small>
                  </label>
                )}
                <label className="wide"><span>已知算法（可选）</span><input value={form.known_algorithm ?? ''} onChange={(e) => updateForm('known_algorithm', e.target.value || null)} placeholder="例如 bcrypt、SHA-256；留空则自动识别" /></label>
              </div>

              <div className="form-section">
                <div className="form-section-title"><span>02</span><div><h2>预算约束</h2><p>设定时间与候选数量上限</p></div></div>
                <div className="form-grid two">
                  <label><span>时间预算 <em>秒</em></span><input type="number" min="1" value={form.time_budget} onChange={(e) => updateForm('time_budget', Number(e.target.value))} required /></label>
                  <label><span>候选预算 <em>个</em></span><input type="number" min="1" value={form.candidate_budget} onChange={(e) => updateForm('candidate_budget', Number(e.target.value))} required /></label>
                </div>
              </div>

              <div className="form-section">
                <div className="form-section-title"><span>03</span><div><h2>上下文信息</h2><p>为 Context 策略提供可解释线索</p></div></div>
                <div className="form-grid two">
                  <label><span>关键词 <em>用逗号分隔</em></span><input value={keywordText} onChange={(e) => setKeywordText(e.target.value)} /></label>
                  <label><span>相关年份 <em>用逗号分隔</em></span><input value={yearText} onChange={(e) => setYearText(e.target.value)} /></label>
                  <label><span>地区</span><input value={form.context.region} onChange={(e) => updateContext('region', e.target.value)} /></label>
                  <label><span>组织</span><input value={form.context.organization} onChange={(e) => updateContext('organization', e.target.value)} /></label>
                </div>
                <label className="wide"><span>补充说明</span><textarea rows={3} value={form.context.description} onChange={(e) => updateContext('description', e.target.value)} /></label>
              </div>

              <div className="form-footer">
                <p><Icon name="shield" size={16} /> 请确认目标属于已获授权的离线安全评测范围。</p>
                <button className="button primary large" type="submit">启动全过程模拟 <Icon name="play" size={15} /></button>
              </div>
            </form>
          </section>
        )}

        {view === 'workspace' && (
          <section className="page workspace-page">
            <div className="page-title workspace-title">
              <div><span className="section-kicker">COMMAND CENTER</span><h1>执行工作台</h1></div>
              <div className="workspace-actions">
                {snapshot.task && !['completed', 'cancelled', 'error'].includes(stage) && (
                  <button
                    className="button cancel-button"
                    onClick={() => void cancelTask({ task_id: snapshot.task!.task_id, name: snapshot.input.name })}
                    disabled={cancellingTaskId === snapshot.task.task_id}
                  >
                    {cancellingTaskId === snapshot.task.task_id ? '取消中…' : '取消任务'}
                  </button>
                )}
                <div className={`stage-badge ${stage}`}><i /> {stageLabels[stage]}</div>
              </div>
            </div>
            {taskActionError && <div className="error-banner"><strong>取消失败</strong><span>{taskActionError}</span><button onClick={() => setTaskActionError('')}>关闭</button></div>}

            {stage === 'idle' ? (
              <div className="empty-state">
                <span><Icon name="layers" size={30} /></span>
                <h2>尚未创建评测任务</h2>
                <p>从一份示例配置开始，几秒内查看完整的智能编排过程。</p>
                <button className="button primary" onClick={() => setView('new')}>配置新任务 <Icon name="arrow" size={16} /></button>
              </div>
            ) : (
              <>
                <div className="process-rail">
                  {workflow.map((item, index) => {
                    const itemOrder = stageOrder.indexOf(item.key)
                    const active = stage === item.key || (stage === 'uploading' && index === 0)
                    const done = completedStep > itemOrder || stage === 'completed'
                    return <div key={item.key} className={`rail-item ${active ? 'active' : ''} ${done ? 'done' : ''}`}><span>{done ? <Icon name="check" size={14} /> : item.index}</span><div><strong>{item.title}</strong><small>{item.caption}</small></div></div>
                  })}
                </div>

                {error && <div className="error-banner"><strong>流程中断</strong><span>{error}</span><button onClick={resetFlow}>返回修改</button></div>}

                <div className="metrics-grid">
                  <article><div><span>执行进度</span><Icon name="pulse" /></div><strong>{Math.round(progress * 100)}<small>%</small></strong><div className="metric-line"><i style={{ width: `${progress * 100}%` }} /></div></article>
                  <article><div><span>已测试候选</span><Icon name="target" /></div><strong>{formatNumber(snapshot.status?.tested ?? snapshot.result?.total_tested)}</strong><small>预算 {formatNumber(snapshot.input.candidate_budget)}</small></article>
                  <article><div><span>恢复数量</span><Icon name="check" /></div><strong>{snapshot.status?.recovered ?? snapshot.result?.total_recovered ?? 0}</strong><small>Mock 评测结果</small></article>
                  <article><div><span>执行耗时</span><Icon name="clock" /></div><strong>{snapshot.status?.elapsed_time ?? snapshot.result?.total_time ?? 0}<small>s</small></strong><small>预算 {snapshot.input.time_budget}s</small></article>
                </div>

                <div className="workspace-grid">
                  <article className="panel plan-panel">
                    <div className="panel-head"><div><span className="section-kicker">STRATEGY PLAN</span><h2>策略编排</h2></div><span className="panel-tag">{snapshot.plan?.planner_type ?? '等待中'}</span></div>
                    <div className="strategy-list">
                      {snapshot.plan?.strategies.map((strategy) => {
                        const current = snapshot.status?.current_strategy === strategy.strategy_id
                        const result = snapshot.result?.strategy_results.find((item) => item.strategy_id === strategy.strategy_id)
                        return <div className={`strategy-row ${current ? 'current' : ''}`} key={strategy.strategy_id}><span className="strategy-id">{strategy.strategy_id}</span><div className="strategy-main"><div><strong>{strategy.strategy_name}</strong>{current && <em>执行中</em>}</div><p>{strategy.reason}</p><div className="budget-bar"><i style={{ width: `${Math.min(100, strategy.time_budget / snapshot.plan!.total_time_budget * 100)}%` }} /></div></div><div className="strategy-stat"><strong>{result?.recovered ?? '—'}</strong><small>恢复</small></div><div className="strategy-stat"><strong>{strategy.time_budget}s</strong><small>预算</small></div></div>
                      }) ?? <div className="panel-placeholder"><span className="loader" />正在等待策略计划</div>}
                    </div>
                  </article>

                  <article className="panel prir-panel">
                    <div className="panel-head"><div><span className="section-kicker">TASK PROFILE</span><h2>PRIR 画像</h2></div><span className="confidence">{snapshot.prir ? `${Math.round(snapshot.prir.confidence * 100)}% 置信度` : '分析中'}</span></div>
                    {snapshot.prir ? <dl className="profile-list"><div><dt>目标类型</dt><dd>{snapshot.prir.target_type}</dd></div><div><dt>识别算法</dt><dd>{snapshot.prir.algorithm}</dd></div><div><dt>验证成本</dt><dd><span className={`cost ${snapshot.prir.verification_cost}`}>{snapshot.prir.verification_cost}</span></dd></div><div><dt>候选空间</dt><dd>{formatNumber(snapshot.prir.candidate_space)}</dd></div><div><dt>上下文</dt><dd>{snapshot.prir.context_available ? '可用' : '不可用'}</dd></div><div><dt>任务编号</dt><dd>{snapshot.prir.task_id}</dd></div></dl> : <div className="panel-placeholder"><span className="loader" />正在建立统一任务画像</div>}
                  </article>
                </div>

                {snapshot.result && <article className="result-strip"><div><span className="result-check"><Icon name="check" /></span><div><span className="section-kicker">ASSESSMENT COMPLETE</span><h2>模拟评测链路已完整跑通</h2><p>共测试 {formatNumber(snapshot.result.total_tested)} 个候选，恢复 {snapshot.result.total_recovered} 项。各策略统计见上方列表。</p></div></div><button className="button secondary" onClick={resetFlow}>新建评测 <Icon name="arrow" size={15} /></button></article>}
              </>
            )}
          </section>
        )}

        {view === 'tasks' && (
          <section className="page tasks-page">
            <div className="page-title"><div><span className="section-kicker">TASK ARCHIVE</span><h1>任务记录</h1></div><button className="button primary" onClick={() => setView('new')}>新建任务 <Icon name="plus" size={15} /></button></div>
            {taskActionError && <div className="error-banner"><strong>操作失败</strong><span>{taskActionError}</span><button onClick={() => setTaskActionError('')}>关闭</button></div>}
            <div className="table-shell">
              <div className="table-head"><span>任务</span><span>目标</span><span>预算</span><span>状态</span><span>操作</span></div>
              {tasksLoading && <div className="table-empty">正在从后端加载任务记录…</div>}
              {!tasksLoading && tasksError && <div className="table-empty">加载失败：{tasksError}</div>}
              {!tasksLoading && !tasksError && tasks.map((task) => {
                const canCancel = !['completed', 'failed', 'cancelled'].includes(task.status)
                return <div className="table-row" key={task.task_id}><div><strong>{task.name}</strong><small>{task.task_id}</small></div><span>{task.target.type.toUpperCase()} · {task.known_algorithm ?? '自动识别'}</span><span>{task.time_budget}s / {formatNumber(task.candidate_budget)}</span><span><i className={`status-dot ${task.status}`} />{taskStatusLabels[task.status]}</span><div className="task-actions"><button onClick={() => void showTaskDetail(task.task_id)} disabled={detailLoading}>详情</button>{canCancel && <button className="danger" onClick={() => void cancelTask(task)} disabled={cancellingTaskId === task.task_id}>{cancellingTaskId === task.task_id ? '取消中…' : '取消任务'}</button>}</div></div>
              })}
              {!tasksLoading && !tasksError && tasks.length === 0 && <div className="table-empty">后端尚无任务记录</div>}
            </div>
            {detailLoading && <div className="task-detail-card"><div className="panel-placeholder"><span className="loader" />正在读取任务详情</div></div>}
            {!detailLoading && selectedTask && <article className="task-detail-card">
              <div className="panel-head"><div><span className="section-kicker">TASK DETAIL</span><h2>{selectedTask.name}</h2></div><button className="detail-close" onClick={() => { setSelectedTask(null); setSelectedFile(null) }}>关闭</button></div>
              <dl className="task-detail-grid">
                <div><dt>任务编号</dt><dd>{selectedTask.task_id}</dd></div>
                <div><dt>当前状态</dt><dd>{taskStatusLabels[selectedTask.status]}</dd></div>
                <div><dt>目标类型</dt><dd>{selectedTask.target.type.toUpperCase()}</dd></div>
                <div><dt>算法</dt><dd>{selectedTask.known_algorithm ?? '自动识别'}</dd></div>
                <div><dt>时间预算</dt><dd>{selectedTask.time_budget} 秒</dd></div>
                <div><dt>候选预算</dt><dd>{formatNumber(selectedTask.candidate_budget)}</dd></div>
                <div><dt>创建时间</dt><dd>{formatDate(selectedTask.created_at)}</dd></div>
                <div><dt>更新时间</dt><dd>{formatDate(selectedTask.updated_at)}</dd></div>
              </dl>
              <div className="task-context"><strong>上下文信息</strong><p>{[...selectedTask.context.keywords, ...selectedTask.context.years.map(String), selectedTask.context.region, selectedTask.context.organization, selectedTask.context.description].filter(Boolean).join(' · ') || '未提供'}</p></div>
              {selectedFile && <div className="file-detail"><div><span className="section-kicker">FILE DETAIL</span><strong>{selectedFile.filename}</strong></div><dl><div><dt>大小</dt><dd>{formatBytes(selectedFile.size)}</dd></div><div><dt>类型</dt><dd>{selectedFile.content_type ?? '未知'}</dd></div><div><dt>文件编号</dt><dd>{selectedFile.file_id}</dd></div><div><dt>SHA-256</dt><dd>{selectedFile.sha256}</dd></div></dl></div>}
            </article>}
          </section>
        )}
      </main>
    </div>
  )
}

export default App
