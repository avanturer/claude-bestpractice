// The hooks module of claude-bestpractice: the part of the plugin that runs inside Claude Code.
//
// It changes what the model reads and where a search goes, which no settings hook can: a long
// successful command result becomes a digest with the whole kept on disk (decision 0048), the
// harness's WebSearch is answered by Parallel and then Exa (decision 0049), a listing of the
// agents a session can message says what each session on the repository is doing (decision
// 0050), a subagent runs no lower than the founder's floor (decision 0051), and the founder sees
// the board beside the chat, a status line and a pane the model is never sent (decision 0074).
// What any of
// that should be is asked of the plugin's Python library through
// `bin/claude-bp-mod`; whatever goes wrong here leaves the call as Claude Code made it.
import type { EngineInterface, Register, ToolCallResult } from 'claude-code'

// Below this a Bash result is not worth a process: `digest.MIN_CHARS` in the library.
const MIN_CHARS = 6_000
// How long one question to the library may take before the result goes on as it was.
const ASK_MS = 20_000
// How Python is started where `python3` is not a program, as `bin/*.cmd` start it on Windows.
const PYTHONS: readonly (readonly string[])[] = [['python3'], ['py', '-3'], ['python']]
// What a shell answers for a program it cannot find, on POSIX and on Windows.
const NOT_FOUND = new Set([127, 9009])

const PARALLEL_SEARCH = /^mcp__.*parallel.*__web_search$/i
const EXA_SEARCH = /^mcp__.*exa.*__web_search(?:_advanced)?_exa$/i
// How long a search through Parallel lets the same loop ask Exa without being sent back.
const PARALLEL_FIRST_MS = 15 * 60_000
// How many of WebSearch's allowed domains become `site:` queries of their own.
const SITE_QUERIES = 3
// The tiers a subagent can name, cheapest first, as `subagents.TIERS` in the library has them.
const TIERS = ['haiku', 'sonnet', 'opus', 'fable']
// The pane the founder opens with `/sessions`, and how often the board is read again besides the
// end of every turn: a session that only waits still sees a sibling's pull request come in.
const BOARD_PANE = 'sessions'
const BOARD_EVERY_MS = 60_000

type BashRecord = {
  stdout?: string
  stderr?: string
  interrupted?: boolean
  isImage?: boolean
  backgroundTaskId?: string
  persistedOutputPath?: string
  persistedOutputSize?: number
}

type Answer = Record<string, unknown>

let python: readonly string[] | null = null
const searchedThroughParallel = new Map<string, number>()
// The pane's rows as the library last gave them; a reload starts them over.
let boardRows: readonly string[] = []

async function ask($: EngineInterface, what: string, request: Answer): Promise<Answer> {
  const script = `${$.plugin.root}/bin/claude-bp-mod`
  const body = JSON.stringify({ cwd: await $.session.cwd(), session: await $.session.id(), ...request })
  for (const interpreter of python ? [python] : PYTHONS) {
    const ran = await $.process.run([...interpreter, script, what], { stdin: body, timeoutMs: ASK_MS })
      .catch(() => null)
    if (ran === null || NOT_FOUND.has(ran.exitCode)) continue
    python = interpreter
    return ran.exitCode === 0 ? parsed(ran.stdout) : {}
  }
  return {}
}

function parsed(text: string): Answer {
  try {
    const value: unknown = JSON.parse(text)
    return typeof value === 'object' && value !== null ? (value as Answer) : {}
  } catch (error) {
    return { error: String(error) }
  }
}

function succeeded(ran: ToolCallResult): boolean {
  return 'result' in ran && ran.result !== undefined && !ran.isError
}

function textOf(ran: ToolCallResult): string {
  if (!succeeded(ran)) return ''
  if (typeof ran.text === 'string') return ran.text
  const content = (ran as { result: { content?: unknown } }).result.content
  if (!Array.isArray(content)) return ''
  return content.map((block: { text?: unknown }) => (typeof block.text === 'string' ? block.text : '')).join('\n')
}

