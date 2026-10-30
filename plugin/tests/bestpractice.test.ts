// The hooks module, driven the way a session drives it, with the library and the tools stubbed:
// `claude plugin test plugin` runs these with no process, network or file system.
import { expect, test } from 'claude-code/testing'

const PARALLEL = 'mcp__plugin_bestpractice_parallel__web_search'
const EXA = 'mcp__plugin_bestpractice_exa__web_search_exa'
const LONG = 'PASSED\n'.repeat(2_000)

function library(answers: Record<string, unknown>) {
  return async ($: unknown, e: { argv: readonly string[] }) => {
    const asked = e.argv[e.argv.length - 1]
    const answer = asked in answers ? answers[asked] : {}
    return { value: { exitCode: 0, stdout: JSON.stringify(answer), stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  }
}

type On = (event: string, ...rest: unknown[]) => unknown

function inASession(on: On) {
  on('session.cwd', async () => ({ value: '/work/repo' }))
  on('session.id', async () => ({ value: 'session-1' }))
}

function tools(names: string[]) {
  return async () => ({ value: names.map((name) => ({ name, description: '', mcp: name.startsWith('mcp__') })) })
}

test('a long successful run reaches the model as the digest the library wrote', async ($, on) => {
  inASession(on as On)
  on('process.run', library({ digest: { stdout: '[digest] 10153 passed' } }))
  on('tool.call', { tool: 'Bash' }, async () => ({ result: { stdout: LONG, stderr: '', interrupted: false } }))
  const ran = await $.tool.call({ tool: 'Bash', command: 'pytest -v' })
  expect((ran as { result: { stdout: string } }).result.stdout).toBe('[digest] 10153 passed')
})

test('a short run is never sent to the library', async ($, on) => {
  inASession(on as On)
  let asked = 0
  on('process.run', async () => { asked += 1; return { value: { exitCode: 0, stdout: '{}', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } } })
  on('tool.call', { tool: 'Bash' }, async () => ({ result: { stdout: 'ok\n', stderr: '', interrupted: false } }))
  const ran = await $.tool.call({ tool: 'Bash', command: 'pytest -q' })
  expect((ran as { result: { stdout: string } }).result.stdout).toBe('ok\n')
  expect(asked).toBe(0)
})

test('WebSearch is answered by Parallel, naming Exa for what it misses', async ($, on) => {
  inASession(on as On)
  on('process.run', library({ settings: { enabled: true, route_search: true } }))
  on('tool.list', tools(['WebSearch', PARALLEL, EXA]))
  on('tool.call', { tool: PARALLEL }, async () => ({ result: { content: [{ type: 'text', text: 'uv run --frozen keeps the lockfile' }] } }))
  const ran = await $.tool.call({ tool: 'WebSearch', query: 'uv run frozen' })
  const results = (ran as { result: { results: string[] } }).result.results
  expect(results[0]).toContain('uv run --frozen keeps the lockfile')
  expect(results[0]).toContain(EXA)
})

test('a search the founder switched off goes to WebSearch itself', async ($, on) => {
  inASession(on as On)
  on('process.run', library({ settings: { enabled: true, route_search: false } }))
  on('tool.list', tools(['WebSearch', PARALLEL]))
  on('tool.call', { tool: 'WebSearch' }, async () => ({ result: { query: 'q', results: ['from the harness'], durationSeconds: 1 } }))
  const ran = await $.tool.call({ tool: 'WebSearch', query: 'q' })
  expect((ran as { result: { results: string[] } }).result.results[0]).toBe('from the harness')
})

test('Exa before any search through Parallel is sent back to Parallel', async ($, on) => {
  inASession(on as On)
  on('process.run', library({ settings: { enabled: true, route_search: true } }))
  on('tool.list', tools([PARALLEL, EXA]))
  const ran = await $.tool.call({ tool: EXA, query: 'q', objective: 'q' })
  expect((ran as { deny?: string }).deny).toContain('search with Parallel first')
})

test('a listing of agents says what each session is doing', async ($, on) => {
  inASession(on as On)
  on('process.run', library({ roster: { text: '  - fuddy-71: 0012 OTA banner (branch feat/ota)' } }))
  on('tool.call', { tool: 'ListAgents' }, async () => ({ result: { listing: 'fuddy-71\nfuddy-3f' } }))
  const ran = await $.tool.call({ tool: 'ListAgents' })
  expect((ran as { result: { listing: string } }).result.listing).toContain('fuddy-71: 0012 OTA banner')
})

test('a subagent named below the founder\'s floor runs on the floor', async ($, on) => {
  inASession(on as On)
  on('process.run', library({ settings: { enabled: true, subagent_min_model: 'sonnet' } }))
  let started = ''
  on('agent.spawn', async ($inner: unknown, e: { model?: string }) => {
    started = e.model ?? ''
    return { model: started }
  })
  await $.agent.spawn({ prompt: 'find the config', description: 'find', subagentType: 'Explore', model: 'haiku' })
  expect(started).toBe('sonnet')
})

test('a subagent at or above the floor runs on what it named', async ($, on) => {
  inASession(on as On)
  on('process.run', library({ settings: { enabled: true, subagent_min_model: 'sonnet' } }))
  let started = ''
  on('agent.spawn', async ($inner: unknown, e: { model?: string }) => {
    started = e.model ?? ''
    return { model: started }
  })
  await $.agent.spawn({ prompt: 'design it', description: 'design', subagentType: 'Plan', model: 'opus' })
  expect(started).toBe('opus')
})
