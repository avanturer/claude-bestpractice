// The hooks module of claude-bestpractice: the part of the plugin that runs inside Claude Code.
//
// It changes what the model reads and where a search goes, which no settings hook can: a long
// successful command result becomes a digest with the whole kept on disk (decision 0048), the
// harness's WebSearch is answered by Parallel and then Exa (decision 0049), a listing of the
// agents a session can message says what each session on the repository is doing (decision
// 0050), and a subagent runs no lower than the founder's floor (decision 0051). What any of
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

const KEEP_THROUGH_COMPACTION = [
  'Keep, word for word: the founder\'s requests and decisions, every +merge, +release and',
  '+migration they gave and what it covered, the task card ids and titles in play, the files',
  'changed and why, each failing test by name with its first error line, and what was tried',
  'and failed. Keep the path a claude-bestpractice digest names for its whole output instead of',
  'the output. Drop passing test lists, install and build logs, and file contents that can be',
  'read again.',
].join(' ')

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

function ourRefusal(ran: ToolCallResult): string {
  return 'deny' in ran && typeof ran.deny === 'string' && ran.deny.startsWith('claude-bestpractice') ? ran.deny : ''
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
      if (textOf(found).trim()) {
        searchedThroughParallel.set(loopOf(e), Date.now())
        return routed(e.query, textOf(found), parallel, exa, started)
      }
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
    if (succeeded(ran)) searchedThroughParallel.set(loopOf(e), Date.now())
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
        + 'with the page you want described rather than keywords.',
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

  on('agent.spawn', async ($, e, next) => {
    if (e.fork) return next(e)
    const floor = String((await ask($, 'settings', {})).subagent_min_model ?? '')
    const named = e.model ? tierOf(e.model) : -1
    const model = named >= 0 && named < TIERS.indexOf(floor) ? floor : e.model
    const map = await ask($, 'map', { task: `${e.description}\n${e.prompt}` })
    const prompt = typeof map.text === 'string' && map.text !== '' ? `${e.prompt}\n\n${map.text}` : e.prompt
    return next({ ...e, model, prompt })
  }).catch(async ($, e, next) => next(e))

  on('session.compact', async ($, e, next) => {
    const instructions = e.instructions ? `${e.instructions}\n\n${KEEP_THROUGH_COMPACTION}` : KEEP_THROUGH_COMPACTION
    return next({ ...e, instructions })
  }).catch(async ($, e, next) => next(e))
}
