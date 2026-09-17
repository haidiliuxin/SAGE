import assert from 'node:assert/strict'
import test from 'node:test'
import { createRequire } from 'node:module'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { build } from 'esbuild'
import React from 'react'
import { create, act } from 'react-test-renderer'

const require = createRequire(import.meta.url)
globalThis.IS_REACT_ACT_ENVIRONMENT = true

async function loadSource(relativePath) {
  const compiled = await build({
    entryPoints: [fileURLToPath(new URL(relativePath, import.meta.url))],
    bundle: true, write: false, platform: 'node', format: 'esm', jsx: 'automatic',
    define: { 'import.meta.env.VITE_API_BASE_URL': '""' },
    plugins: [{ name: 'shared-react', setup(builder) {
      builder.onResolve({ filter: /^react(?:\/.*)?$/ }, (args) => ({
        path: pathToFileURL(require.resolve(args.path)).href, external: true,
      }))
    } }],
  })
  return import('data:text/javascript;base64,' + Buffer.from(compiled.outputFiles[0].text).toString('base64'))
}

const { default: App, parseCandidateList } = await loadSource('../src/App.tsx')
const { parseContextLists, parseHistoricalPasswords } = await loadSource('../src/context-input.ts')
const { ResearchPanel, TaskRuns } = await loadSource('../src/ResearchPanel.tsx')

function text(node) {
  if (typeof node === 'string') return node
  return (node.children ?? []).map(text).join('')
}

async function openForm(t, fetchHandler) {
  const savedWindow = globalThis.window
  const savedFetch = globalThis.fetch
  globalThis.window = {
    location: { hash: '', pathname: '/', search: '' },
    history: { pushState() {}, replaceState() {} },
    addEventListener() {}, removeEventListener() {}, clearTimeout() {},
    setInterval: () => 0, clearInterval: () => {},
    setTimeout: (callback) => { callback(); return 0 },
  }
  globalThis.fetch = fetchHandler
  let renderer
  t.after(async () => {
    if (renderer) await act(async () => renderer.unmount())
    globalThis.window = savedWindow
    globalThis.fetch = savedFetch
  })
  await act(async () => { renderer = create(React.createElement(App)) })
  const start = renderer.root.findAllByType('button').find((button) => text(button).trim() === '创建评测')
  await act(async () => start.props.onClick())
  return {
    renderer,
    input(label) {
      return renderer.root.findAllByType('label').find((node) => text(node).startsWith(label)).findByType('input')
    },
    select(label) {
      return renderer.root.findAllByType('label').find((node) => text(node).startsWith(label)).findByType('select')
    },
    async submit() {
      await act(async () => renderer.root.findByType('form').props.onSubmit({ preventDefault() {} }))
    },
  }
}

function remoteFlow(strategyIds = ['S1'], overrides = {}) {
  let submitted
  let execution
  const taskId = 'T-TEST'
  const runId = 'R-TEST'
  const strategyNames = { S1: 'Baseline', S2: 'Rule', S3: 'PCFG-lite', S4: 'Context' }
  const strategies = strategyIds.map((strategy_id, index) => ({
    strategy_id, strategy_name: strategyNames[strategy_id] ?? strategy_id,
    priority: index + 1, time_budget: 10, candidate_budget: 100,
    reason: 'test fixture', parameters: {},
  }))
  const handler = async (url, init) => {
    let data
    if (url === '/health') data = { status: 'ok' }
    else if (url === '/api/system/config') data = {
      planner_type: 'rule',
      scheduler_type: 'heuristic_bandit',
      real_execution_configured: true,
      feedback_mock_enabled: false,
    }
    else if (url === '/api/tasks') {
      submitted = JSON.parse(init.body)
      data = { task_id: taskId, status: 'created', created_at: new Date().toISOString() }
    } else if (url.endsWith('/analyze')) data = {
      task_id: taskId, target_type: 'hash', algorithm: 'unknown', salt: null,
      verification_cost: 'unknown', context_available: false, candidate_space: null,
      time_budget: 300, candidate_budget: 100000, status: 'analyzed', confidence: 0,
      warnings: [], ...overrides.prir,
    }
    else if (url.endsWith('/plan')) data = { task_id: taskId, planner_type: 'mock', total_time_budget: 300, strategies, status: 'planned', warnings: [], ...overrides.plan }
    else if (url.endsWith('/execute')) {
      execution = JSON.parse(init.body)
      data = { task_id: taskId, run_id: runId, status: 'running', started_at: new Date().toISOString() }
    }
    else if (url === `/api/runs/${runId}/status`) data = overrides.status
      ? await overrides.status()
      : { task_id: taskId, run_id: runId, status: 'completed', progress: 1, current_strategy: null, elapsed_time: 2, tested: 123, recovered: 2, message: 'done' }
    else if (url === `/api/tasks/${taskId}/status`) data = overrides.taskStatus
      ? await overrides.taskStatus(JSON.parse(init.body).status)
      : { task_id: taskId, status: JSON.parse(init.body).status, created_at: new Date().toISOString() }
    else if (url.endsWith('/result')) data = {
      task_id: taskId, run_id: runId, status: 'completed', total_time: 2,
      total_tested: 123, total_recovered: 2, finished_at: new Date().toISOString(),
      strategy_results: strategies.map(({ strategy_id }) => ({ strategy_id, time: 1, tested: 100, recovered: 1, success_rate: 0.01 })),
      recovered_items: [],
      ...overrides.result,
    }
    else if (url.endsWith('/research')) data = { run_id: runId, task_id: taskId, status: 'completed', available: false }
    else if (url.includes('/research/events?')) data = { items: [], next_after_sequence: 0, next_before_sequence: null, has_more: false }
    else throw new Error(`Unexpected URL: ${url}`)
    return Response.json(data)
  }
  return { handler, submitted: () => submitted, execution: () => execution }
}

