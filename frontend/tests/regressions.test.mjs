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

const { default: App } = await loadSource('../src/App.tsx')
const { parseContextLists } = await loadSource('../src/context-input.ts')

function text(node) {
  if (typeof node === 'string') return node
  return (node.children ?? []).map(text).join('')
}

async function openForm(t, fetchHandler) {
  const savedWindow = globalThis.window
  const savedFetch = globalThis.fetch
  globalThis.window = {
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
    }
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
  assert.deepEqual(remote.execution(), { mode: 'real' })
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
