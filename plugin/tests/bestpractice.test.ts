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

// What Parallel's free tier answers once a run of research agents has used it up (#291).
const LIMITED = { isError: true as const, result: 'Free tier rate limit exceeded', text: 'Free tier rate limit exceeded' }

function answers(text: string) {
  return async () => ({ result: { content: [{ type: 'text', text }] } })
}

test('Exa after a search Parallel answered with its rate limit is let through', async ($, on) => {
  inASession(on as On)
  on('process.run', library({ settings: { enabled: true, route_search: true } }))
  on('tool.list', tools([PARALLEL, EXA]))
  on('tool.call', { tool: PARALLEL }, async () => LIMITED)
  on('tool.call', { tool: EXA }, answers('from Exa'))
  await $.tool.call({ tool: PARALLEL, objective: 'q', search_queries: ['q'], agentId: 'researcher' })
  const ran = await $.tool.call({ tool: EXA, query: 'q', objective: 'q', agentId: 'researcher' })
  expect((ran as { deny?: string }).deny).toBeUndefined()
  expect(JSON.stringify((ran as { result: unknown }).result)).toContain('from Exa')
})

test('WebSearch goes on to Exa when Parallel answers with its rate limit', async ($, on) => {
  inASession(on as On)
  on('process.run', library({ settings: { enabled: true, route_search: true } }))
  on('tool.list', tools(['WebSearch', PARALLEL, EXA]))
  on('tool.call', { tool: PARALLEL }, async () => LIMITED)
  on('tool.call', { tool: EXA }, answers('uv run --frozen keeps the lockfile'))
  const ran = await $.tool.call({ tool: 'WebSearch', query: 'uv run frozen', agentId: 'researcher' })
  expect((ran as { deny?: string }).deny).toBeUndefined()
  const results = (ran as { result: { results: string[] } }).result.results
  expect(results[0]).toContain('uv run --frozen keeps the lockfile')
  expect(results[0]).toContain(`sent this WebSearch to ${EXA}`)
})

test('WebSearch goes on to itself when Parallel and Exa both answer with errors', async ($, on) => {
  inASession(on as On)
  on('process.run', library({ settings: { enabled: true, route_search: true } }))
  on('tool.list', tools(['WebSearch', PARALLEL, EXA]))
  on('tool.call', { tool: PARALLEL }, async () => LIMITED)
  on('tool.call', { tool: EXA }, async () => LIMITED)
  on('tool.call', { tool: 'WebSearch' }, async () => ({ result: { query: 'q', results: ['from the harness'], durationSeconds: 1 } }))
  const ran = await $.tool.call({ tool: 'WebSearch', query: 'q', agentId: 'researcher' })
  expect((ran as { result: { results: string[] } }).result.results[0]).toBe('from the harness')
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

test('a subagent is handed the map of its own task', async ($, on) => {
  inASession(on as On)
  on('process.run', library({ settings: { enabled: true, subagent_min_model: 'sonnet' },
    map: { text: '## Where things are for this task\nmobile/src/ota/check.ts: checkForUpdate' } }))
  let prompt = ''
  on('agent.spawn', async ($inner: unknown, e: { prompt: string }) => {
    prompt = e.prompt
    return { model: 'sonnet' }
  })
  await $.agent.spawn({ prompt: 'fix the OTA check', description: 'ota', subagentType: 'general-purpose', model: 'sonnet' })
  expect(prompt).toContain('fix the OTA check')
  expect(prompt).toContain('mobile/src/ota/check.ts')
})

const SESSIONS_PANE = {
  plugin: 'bestpractice',
  component: 'Pane',
  requestId: 'sessions',
  props: {
    title: 'Sessions',
    isFocused: false,
    bodyColumns: 80,
    placement: 'dock',
    scroll: { offset: 0, bodyRows: 20 },
    view: {},
  },
} as const

const BOARD = {
  line: 'bestpractice · 2 other session(s) · #297 wait for your `+merge`',
  rows: ['ledger-export-3c: 0006 Add CSV export · feat/export', '#297 on feat/x: ready to merge'],
}

function statusLines(on: On): (string | undefined)[] {
  const shown: (string | undefined)[] = []
  on('ui.status', async ($: unknown, e: { text: string | undefined }) => {
    shown.push(e.text)
    return { value: undefined }
  })
  return shown
}

test('the founder sees the board as a status line, which the model is never sent', async ($, on) => {
  inASession(on as On)
  on('process.run', library({ board: BOARD }))
  on('ui.open', async () => ({ value: { isPlaced: true } }))
  const shown = statusLines(on as On)
  await $.command.run({ command: 'sessions', args: '' })
  expect(shown).toContain(BOARD.line)
})

test('the sessions pane draws a row for each session and pull request, in the terminal and VS Code', async ($, on) => {
  inASession(on as On)
  on('process.run', library({ board: BOARD }))
  on('ui.open', async () => ({ value: { isPlaced: true } }))
  statusLines(on as On)
  await $.command.run({ command: 'sessions', args: '' })
  for (const surface of ['terminal', 'vscode'] as const) {
    const ui = await $.ui.mount({ ...SESSIONS_PANE, surface })
    expect((await ui.find({ type: 'Text', text: /ledger-export-3c/ }))?.text).toBe(BOARD.rows[0])
    expect((await ui.find({ type: 'Text', text: /#297/ }))?.text).toBe(BOARD.rows[1])
    await ui.unmount()
  }
})

test('a board the library has nothing for clears the line, as enabled off does', async ($, on) => {
  inASession(on as On)
  on('process.run', library({ board: {} }))
  on('ui.open', async () => ({ value: { isPlaced: true } }))
  const shown = statusLines(on as On)
  await $.command.run({ command: 'sessions', args: '' })
  expect(shown).toEqual([undefined])
})

test('a read of one of this plugin\'s skills is counted, and another plugin\'s is not', async ($, on) => {
  inASession(on as On)
  const asked: string[] = []
  on('process.run', async ($$: unknown, e: { argv: readonly string[] }) => {
    asked.push(e.argv[e.argv.length - 1])
    return { value: { exitCode: 0, stdout: '{}', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  on('skill.prompt', async (_: unknown, e: { text: string }) => ({ text: e.text }))
  const skill = (pluginName: string, name: string) => $.skill.prompt({
    skill: name, text: 'the skill', frontmatter: {}, source: 'plugin', pluginName,
  } as never)
  await skill('bestpractice', 'llm-first-code')
  await skill('superpowers', 'brainstorming')
  expect(asked.filter((what) => what === 'skill').length).toBe(1)
})