test('parse comma-separated context only at submission, accepting empty fields', () => {
  assert.deepEqual(parseContextLists(' alpha, beta，学校, ', '2024, 2025，'), {
    keywords: ['alpha', 'beta', '学校'], years: [2024, 2025],
  })
  assert.deepEqual(parseContextLists(' ,， ', ''), { keywords: [], years: [] })
  for (const invalid of ['202x', '2024.5', 'Infinity', '-1', '0', '9007199254740993']) {
    assert.throws(() => parseContextLists('', invalid), /相关年份/)
  }
})

test('historical passwords use newline boundaries and preserve password text', () => {
  assert.deepEqual(parseHistoricalPasswords('old,with,commas\n second value \nold,with,commas'), [
    'old,with,commas', ' second value ',
  ])
})

test('component retains typed separators and submits separate keywords and years', async (t) => {
  const remote = remoteFlow()
  const form = await openForm(t, remote.handler)
  for (const [label, first, separator, second] of [
    ['关键词', 'alpha', ',', 'beta'], ['相关年份', '2024', '，', '2025'],
  ]) {
    const input = form.input(label)
    await act(async () => input.props.onChange({ target: { value: first + separator } }))
    assert.equal(input.props.value, first + separator)
    await act(async () => input.props.onChange({ target: { value: input.props.value + second } }))
  }
  await form.submit()
  assert.deepEqual(remote.submitted().context.keywords, ['alpha', 'beta'])
  assert.deepEqual(remote.submitted().context.years, [2024, 2025])
})

test('execution mode defaults to mock and can be switched to real', async (t) => {
  const remote = remoteFlow()
  const form = await openForm(t, remote.handler)
  assert.equal(form.select('执行模式').props.value, 'mock')
  await act(async () => form.select('执行模式').props.onChange({ target: { value: 'real' } }))
  await form.submit()
  assert.deepEqual(remote.execution(), { mode: 'real', candidates: [], stop_on_hit: false })
})

test('candidate wordlist is parsed, deduplicated and submitted with the run', () => {
  assert.deepEqual(parseCandidateList('alpha\n\n beta \nalpha\n'), ['alpha', 'beta'])
  assert.deepEqual(parseCandidateList('x'.repeat(1025) + '\nok'), ['ok'])
  assert.deepEqual(parseCandidateList(''), [])
})

test('pasted wordlist and stop-on-hit reach the execute request', async (t) => {
  const remote = remoteFlow()
  const form = await openForm(t, remote.handler)
  const wordlist = form.renderer.root.findAllByType('label').find((node) => text(node).startsWith('补充候选词表'))
  await act(async () => wordlist.findByType('textarea').props.onChange({ target: { value: 'hunter2\nletmein\nhunter2' } }))
  const stopOnHit = form.renderer.root.findAllByType('input').find((node) => node.props.type === 'checkbox')
  await act(async () => stopOnHit.props.onChange({ target: { checked: true } }))
  await form.submit()

  assert.deepEqual(remote.execution(), {
    mode: 'mock',
    candidates: ['hunter2', 'letmein'],
    stop_on_hit: true,
  })
})

