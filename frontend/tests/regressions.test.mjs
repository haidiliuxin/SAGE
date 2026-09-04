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
    async submit() {
      await act(async () => renderer.root.findByType('form').props.onSubmit({ preventDefault() {} }))
    },
  }
}

function remoteFlow(strategyIds = ['S1']) {
  let submitted
  const taskId = 'T-TEST'
  const runId = 'R-TEST'
  const strategies = strategyIds.map((strategy_id, index) => ({
    strategy_id, strategy_name: strategy_id === 'S4' ? 'Context' : 'Baseline',
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
      warnings: [],
    }
    else if (url.endsWith('/plan')) data = { task_id: taskId, planner_type: 'mock', total_time_budget: 300, strategies, status: 'planned', warnings: [] }
    else if (url.endsWith('/execute')) data = { task_id: taskId, run_id: runId, status: 'running', started_at: new Date().toISOString() }
    else if (url.endsWith('/status')) data = { task_id: taskId, run_id: runId, status: 'completed', progress: 1, current_strategy: null, elapsed_time: 2, tested: 123, recovered: 2, message: 'done' }
    else if (url.endsWith('/result')) data = {
      task_id: taskId, run_id: runId, status: 'completed', total_time: 2,
      total_tested: 123, total_recovered: 2, finished_at: new Date().toISOString(),
      strategy_results: strategies.map(({ strategy_id }) => ({ strategy_id, time: 1, tested: 100, recovered: 1, success_rate: 0.01 })),
    }
    else throw new Error(`Unexpected URL: ${url}`)
    return Response.json(data)
  }
  return { handler, submitted: () => submitted }
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