function tierOf(model: string): number {
  const named = model.toLowerCase()
  return TIERS.findIndex((tier) => named === tier || named.includes(`-${tier}`))
}

function loopOf(e: { agentId?: string }): string {
  return e.agentId ?? ''
}

function searchQueries(query: string, allowed: readonly string[] | undefined): string[] {
  const sites = (allowed ?? []).slice(0, SITE_QUERIES).map((domain) => `${query} site:${domain}`)
  return sites.length > 0 ? sites : [query]
}

function routed(query: string, text: string, tool: string, exa: string | undefined, started: number) {
  const next = exa
    ? ` If they miss what you need, ask Exa (${exa}) for that one thing, described as the page you want.`
    : ''
  return {
    result: {
      query,
      results: [`${text}\n\n[claude-bestpractice sent this WebSearch to ${tool}.${next}]`],
      durationSeconds: (Date.now() - started) / 1000,
    },
  }
}

// Reads the board again and shows it: the status line under the prompt, and the pane's rows if it
// is open. An empty answer, which is what `enabled off` and a directory outside every repository
// give, clears the line.
async function showTheBoard($: EngineInterface): Promise<void> {
  const board = await ask($, 'board', {})
  $.ui.status(typeof board.line === 'string' && board.line !== '' ? board.line : undefined)
  boardRows = Array.isArray(board.rows) ? board.rows.filter((row): row is string => typeof row === 'string') : []
  $.ui.invalidate('ui.render')
}

function ourRefusal(ran: ToolCallResult): string {
  return 'deny' in ran && typeof ran.deny === 'string' && ran.deny.startsWith('claude-bestpractice') ? ran.deny : ''
}

// Whether a call to Parallel's search was its turn, so the order goes on to Exa: answered, answered
// with an error (its free-tier limit among them), or refused by anything but this plugin's check of
// what the query carries, which refuses it to Exa too. Counted only on results, a limit error sent
// Exa and WebSearch back to a Parallel that could not answer, for the rest of the run (#291).
function parallelHadItsTurn(ran: ToolCallResult): boolean {
  return ourRefusal(ran) === ''
}