test('sidebar shows planner and scheduler modes separately', async (t) => {
  const remote = remoteFlow()
  const form = await openForm(t, remote.handler)
  await act(async () => { await Promise.resolve() })
  const footer = form.renderer.root.findAllByType('p').map((node) => text(node)).join(' ')
  assert.match(footer, /规划 规则规划/)
  assert.match(footer, /调度 启发式 Bandit/)
})

for (const ids of [['S1'], ['S1', 'S4']]) {
  test(`summary uses backend totals without inventing a winner (${ids.join(',')})`, async (t) => {
    const form = await openForm(t, remoteFlow(ids).handler)
    await form.submit()
    const summary = form.renderer.root.findAllByType('article').find((node) => node.props.className === 'result-strip')
    const summaryText = text(summary)
    assert.match(summaryText, /共测试 123 个候选，恢复 2 项/)
    assert.doesNotMatch(summaryText, /Context|最高|收益/)
  })
}

test('result view shows recovered plaintext instead of only a count', async (t) => {
  const remote = remoteFlow(['S1'], {
    result: {
      total_recovered: 1,
      recovered_items: [{ target: '$zip2$*0*3*0*76e99cf0732df1cf*508b*4a*d1aaf2$', plaintext: '网络安全2024' }],
    },
  })
  const form = await openForm(t, remote.handler)
  await form.submit()

  const pageText = text(form.renderer.root)
  assert.match(pageText, /恢复结果/)
  assert.match(pageText, /网络安全2024/)
  assert.match(pageText, /来源目标/)
  const copy = form.renderer.root.findAllByType('button').find((node) => text(node).trim() === '复制')
  assert.ok(copy, '恢复结果应提供复制按钮')
})

test('mock runs explain that simulated statistics contain no plaintext', async (t) => {
  const remote = remoteFlow(['S1'], { result: { total_recovered: 6, recovered_items: [] } })
  const form = await openForm(t, remote.handler)
  await form.submit()

  const pageText = text(form.renderer.root)
  assert.match(pageText, /恢复结果/)
  assert.match(pageText, /模拟执行（Mock）：只产生模拟统计，没有真实明文/)
})

test('real runs without a hit say so instead of staying silent', async (t) => {
  const remote = remoteFlow(['S1'], { result: { total_recovered: 0, recovered_items: [] } })
  const form = await openForm(t, remote.handler)
  await act(async () => form.select('执行模式').props.onChange({ target: { value: 'real' } }))
  await form.submit()

  const pageText = text(form.renderer.root)
  assert.match(pageText, /本次真实运行未恢复出明文/)
  assert.match(pageText, /候选空间或预算已耗尽/)
})

test('plan panel explains when S4 and S5 are absent from the plan', async (t) => {
  const form = await openForm(t, remoteFlow(['S1', 'S2', 'S3']).handler)
  await form.submit()

  const pageText = text(form.renderer.root)
  assert.match(pageText, /S4 个性化策略未纳入计划/)
  assert.match(pageText, /S5 迁移策略未纳入计划/)
})

test('plan panel stays silent about S4 when the plan includes it', async (t) => {
  const form = await openForm(t, remoteFlow(['S1', 'S2', 'S3', 'S4']).handler)
  await form.submit()

  const pageText = text(form.renderer.root)
  assert.doesNotMatch(pageText, /S4 个性化策略未纳入计划/)
})

test('invalid year is reported without sending a task to the backend', async (t) => {
  const remote = remoteFlow()
  const form = await openForm(t, remote.handler)
  await act(async () => form.input('相关年份').props.onChange({ target: { value: '202x' } }))
  await form.submit()
  assert.equal(remote.submitted(), undefined)
  assert.match(text(form.renderer.root), /相关年份请输入正整数/)
})

test('backend connection failure shows an error, never a local success result', async (t) => {
  const form = await openForm(t, async () => { throw new TypeError('Network unavailable') })
  await form.submit()
  assert.match(text(form.renderer.root), /Network unavailable/)
  assert.equal(form.renderer.root.findAllByType('article').filter((node) => node.props.className === 'result-strip').length, 0)
})

test('switching from hash to a file target clears stale algorithm and constrains file type', async (t) => {
  const form = await openForm(t, remoteFlow().handler)
  const targetLabel = form.renderer.root.findAllByType('label').find((node) => text(node).startsWith('目标类型'))
  await act(async () => targetLabel.findByType('select').props.onChange({ target: { value: 'zip' } }))

  assert.equal(form.input('已知算法').props.value, '')
  assert.equal(form.renderer.root.findByProps({ className: 'upload-box' }).findByType('input').props.accept, '.zip,application/zip')
})