export const register: Register = (on) => {
  on('tool.call', { tool: 'Bash' }, async ($, e, next) => {
    const ran = await next(e)
    if (!succeeded(ran)) return ran
    const out = (ran as { result: BashRecord }).result
    const printed = (out.stdout ?? '').length + (out.stderr ?? '').length
    if (out.interrupted || out.isImage || out.backgroundTaskId) return ran
    if (printed < MIN_CHARS && !out.persistedOutputPath) return ran
    const answer = await ask($, 'digest', {
      command: e.command, stdout: out.stdout ?? '', stderr: out.stderr ?? '',
      persisted: out.persistedOutputPath ?? '',
    })
    if (typeof answer.stdout !== 'string' || answer.stdout === '') return ran
    const digested: BashRecord = { ...out, stdout: answer.stdout, stderr: '' }
    delete digested.persistedOutputPath
    delete digested.persistedOutputSize
    return { result: digested }
  }).catch(async ($, e, next) => next(e))

  on('tool.call', { tool: 'WebSearch' }, async ($, e, next) => {
    if (e.blocked_domains && e.blocked_domains.length > 0) return next(e)
    const settings = await ask($, 'settings', {})
    if (settings.route_search !== true) return next(e)
    const tools = await $.tool.list()
    const parallel = tools.find((tool) => PARALLEL_SEARCH.test(tool.name))?.name
    const exa = tools.find((tool) => EXA_SEARCH.test(tool.name))?.name
    const started = Date.now()
    if (parallel) {
      const found = await $.tool.call({
        tool: parallel, objective: e.query, search_queries: searchQueries(e.query, e.allowed_domains),
      })
      if (ourRefusal(found)) return { deny: ourRefusal(found) }
      if (parallelHadItsTurn(found)) searchedThroughParallel.set(loopOf(e), Date.now())
      if (textOf(found).trim()) return routed(e.query, textOf(found), parallel, exa, started)
    }
    if (exa) {
      const found = await $.tool.call({ tool: exa, query: e.query, objective: e.query, numResults: 8 })
      if (ourRefusal(found)) return { deny: ourRefusal(found) }
      if (textOf(found).trim()) return routed(e.query, textOf(found), exa, undefined, started)
    }
    return next(e)
  }).catch(async ($, e, next) => next(e))

  on('tool.call', { tool: PARALLEL_SEARCH }, async ($, e, next) => {
    const ran = await next(e)
    if (parallelHadItsTurn(ran)) searchedThroughParallel.set(loopOf(e), Date.now())
    return ran
  }).catch(async ($, e, next) => next(e))

  on('tool.call', { tool: EXA_SEARCH }, async ($, e, next) => {
    if (Date.now() - (searchedThroughParallel.get(loopOf(e)) ?? 0) < PARALLEL_FIRST_MS) return next(e)
    const parallel = (await $.tool.list()).find((tool) => PARALLEL_SEARCH.test(tool.name))?.name
    if (!parallel) return next(e)
    const settings = await ask($, 'settings', {})
    if (settings.route_search !== true) return next(e)
    return {
      deny: `claude-bestpractice: search with Parallel first, through WebSearch (this plugin sends it there) or ${parallel}. `
        + 'Exa is for the one thing Parallel\'s results miss: call it again after that search, '
        + 'with the page you want described rather than keywords. A search Parallel answers with '
        + 'an error, its rate limit among them, counts as that search.',
    }
  }).catch(async ($, e, next) => next(e))

  on('tool.call', { tool: 'ListAgents' }, async ($, e, next) => {
    const ran = await next(e)
    if (!succeeded(ran)) return ran
    const listed = (ran as { result: { listing?: unknown } }).result
    if (typeof listed.listing !== 'string') return ran
    const answer = await ask($, 'roster', {})
    if (typeof answer.text !== 'string' || answer.text === '') return ran
    return { result: { ...listed, listing: `${listed.listing}\n\n${answer.text}` } }
  }).catch(async ($, e, next) => next(e))

  on('session.start', async ($, e, next) => {
    const started = await next(e)
    await $.command.register({ name: BOARD_PANE, description: 'What every session on this repository is doing' })
    $.clock.every(BOARD_EVERY_MS, () => showTheBoard($))
    void showTheBoard($)
    return started
  }).catch(async ($, e, next) => next(e))

  on('turn.complete', async ($, e, next) => {
    const completed = await next(e)
    void showTheBoard($)
    return completed
  }).catch(async ($, e, next) => next(e))

  on('command.run', { command: BOARD_PANE }, async ($) => {
    await showTheBoard($)
    const opened = await $.ui.open({ id: BOARD_PANE, title: 'Sessions' })
    return { text: opened.isPlaced ? 'The sessions pane is open.' : 'The sessions pane opens where there is room.' }
  }).catch(async ($, e, next) => (next.called ? next(e) : { text: 'The sessions pane could not be read.' }))

  on('ui.render', { component: 'Pane', requestId: BOARD_PANE }, async ($, e) => {
    const { Box, Text } = $.ui.resolve(e)
    const rows = boardRows.length > 0 ? boardRows : ['No other session, pull request or red suite here.']
    return h(Box, { flexDirection: 'column' }, ...rows.map((row) => h(Text, null, row)))
  }).catch(async ($, e, next) => next(e))

  on('agent.spawn', async ($, e, next) => {
    if (e.fork) return next(e)
    const floor = String((await ask($, 'settings', {})).subagent_min_model ?? '')
    const named = e.model ? tierOf(e.model) : -1
    const model = named >= 0 && named < TIERS.indexOf(floor) ? floor : e.model
    const map = await ask($, 'map', { task: `${e.description}\n${e.prompt}` })
    const prompt = typeof map.text === 'string' && map.text !== '' ? `${e.prompt}\n\n${map.text}` : e.prompt
    return next({ ...e, model, prompt })
  }).catch(async ($, e, next) => next(e))
}