test('workspace renders backend PRIR, rule plan budgets, parameters and warnings', async (t) => {
  const remote = remoteFlow(['S1', 'S2', 'S3', 'S4'], {
    prir: {
      algorithm: 'bcrypt', salt: true, verification_cost: 'high',
      context_available: true, confidence: 0.91,
      warnings: ['分析器降级提示'],
    },
    plan: {
      planner_type: 'rule',
      warnings: ['慢 Hash 采用高概率小候选集'],
    },
  })
  const form = await openForm(t, remote.handler)
  await form.submit()
  const pageText = text(form.renderer.root)

  assert.match(pageText, /规则规划/)
  assert.match(pageText, /bcrypt/)
  assert.match(pageText, /包含盐值/)
  assert.match(pageText, /高（慢速验证）/)
  assert.match(pageText, /可用于 S4/)
  assert.match(pageText, /已分配候选/)
  assert.match(pageText, /默认参数/)
  assert.match(pageText, /分析器降级提示/)
  assert.match(pageText, /慢 Hash 采用高概率小候选集/)
})

test('workspace distinguishes a pause request from a completed batch-boundary pause', async (t) => {
  let resolveFirstStatus
  let resolveSecondStatus
  const firstStatus = new Promise((resolve) => { resolveFirstStatus = resolve })
  const secondStatus = new Promise((resolve) => { resolveSecondStatus = resolve })
  let statusCall = 0
  const remote = remoteFlow(['S1'], {
    status: () => (++statusCall === 1 ? firstStatus : secondStatus),
    taskStatus: (status) => ({ task_id: 'T-TEST', status, created_at: new Date().toISOString() }),
  })
  const form = await openForm(t, remote.handler)
  let flow
  await act(async () => {
    flow = form.renderer.root.findByType('form').props.onSubmit({ preventDefault() {} })
    await new Promise(setImmediate)
  })

  const button = (label) => form.renderer.root.findAllByType('button').find((node) => text(node).trim() === label)
  assert.ok(button('批次后暂停'))
  await act(async () => button('批次后暂停').props.onClick())
  assert.match(text(form.renderer.root), /等待当前批次结束/)
  assert.match(text(form.renderer.root), /当前批次会继续执行，之后停止提交新批次/)

  await act(async () => {
    resolveFirstStatus({ task_id: 'T-TEST', run_id: 'R-TEST', status: 'paused', progress: 0.5, current_strategy: null, elapsed_time: 1, tested: 50, recovered: 0, message: 'paused' })
    await new Promise(setImmediate)
  })
  assert.match(text(form.renderer.root), /已暂停/)
  assert.ok(button('继续'))

  await act(async () => {
    resolveSecondStatus({ task_id: 'T-TEST', run_id: 'R-TEST', status: 'completed', progress: 1, current_strategy: null, elapsed_time: 2, tested: 123, recovered: 2, message: 'done' })
    await flow
  })
})

test('task list keeps execution controls out of the archive view', async (t) => {
  const now = new Date().toISOString()
  const base = (task_id, name, status) => ({
    task_id, name, status, created_at: now, updated_at: now,
    target: { type: 'hash', content: '$2b$10$fixture.for.authorized.testing', file_id: null },
    known_algorithm: null, time_budget: 300, candidate_budget: 100000,
    context: { keywords: [], years: [], region: null, organization: null, description: null },
  })
  const handler = async (url, init) => {
    if (url === '/health') return Response.json({ status: 'ok' })
    if (url.startsWith('/api/tasks?')) {
      return Response.json({
        items: [
          base('T-RUN', '正在运行的任务', 'running'),
          base('T-PAUSE', '已暂停的任务', 'paused'),
          base('T-DONE', '已完成的任务', 'completed'),
        ],
        total: 3, limit: 100, offset: 0,
      })
    }
    throw new Error(`Unexpected URL: ${url}`)
  }

  const form = await openForm(t, handler)
  const nav = form.renderer.root.findAllByType('button').find((b) => text(b).trim() === '任务记录')
  await act(async () => nav.props.onClick())

  const labels = () => form.renderer.root.findAllByType('button').map((b) => text(b).trim())
  assert.equal(labels().filter((label) => label === '详情').length, 3)
  assert.ok(!labels().includes('暂停'))
  assert.ok(!labels().includes('继续'))
  assert.ok(!labels().includes('取消任务'))
})

function researchFixture(runId = 'R-HISTORY') {
  const state = { remaining_candidates: 90, remaining_time: 8, arms: { S1: {
    cost_samples: 1, censored_samples: 1, estimated_startup: 0.2,
    estimated_seconds_per_candidate: 0.1, cost_confidence: 0.05,
    recent_throughput: null, last_batch_throughput: null,
  } } }
  const event = { sequence: 4, schema_version: 1, run_id: runId, attempt_id: 'attempt1', round_index: 1,
    event_type: 'decision_completed', payload: {
      decision: { arm_id: 'S1', strategy_id: 'S1', candidate_limit: 10, time_limit: 2, exploration: true },
      scores: { S1: { score: 0.4, mean_reward: 0.1, exploration_bonus: 0.3, predicted_seconds: 1.2 } },
      available_arms: ['S1'], available_batches: { S1: 10 }, updated_state: state,
      reward: -0.2, learning_reward: 0.1,
      reward_breakdown: { recovery_gain: 0.1, time_penalty: 0.2, candidate_penalty: 0.1, duplicate_penalty: null, total: -0.2 },
      feedback: { candidate_count: 10, tested: 8, recovered: 1, duration: 2, duplicate_count: null },
    } }
  return { schema_version: 1, run_id: runId, task_id: 'T-HISTORY', available: true, status: 'completed',
    configuration: { mode: 'real', policy_type: 'cost_aware_ucb', reward_context: { initial_targets: 10 } },
    latest_decision: event, latest_completed: event, latest_state: state, stop_reason: 'time_budget',
    totals: { completed_rounds: 1, submitted_candidates: 10, tested_candidates: 8, recovered_targets: 1, duration: 2, evaluation_reward: -0.2 }, through_sequence: 4,
  }
}

async function mountResearchView(t, Component, props, handler, hash = '') {
  const oldWindow = globalThis.window
  const oldFetch = globalThis.fetch
  globalThis.window = {
    location: { hash, pathname: '/', search: '' }, history: { replaceState() {}, pushState() {} },
    addEventListener() {}, removeEventListener() {},
    setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, clearInterval() {},
  }
  globalThis.fetch = handler
  let renderer
  t.after(async () => {
    if (renderer) await act(async () => renderer.unmount())
    globalThis.window = oldWindow; globalThis.fetch = oldFetch
  })
  await act(async () => { renderer = create(React.createElement(Component, props)) })
  return renderer
}

test('research shows saved policy, distinct rewards, unknown throughput and full download', async (t) => {
  const fixture = researchFixture()
  const renderer = await mountResearchView(t, ResearchPanel, { runId: fixture.run_id }, async url => {
    if (url.endsWith('/research')) return Response.json(fixture)
    return Response.json({ items: [fixture.latest_completed], has_more: false, next_before_sequence: 4 })
  })
  const content = text(renderer.root)
  assert.match(content, /本次调度：成本感知 UCB/)
  assert.match(content, /累计评价奖励 -0.2/)
  assert.match(content, /UCB 学习收益0.1/)
  assert.match(content, /近期吞吐（候选\/秒）—/)
  assert.match(content, /重复惩罚—/)
  assert.equal(renderer.root.findByType('a').props.href, '/api/runs/R-HISTORY/research/download')
})

test('research pages backward without duplicating recent events', async (t) => {
  const fixture = researchFixture()
  const queries = []
  const renderer = await mountResearchView(t, ResearchPanel, { runId: fixture.run_id }, async url => {
    queries.push(url)
    if (url.endsWith('/research')) return Response.json(fixture)
    const old = url.includes('before_sequence=4')
    return Response.json({ items: [{ ...fixture.latest_completed, sequence: old ? 1 : 4 }],
      has_more: !old, next_before_sequence: old ? 1 : 4 })
  })
  const button = caption => renderer.root.findAllByType('button').find(b => text(b) === caption)
  await act(async () => button('更早的事件').props.onClick())
  assert.ok(queries.some(url => url.includes('before_sequence=4')))
  assert.match(text(renderer.root), /事件 #1/)
  assert.doesNotMatch(text(renderer.root), /事件 #4/)
  await act(async () => button('回到最新').props.onClick())
  assert.match(text(renderer.root), /事件 #4/)
})

test('switching runs ignores a late response from the previous run', async (t) => {
  let resolveOld
  const old = new Promise(resolve => { resolveOld = resolve })
  const fixture = researchFixture('R-NEW')
  const renderer = await mountResearchView(t, ResearchPanel, { runId: 'R-OLD' }, async url => {
    if (url.includes('R-OLD')) return old
    if (url.endsWith('/research')) return Response.json(fixture)
    return Response.json({ items: [], has_more: false, next_before_sequence: null })
  })
  await act(async () => renderer.update(React.createElement(ResearchPanel, { runId: 'R-NEW' })))
  await act(async () => { resolveOld(Response.json(researchFixture('R-OLD'))); await new Promise(setImmediate) })
  assert.match(text(renderer.root), /运行 R-NEW/)
  assert.doesNotMatch(text(renderer.root), /R-OLD/)
})

test('a fresh page opens a persisted run from its URL without starting execution', async (t) => {
  const calls = []
  const fixture = researchFixture()
  const renderer = await mountResearchView(t, App, {}, async (url, init) => {
    calls.push([url, init?.method ?? 'GET'])
    if (url === '/health') return Response.json({ status: 'ok' })
    if (url.endsWith('/config')) return Response.json({ planner_type: 'rule', scheduler_type: 'fixed' })
    if (url.endsWith('/research')) return Response.json(fixture)
    if (url.includes('/research/events?')) return Response.json({ items: [], has_more: false, next_before_sequence: null })
    throw new Error(`Unexpected URL: ${url}`)
  }, '#run=R-HISTORY')
  assert.match(text(renderer.root), /本次调度：成本感知 UCB/)
  assert.doesNotMatch(text(renderer.root), /尚未创建评测任务/)
  assert.ok(calls.every(([, method]) => method === 'GET'))
})

test('archive run selection opens the selected durable run', async (t) => {
  const opened = []
  const renderer = await mountResearchView(t, TaskRuns, { taskId: 'T-HISTORY', onOpen: id => opened.push(id) }, async () => Response.json({
    items: [{ run_id: 'R-HISTORY', mode: 'real', status: 'completed', started_at: null }], total: 1, offset: 0, limit: 20,
  }))
  const open = renderer.root.findAllByType('button').find(b => text(b) === '查看研究详情')
  await act(async () => open.props.onClick())
  assert.deepEqual(opened, ['R-HISTORY'])
})

test('archive can reveal the persisted plaintext of a past run', async (t) => {
  const renderer = await mountResearchView(t, TaskRuns, { taskId: 'T-HISTORY', onOpen: () => {} }, async url => {
    if (url.endsWith('/result')) return Response.json({
      task_id: 'T-HISTORY', run_id: 'R-HISTORY', status: 'completed', total_time: 22,
      total_tested: 3267, total_recovered: 1, finished_at: new Date().toISOString(),
      strategy_results: [{ strategy_id: 'S4', time: 7.6, tested: 1265, recovered: 1, success_rate: 0.001 }],
      recovered_items: [{ target: '$zip2$*0*3*0*76e99cf0*508b*4a*d1aaf2$', plaintext: '网络安全2024' }],
      message: null,
    })
    return Response.json({
      items: [{ run_id: 'R-HISTORY', mode: 'real', status: 'completed', started_at: null }], total: 1, offset: 0, limit: 20,
    })
  })

  const button = caption => renderer.root.findAllByType('button').find(b => text(b) === caption)
  assert.doesNotMatch(text(renderer.root), /网络安全2024/)
  await act(async () => button('查看结果').props.onClick())
  const content = text(renderer.root)
  assert.match(content, /网络安全2024/)
  assert.match(content, /恢复 1 项/)
  assert.ok(button('收起结果'))

  await act(async () => button('收起结果').props.onClick())
  assert.doesNotMatch(text(renderer.root), /网络安全2024/)
})

test('archive reports a missing run result instead of showing an empty success', async (t) => {
  const renderer = await mountResearchView(t, TaskRuns, { taskId: 'T-HISTORY', onOpen: () => {} }, async url => {
    if (url.endsWith('/result')) return Response.json({ error: { code: 'TASK_NOT_FOUND', message: '运行结果不存在' } }, { status: 404 })
    return Response.json({
      items: [{ run_id: 'R-MISSING', mode: 'mock', status: 'completed', started_at: null }], total: 1, offset: 0, limit: 20,
    })
  })
  const button = renderer.root.findAllByType('button').find(b => text(b) === '查看结果')
  await act(async () => button.props.onClick())
  assert.match(text(renderer.root), /结果读取失败：运行结果不存在/)
})
