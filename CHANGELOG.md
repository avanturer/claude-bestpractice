# Changelog

## v1.22.0

Nobody has to say "prepare for the compaction" any more.

v1.21.0 built the restore: what was captured is handed back afterwards. It left the half
that decides whether anything WAS captured — substance that never left the conversation
cannot be restored by anything, because nothing wrote it down.

### The compaction is blocked once, for the notes

`PreCompact` is the one event that can block, and this is the one thing worth blocking for.
A session that has actually changed files is stopped exactly once, at the moment the window
still holds the material, and told to write down the three things a restored window cannot
reconstruct: what it has learned that is not in the code yet, what it tried and abandoned
and why, and anything decided that outlives the task. Each names the command that files it.

Once per session, marked before the block is raised, so a session that ignores it, crashes,
or meets the next compaction is never stopped again — one unignorable interruption is the
whole budget. A session that changed nothing is never stopped at all. The checkpoint is
written either way: the block is on top of the flush, never instead of it.

### The checkpoint carries the shape of the work, not just the last few turns

It now snapshots the claimed task with its body and paths, what is queued beside it, what
other sessions hold, the approaches already ruled out, and the open items. All of it is on
disk before the hook runs, so this is a read and a join — still no model call, still
extractive, as the file has promised since it was written.

So a restored window opens with the goal, the plan, the findings and the dead ends, rather
than with a summary of the last few messages.

Found while testing it: `list.extend` returns None, so `extend(...) or append(...)` always
appends, and "(none recorded)" printed underneath a real attempt on the very first run.

## v1.21.0

Compaction stops losing the thread, the last worktrees that still prompt are moved, and
this plugin can finally see what auto mode refused.

### What compaction destroys is handed back

The checkpoint has been written on every compaction since the first release and never read
— which is the exact pattern `provenance` opens by naming as how memory features fail:
capture something on every checkpoint and never look at it again. This plugin was doing it
to itself, while compaction is the largest destroyer of in-context state there is.

`SessionStart` with `source: compact` now hands the checkpoint back: what was asked, what
was underway, and which files had moved, written at the time rather than summarised
afterwards. It fires on compaction and nowhere else, so the always-on budget pays nothing
for it — which is why it can afford to be generous when it does fire.

Nothing here asks the model to prepare for a compaction. Preparation happens at write time,
by the ledger and the attempt log; this is the restore, and it is a hook rather than a
request.

### The last trees that still prompt are moved

`EnterWorktree` prompts on any path outside `.claude/worktrees/`, unconditionally, before
permissions are consulted. v1.14.0 changed where new trees are made and left the existing
ones exactly where they were — so every entry into a tree provisioned before that release
still asked, and would have forever.

`0005-trees-into-the-no-prompt-zone` moves them with `git worktree move`, which carries the
branch and any uncommitted work across; verified against a dirty tree. Without `--force`,
so a locked tree or one with submodules is left alone rather than broken, and the registry
is re-pointed or the next refusal would send a session to a path that no longer exists.

### Auto mode's refusals are visible

`PermissionDenied` fires when auto mode denies a tool call. Until it existed, this plugin
decided one half of the permission question and could not see the other half at all — every
"it asked me again" arrived as a screenshot, and eight worktree rules sat in
`permissions.allow` with six of them inert and nobody able to say which.

Recorded and reported, never overturned. `retry: true` is available on this event and is
deliberately unused: a call this gate was willing to vouch for never reaches the classifier,
because `allow_tool` ends the pipeline — so a denial arriving here is one the plugin did not
approve, and reversing it would be the gate approving by the back door what it declined at
the front. Credentials in the denied command are scrubbed before anything is written.

## v1.20.0

The tool-call ceiling is off, and it is taken back out of repositories that kept it.

### There is no ceiling unless somebody asks for one

A ceiling catches DURATION. A runaway is a SHAPE — and the two guards that read shape,
`max_repeat_signature` and `loop_detect`, are what actually stop one. By count alone an
eleven-hour measuring session is indistinguishable from a loop, so the ceiling only ever
fired on the wrong one; and when it fired it refused everything, including the read that
would have shown the measurement that had just finished.

v1.17.0 fixed three defects in how it counted. That left a fourth: the thing itself.
`max_tool_calls` now defaults to 0, which means off. The key stays, because a number
somebody chose is a different thing from a number this plugin invented — any value above
zero enforces exactly as before.

The shape detectors are unchanged and still hold: four identical calls in a row is still
refused, and still clears the moment anything else happens.

### And it is lifted where it is already written down

`config.save` writes every key, so `"max_tool_calls": 2000` is on disk in every repository
that ever saved a config. Changing the default alone would have left all of them blocked —
and the founder upgrades on top of what was working. `migrate._REPAIRS` gained
`0004-lift-the-tool-call-ceiling`, which turns off exactly the number this plugin chose and
leaves any other value alone, because a number the founder set is their word on the subject.

## v1.19.0

Worktrees, end to end: the board tells the truth about what is closed, moving around never
asks, and trees the sweep cannot clear are named instead of piling up (#123).

### Closing a task sticks

`load_all` reads every worktree of the clone and keeps the most advanced copy, and `next`
was ranked above `done`. So closing a task in your own worktree was outvoted by every
sibling still carrying the queued copy: ten tasks closed, ten still in NEXT, and a board
that cannot say what is finished is not a board. The ranking follows the lifecycle now —
a task file only moves forward, so the copy that travelled furthest is the one written last.

`startable` had the same bug one level down: asking for `next` alone scanned only `next/`
directories, so the dedup never ran and it offered work this clone had already finished.
A state is now resolved across every tree first and filtered afterwards.

The trade, stated: a task closed by mistake in one tree now hides an active `doing` copy in
another. That costs the session working on it nothing — it holds its own file — against a
board that was wrong about everything ever closed.

### Moving around is not a question

A line that is only navigation vouched for nothing, because navigation contributes no
reason of its own. So `cd` back into the worktree this gate had just ordered the session
into went to the classifier, along with `pwd` and `true` — and a session whose shell had
landed in the main checkout was asked to authorise the way back. Navigation anywhere inside
the clone is now vouched for outright. Reads and writes are still judged against the tree
the session owns, so a `cd` elsewhere buys the move and nothing else.

### Trees the sweep cannot clear are named

The sweep is built out of commands that REFUSE — `git worktree remove` without `--force`
will not touch a tree with modifications — so a session that died mid-edit leaves a tree no
sweep will ever clear. That is correct, and it is also how eight of them accumulate: the
plugin will not delete the work, and until now nothing named it either. The board reports
them, never removes them, and says explicitly to ask before discarding anything.

Measured on four finished trees with every session gone: the empty one, the merged one and
the one with unmerged commits are all swept (the branch survives in the last case, so
nothing committed is lost). The one holding uncommitted work stays — and is now the only
one the founder has to think about.

## v1.18.0

Verifying this plugin was writing 1,052 lines into the founder's own settings, and nothing
could remove a block whose repository had been deleted. Both are mine, from v1.15.0 (#121).

### Verification no longer writes to a real person's settings

`policy` writes to `~/.claude/settings.json` on purpose — that is the feature. Under test,
every integration case provisions a temporary repository and runs the real hooks in it, and
those hooks inherited the ambient `HOME`. One `make check` added 1,052 entries; one
`claude-bp-doctor` added 68. A machine that had verified a few releases was asking the
classifier to consider 288 repositories, 287 of them gone — 336 KB of prose, read on every
call in every project on that machine.

Fixed twice on purpose. `tests/conftest.py` points `HOME` at a sandbox before the first test
module is imported, and the doctor gives every gate it runs a per-run sandbox of its own —
that is the cause. And `policy` refuses to write about a repository under the temp root into
a settings file that is not, which makes the class of accident unrepeatable from a call site
nobody has written yet. Measured after: 0 written, from 392 in the two test files that were
worst.

The doctor's leak was not only a test problem — it wrote into the settings of anyone who ran
`claude-bp-doctor`, which the plugin tells them to run on install.

### A block for a repository that is gone can be dropped

`--apply` touches only its own repository's marker, which is right for two live repositories
and left no path at all for a dead one. `claude-bp policy --prune` drops the blocks this
plugin wrote whose path is no longer on disk, and the dry run and the board report the count
first.

Deleting rather than reporting, which is the opposite of how dead `permissions.allow` rules
and stale instruction lines are treated — the difference is authorship. Those are lines the
founder wrote and only they should remove them. These are lines the plugin wrote about
repositories that no longer exist, and leaving them means the founder hand-editing settings
to clean up after the plugin's own test suite.

Not on disk at all, rather than "not a git repository": an unmounted disk whose mount point
still exists keeps its block. And since the plugin's own commands are vouched for, the agent
runs the prune — the founder never touches the file.

## v1.17.0

Five symptoms from one eleven-hour measuring session. Four were this plugin's.

### The ceiling counted attempts, including the ones it refused

"this session has made 2015 tool calls, past the ceiling of 2000" arrived eleven hours into
a session of ssh, pytest and paired comparisons, and then refused every call — including
reading the file holding the measurement that had just finished. Two thousand calls over
eleven hours of measurement is not a runaway.

Three changes, and each was a separate defect. A call this gate REFUSED no longer counts:
every refusal used to push the session closer to a wall it would then hit for having been
refused. The message names `max_tool_calls` and the command that raises it. And that command
is exempt from the ceiling — one that also refuses the only way past it is not a ceiling, it
is the end of the session, which is the shape of #108 in a place where the founder may be
asleep.

### A metric is not a credential

`TOKEN` is in the credential name list and belongs there. It is also the most common word in
a machine-learning metric, so `tokens_per_second: 1043.7712` read as an assigned secret and
the gate refused the command that was reading a training log. A wholly numeric value is now
exempt: no credential worth rotating is a bare number, and every real credential FORMAT has
its own detector. The trade — `SECRET_KEY = '12345678'` passes — is deliberate and has a test
saying so.

### The gate stopped quoting itself as the task

v1.16.0 stopped a `<task-notification>` becoming the session's task statement. The other
source was closer to home: the drift block's own text came back as a prompt, so the gate was
measuring 140 files against its own previous refusal. Anything beginning `claude-bestpractice:`
is this plugin's voice and is never the record of what the founder asked for.

### What is committed on this branch is not this turn's drift

Forty-nine commits in, the gate demanded 140 files be reverted, four times in one turn, and
the founder was asleep. Drift is about unreviewed spill; a committed file is already in the
flow this plugin enforces — it is in the branch, it will be in the pull request, and the
review gate reads that diff. The cost is stated rather than hidden: drift now catches what is
still uncommitted.

### A review finding is raised once, not on every commit

The same seven findings arrived on every commit for twenty-plus commits. A signal that
repeats unchanged stops being read at all. Each is now raised once, keyed by what it is about
— detector, file, and the offending line's text, so the same code moved down a file is not a
rediscovery — and the board carries it from there.

### Not ours: a directory named `eval/` (#117)

Measured again on this version, including the exact `cd .../eval && python hard_cases.py`
form: this gate allows it. The refusal comes from Claude Code's own worktree-isolation guard,
below where a hook approval reaches. Detail on the issue.

## v1.16.0

Three reports from one overnight run, two of them ours.

### A harness block is never the task (#118)

A background-task completion notice became the session's task statement, and the
scope-drift refusal then quoted a tool-use id back at an agent whose 136 changed files were
all reported as out of scope — during unattended overnight work, which is exactly when
nobody is there to answer a gate that says it cannot be answered in prose.

The cause was a name list: `background-task` was on it, `task-notification` was not. Names
are now the cheap half. The half that does not depend on having guessed every tag the
harness will ever add asks about the SHAPE — a message that is one XML element and nothing
else was written by the harness, not typed by the founder, and never becomes the record of
what they asked for. A founder pasting XML is unaffected, because they paste it into a
sentence.

Found while mutation-testing the fix: removing the name list broke no test, which meant the
tests only covered a block arriving alone. A notification appended to a real instruction is
the other case, and it now has its own.

### The plugin's own commands need no permission (#116)

`claude-bp policy --apply` was refused by the auto-mode classifier, with the standard advice
that the founder add a Bash permission rule to their settings. The command whose entire
purpose is that the agent maintains that file could only run once the founder had
hand-edited that file. That is the loop #113 was about, one level in.

Two changes. The refresh happens in the SessionStart hook, where no classifier stands — the
same layer `worktree.trust()` already writes from, and doing exactly what the command did:
facts only, marked entries only. And this plugin's own commands are vouched for, resolved
to the `bin/` of THIS install rather than by name, because every refusal it prints names one
of them as the way out. `claude-bp adopt` is excluded: it moves another tool's hook entries,
which is not this plugin's own state.

A doctor check now drives the whole path through the real hook against a throwaway HOME,
because the feature passed 31 of 31 checks while being unusable on the founder's machine.

### Not ours: a directory named `eval/` (#117)

Reported against this plugin and measured here: the string `eval` appears nowhere in it, and
every command in the report — `ls .../eval/`, `cd .../eval && pytest`, and a `;`-separated
pair — is allowed by this gate, most of them vouched for outright. The refusal comes from
Claude Code's own worktree-isolation guard, one layer down, where a hook approval cannot
reach. Details on the issue.

## v1.15.0

The founder was the integration layer between auto mode and this plugin. He is not any more.

### The plugin tells auto mode what this repository is (#113)

Two layers answer the same question — may this call proceed unattended. The classifier
answers it from prose in `~/.claude/settings.json`; this plugin answers it from state it
computes on every hook call. The only thing joining them was the founder retyping one half
into the other: 8,940 bytes of hand-written policy on one machine, most of it authored
mid-session, at the moment a prompt had already interrupted something else — which is the
worst possible incentive for writing a permission rule.

`claude-bp policy --apply` writes the derivable half, and `Setup` runs it once per project.
The line it draws is between a fact and a grant. Facts — where this repository is, its
remotes, its trunk, its checks, that sessions share one clone through worktrees under
`.claude/worktrees/` — are re-derived from the repository every run, so nothing an agent
says can change what gets written. That is what makes it safe for the agent to run rather
than the founder. Grants are not written at all: `autoMode.allow` widens what may proceed
unattended, and a session that has just been interrupted has a direct motive to widen it.

Nothing hand-written is touched. Every generated line carries a marker naming this
repository, and only lines carrying it are read, replaced or removed — so the founder's own
prose survives verbatim, another governed repository's block survives untouched, and two
repositories on one machine refresh independently. Decision 0008.

### Rules that no longer do anything are named

Eight worktree entries in one `permissions.allow`: six inert because the vouch answers
`git worktree` by predicate, and two that cannot fire at all because the CLI's own safety
check outranks them. Nothing anywhere told the founder which was which — a hand-maintained
list has no expiry and no test. They are reported now, and never deleted: a rule that is
redundant here may be why something works in another repository this install cannot see.

### A standing instruction whose subject has left the repository is reported

One environment rule still stated that the production SSH key lived inside the checkout, a
day after it had been moved out and revoked on the server. Every session read it. The same
machinery this plugin already points at its own knowledge anchors now points one directory
up: a prescriptive line in `CLAUDE.md` or `.claude/rules/*.md` naming a path that is not
there is surfaced on the board, with the instruction to tell the founder and to leave their
file alone.

There is deliberately no generated block in `CLAUDE.md`. Session start already delivers
those facts, fresher and cheaper; a second copy would double the always-on cost to say the
same thing twice.

## v1.14.0

Every step this plugin orders, it now takes without asking — and every remedy it names is
one the session can actually reach.

### Worktrees are made where entering them is silent (#111)

Since CLI v2.1.206 `EnterWorktree` prompts for approval on any path outside
`.claude/worktrees/`, unconditionally, before permissions are consulted at all. So the gate
ordered a move and the founder was asked to authorise it — every time, in a repository with
eight sibling trees. No hook approval and no `permissions.allow` entry could clear it: the
prompt is that tool's own safety check.

Trees are provisioned under `.claude/worktrees/` now, anchored to the main checkout so they
never nest inside one another. The reason they used to sit outside the repository — untracked
noise in every status, glob and scan — is paid rather than dropped: the location is excluded
in `.git/info/exclude`, which is per-clone and not the founder's `.gitignore`, and `.claude/`
is a dot-directory the search tools skip by default. Both are asserted by the doctor.

Ownership moved with them. `owned_by_session` was plain containment, so a main checkout that
now CONTAINS every session's tree owned all of them — the silent cross-tree overwrite this
plugin exists to prevent, reintroduced by a change two files away. Caught by a test that had
been passing for eleven versions.

### `ExitWorktree` is vouched for on the same terms as entering (#110)

Leaving was the one worktree call the gate stayed silent on, while `git worktree remove`
through Bash — the identical action in the other spelling — was already vouched for. Keeping
the tree is approved unconditionally, removing it when the tree holds no uncommitted work,
and `discard_changes` never.

### The pull request stops being a formality

The founder watched the idea, the checks and the commits go past in the chat, and was then
asked whether to open the pull request. Opening one and merging one this gate has just found
no blockers for are both vouched for, so there is no prompt left to hide behind; a call that
names somebody else's repository is not, and the permission layer decides that one. And a
turn that ends with finished work and no pull request is blocked once, with the instruction
to open it and then merge it — bringing the founder a conflict that needs their judgement,
and nothing else.

### A gate switch is the founder's word, not the session's (#108)

`evidence-gate` blocked a turn and offered, as a way out, editing a file `pre-tool` refuses
in the same breath. Seven messages named that file. There is one door now —
`claude-bp set <key> <value>` — and its key is the founder's own message: `prompt-capture`
records a switch they asked for in their words, where no session can write it, and the word
is consumed on use. `test_command` is not settable this way at all; `claude-bp ci` owns it,
because it runs the command before writing it. Decision 0006, proved by a new doctor check.

### A knowledge layer in another shape is not an absent one (#112)

The plugin put its own layer in `.claude/rules/` and then judged whether a layer existed by
looking for its own four filenames — so a repository with `CLAUDE.md` and eight rule files in
that exact directory was told, on every session start, to run `claude-bp init`. It reads what
is there now, counts what those files cost in every turn (nothing else was measuring them),
and tells the session to read them and put new rules where the existing ones are. They are
never rewritten. Decision 0007.

### An upgrade reconciles what differs, rather than ticking off names

Repairs are keyed by revision, not by name. A repair whose implementation gets better was
already recorded as done in every repository that ran the old one, and those were exactly the
repositories that never got the improvement — which matters because the founder upgrades on
top of what was working, several versions at a time. A record written before revisions existed
reads as revision 0, so every repair runs again there, and what an upgrade actually changed is
said on the board once.

## v1.13.0

A card before the code, and an upgrade that fixes the repository it lands in.

### A card is asked for at the first write, not at the finish

v1.12.0 made the board binding, but it asked at the END: a whole turn of edits happened
first and the board became true only in retrospect. The first write of a session the
founder has briefed is now refused until a task on the board says who is doing what.

Never a wedge — the way out is a Bash command, and Bash is not what this refuses. The
ledger's own files are exempt, or filing the card would be refused by the rule demanding
one. And it stays quiet for a session with no statement from the founder: a card records
work somebody asked for, and demanding one where nothing was asked is the gate inventing a
process rather than recording one. The Stop demand still catches that session at the end.

### An upgrade repairs, it does not merely behave better next time

The founder upgrades on top of what was working, so a fix that only improves the next
repository is half a fix. `migrate._REPAIRS` gained `0003-absorb-scratch-todos`: a
`TODO-*.md` a previous session wrote as a stand-in — because the ledger could not park a
task yet — is pulled into the ledger, and its file is rewritten to a pointer so nothing
that linked to it breaks and git keeps the whole text.

A document the founder curates is still never touched. Deciding what in one is a task
needs judgement a regex does not have, and rewriting it on a hunch is worse than the
duplicate; those are reported, with `adopt --brief`, and left to the agent.

This reverses part of an earlier rule, deliberately and with its reasoning recorded:
"nothing is adopted without being asked" holds for what the founder curates and does not
hold for a workaround the plugin's own absence caused.

### Decision 0005, so this is not re-decided every release

`The plugin absorbs what it owns, and an upgrade repairs the repository it lands in` is
now a decision record, indexed and injected into every session: nothing the plugin owns is
reimplemented beside it, every change that leaves state behind ships its repair, and
duplicates are absorbed where that is mechanical and refused where it is not.

## v1.12.1

The scope-drift gate could wedge a session with no way out that did not go through the
founder (#106).

### A pasted terminal log is not a statement of work

The founder pastes the tail of a deploy run. It becomes the session's task. Every drift
refusal then quotes `Task was: (.venv) hedge@AVANTURER-PC:~/dev/startups/fuddy$ …` back at
an agent that cannot possibly satisfy it, and the founder is pulled in to type a filename
before any further work can be reported.

A paste is now recognised — a shell prompt, a traceback, `npm ERR!`, a timestamped log —
and is neither the statement nor the blank-board fallback. The first line decides, so
"this failed, look: <traceback>" stays an instruction with evidence attached. An empty
statement is the honest state for a paste, and it leaves `task_paths` empty, which
disables the drift check rather than enforcing it against a shell prompt.

### What the gate gave up on does not come back

`stop_hook_active` is false on the first Stop of every new message, so the escalation
ceiling was never consulted there: each new message bought one guaranteed block on the
same paths, forever, including the message asking the founder to clear it. Giving up and
then asking again is the gate contradicting its own decision. What the ceiling releases is
recorded and stays released — the founder still sees it, as an unverified attempt and an
open item on the board.

### Work already merged is not this session's drift

A file whose content is identical on the trunk has been through whatever review the
founder runs. Compared by blob rather than by name, because a file that exists there with
different content is exactly the case drift is for.

Found while fixing it: `git rev-parse` ECHOES an unresolvable argument instead of failing,
so an absent `origin/HEAD` came back as the literal string `origin/HEAD:file.py` — non-empty,
ending the trunk search at the first name and making every merged file look unmerged. The
result is checked against the SHA shape now.

## v1.12.0

The vouch stopped being a list of strings and became a question about the command.

### Measured, same session profile, before and after (#102)

35 calls in the shape three days of transcripts had: 3 production actions, 9 reads, 6
project checks, 8 writes and commits in the session's own tree, 9 scratchpad and config
reads.

| | v1.11.0 | v1.12.0 |
|---|---|---|
| vouched, no prompt | 2 | 29 |
| sent to the classifier | 33 | 6 |
| production actions vouched | 0 | **0** |

The six still asked about are the three production actions — correctly — and three reads of
paths outside the repository, which this deliberately does not cover.

### Why a predicate and not a longer list

`make test` was vouched and `ruff check src/` was not, so the founder went back to writing
paragraphs into `autoMode.allow` describing which tree the session owns and what this
project's checks are. Both are things the plugin computes on every hook call. Prefix rules
cannot see inside `cd backend && ruff check && pytest -q`; the classifier can parse it but
does not know which tree this session owns. The plugin is the only layer with both.

Now vouched, by parse:

- **Reads that change nothing** — `git log/diff/status/show/rev-parse/ls-files/blame`,
  `cat/head/tail/grep/wc` over paths inside this tree.
- **This project's checks in any spelling** — pytest, ruff, mypy, tsc, jest, eslint, go
  test, cargo test, `npm run lint`, `make <check-target>`, `python -m pytest`, `bash -n`.
- **Every segment of a compound command**, each judged alone.
- **Writes and commits in the tree the session occupies** — not only trees this plugin
  provisioned. A worktree the founder made by hand counts.

### Three rules keep a predicate from becoming a bypass

- **Whitelist, never blacklist.** A program not named is not vouched for, so production is
  out by construction and every new surface arrives silent rather than approved.
- **The line, whole.** No env assignment stripped, no wrapper unwrapped, no `$(…)`, no
  backtick, no redirection. `GIT_PAGER='sh -c …' git log` is not a read; `git -c
  core.pager=… log` is not a read; `cat x > /etc/cron.d/y` is not a read.
- **One unqualified segment ends the line**, because `allow_tool` approves the line and
  there is no half of it to approve.

`bundle exec` and `uv run` are judged by the command they carry, `git commit --no-verify`
is refused a vouch by the plugin whose central claim is that a finish needs evidence, and
`go test github.com/…` is somebody else's code over the network rather than this project's.

### The rule is published, not only applied

`claude-bp status` gained a `VOUCHED FOR` section listing what is waved through in this
repository and what is not. A rule the founder can only discover by noticing which prompts
stopped appearing is one they will reverse-engineer into a hand-written paragraph — which
is how this issue started (#82).

### A second ledger is refused while it is still one file (#103)

The registry check ran at SessionStart and nowhere else, so it could only ever report
documents that already existed. A session that **created** one mid-session was told
nothing: the duplicate was written, wired into three entry points and committed across two
commits before a merge conflict with another session's migration made it visible. The
founder had asked for a TODO system "while the plugin does not support it", and neither of
them noticed that it does.

Writing a task registry beside a populated ledger is now refused at write time, naming
both ways forward: `claude-bp-plan add` to put the work where the sessions read it, or
`adopt --ignore <file>` if the document is the founder's to curate.

Narrow on purpose, because a false refusal costs a document somebody meant to write: only
when the ledger already holds tasks, only for a file that does not exist yet — so
migrating an existing registry is never refused — never the ledger's own task documents,
never a pull-request template, and never a document already declared curated.

That last escape did not work when it was written: `adopt --ignore` refused a path that was
not in the tree, and the file does not exist precisely because the gate just refused it.
It now records the decision and says the file is not there, which is how `park` settled the
same question.

### Tasks can say how they relate to each other (#104)

A research session produces work that is not independent — B is wrong until A lands, or
two changes individually swing the result the wrong way and only mean something shipped
together. None of it was expressible, so it went into a markdown section and was hoped to
be read, which is the failure the ledger exists to end, one level up.

```
claude-bp-plan add "score zero for prohibited only" --after 0035
claude-bp-plan park "recolour the ingredient"       --with 0033
```

```
NEXT
  0001  fix the prohibited-substance flag
  0002  score zero only for prohibited substances  [after 0001]
  0003  recolour the ingredient  [with 0004]

4 next (3 ready to start) · 0 in flight · 0 paused · 0 done
```

`claim` says when an earlier task has not landed rather than proceeding silently, and says
it without refusing: starting ahead of the order is sometimes right, and starting ahead of
it *without knowing* is what cost a measurement that could no longer be taken.

`add` also gained `--paths`, `--done-when`, `--after` and `--with`. Nothing had marked it
as the impoverished half of the pair, so thirteen tasks were filed with it and their files
backfilled by hand; a bare `add` now points at `park` once, while the difference is still
cheap to fix.

Two carry paths were fixed along the way. A state transition dropped the new relations —
every field ever added to this model has been dropped by a move at least once — and the
reclaim path rewrote a crashed session's task from its title alone, discarding its files,
its finish condition and its order at the moment the next session has least context.

### The board is now binding, not advisory

Two gaps, both about the board's central claim — that it says what is in flight.

**Work that changed files while nothing on the board says so is refused at the finish.**
`pre-tool`, `evidence-gate` and `session-start` did not call `plan` even once: a session
could rewrite the importer for three hours and appear, to every sibling, to be doing
nothing at all. The Stop gate now demands the work be on the board, alongside scope drift
and the evidence demand — satisfied once per session with the command the refusal names,
then never seen again. `require_task` in config turns it off.

**A task nobody is working on goes back to the queue.** `reap` covered the session that
DIED; nothing covered the commoner case — a live chat that claimed 0007, moved on, and
left it reading `doing` on every board for the rest of the week. `plan.sweep_idle` runs at
SessionStart before the board is rendered: back to `next`, carrying a line saying what
happened, after `task_idle_hours` (default 24) untouched. Not to `paused`, because paused
means waiting on something nameable and this is waiting on nobody. A session still editing
the task's own files keeps it however long it takes — reclaiming work mid-change is worse
than the stale row it was meant to fix.

The demand exposed a defect in the command it names. `claude-bp-plan claim` stamped
`cli-<branch>` as the owner, so a task claimed from inside a session belonged to somebody
the registry had never heard of: the board said "claimed by cli-main, which has no record
— reclaimable" for work a live chat was doing, and the new demand could not have been
cleared by its own instruction. Identity is `(harness id, worktree)` in the CLI too now,
composed by one shared function instead of two that disagreed.

### shellcmd.segments

`commands()` strips `FOO=bar` and `timeout 30` to find the program a gate should judge.
That is right for a refusal and backwards for a vouch: it hands back the approvable half
and discards the half that runs. `segments()` returns the line as written, and the vouch
declines whatever it cannot account for.

## v1.11.0

Three refusals that were the plugin arguing with itself, and one prompt it caused.

### A lease survived the worktree it was taken in (#100)

A branch merged, its worktree removed, the same chat continuing from a fresh tree — and
the files it had just edited were locked against it for the rest of the TTL, by a message
saying another session was editing them right now. The tree those files lived in no longer
existed.

`reap` already releases the leases of a session whose worktree git no longer lists. It runs
at SessionStart, and **entering a worktree fires no SessionStart**: identity is (harness id,
worktree), so one chat moving to a fresh tree becomes a new session with no lifecycle event
in between. Nothing reaped the record it left behind.

The lease table now asks the registry the same question every other reader asks — `is_live`,
which weighs pid, heartbeat AND worktree — instead of reading a pid on its own. A holder
with no record at all still falls back to the pid and the TTL, because a lease whose session
was deleted and not yet re-adopted is not a path to hand over.

### `adopt --ignore` was contradicted by the next command (#98)

`--ignore` said a document would not be raised again; `--check` raised it in the next breath
with a non-zero exit, and every sibling worktree went on counting it. The record was being
written the whole time — `--check` had simply never read it, and the decision lived in one
checkout's Tier A in a product whose premise is three to eight of them.

- `--check` honours the decision, and names the checkout that actually holds it.
- The decision is read across sibling worktrees, the same way `plan.load_all` reads tasks.
- `--ignore` on a path that is not there is refused instead of answered with the same
  sentence a real registry gets.

### The plugin stopped interrogating its own instructions (#99)

Measured on a real machine over three days: 35 classifier prompts, three of them worth
asking about. `EnterWorktree` was already vouched for; the CLI spelling of the same move
was not, nor was the suite the evidence gate refuses a finish without.

`pre-tool` now returns `allow` — **after every one of its own gates has spoken, never
before** — for `git worktree add/remove/list`, for the detected test command exactly as
detected, and for writes inside a tree this plugin provisioned for this session. One
command it cannot name takes the whole line with it, an unparseable line vouches for
nothing, and production stays where it was: ssh, force pushes and store submissions reach
the permission layer untouched.

### `git worktree list` was refused as a command that destroys work

`worktree` was in the tree-verb set whole, for the sake of `remove`. Asking where the other
sessions are, from anywhere but the tree you were standing in, came back refused for
discarding uncommitted work — citing reset, clean and stash, none of which it was.

## v1.10.0

A task now says whether a chat is on it, what would finish it, and what is stopping it.

### Is anyone working on this right now — derived, not stored

The board printed the owner's session id and nothing about it, so a task a chat is editing
this minute and one abandoned by a crashed session three days ago read identically.

Activity is **derived from the session registry on every read**, never written to the task
file. A stored "in progress" flag is set by a session that then crashes and stays true
forever — which is precisely the case the reader needs it for. Three answers now:

```
IN FLIGHT:
  - 0007 rework the importer [active in 65a7313b, seen 40s ago]
  - 0009 the kuper backfill  [held by 1f2c9a04, which is gone — reclaimable]
```

There is a test asserting the word never reaches the file, because a file that can carry it
can carry it wrongly.

### Paused is its own state, and must say what would lift it

`next` means pick me up. The same task waiting on a schema decision, a credential or
somebody else's merge says the opposite, and conflating them sends session after session at
work that cannot move.

```
claude-bp-plan pause 0007 --blocker "waiting on the schema decision in #41"
claude-bp-plan resume 0007
```

The blocker is required, on the same grounds a handoff is: a pause nobody can lift is a
task that has quietly left the ledger.

### A task can learn things while it waits

`claude-bp-plan update <id> --note … --paths … --done-when …`. Before this the only ways to
record what a task had learned were to park a second one, which splits the identity, or to
edit the file by hand, which the worktree gate refuses from the main checkout.

### Done when

`done_when` is the missing half of decision 0002 at task level: without it a model closes a
task on its own judgement, which is the assertion this project refuses everywhere else. It
leads the handoff view, above the prose, and survives every transition — a move that forgot
the files and the finish condition would hand the next session exactly the thin task the
ledger exists to prevent.


## v1.9.1

### Progress is not a repeat (#95)

Five sequential edits to five different regions of one module are ordinary work, and they
shared a signature — so the fifth was refused as "run 4 times in a row with nothing in
between". The detector wanted to tell a retry from progress and was not drawing that line:
it keyed on tool and path and left out the ANCHOR, which is the part that says *which
region* an edit names. A retry repeats its `old_string`; a distinct edit does not.

A Bash command is hashed whole now. Truncating at 160 characters made four heredocs writing
four different files identical, because the boilerplate that opens a heredoc is — so
probing for the cause of one block was itself blocked.

The replacement text stays out, deliberately, which is what the truncation was for: a retry
with a one-character change must not read as progress.

### A refusal that cannot be located has no exit (#95)

A write was refused for "what looks like a credential". The founder deleted the two lines
they suspected, the write was refused again, and nothing told them what had actually
matched — so the file could not be written by any route, and the only way out was to stop
using the gate.

Their diagnosis was wrong, which is the point: `SALT_EXTREME = 15.0` and
`skip_special_tokens=True` do not fire, and never did. Nothing said so.

The refusal now names the line and a **scrubbed** excerpt. Scrubbed because printing the
matched value to prove a value was matched would put it in the transcript, which is the
thing being prevented.



## v1.9.0

### The worktree prompt is the tool's own, and a hook cannot close it (#91)

The plugin tells the agent that entering a worktree needs no permission. The agent obeys.
Claude Code then asks the founder anyway, one layer below the instruction.

The obvious fix does not work, and it is worth recording why rather than shipping it and
finding out. `EnterWorktree.checkPermissions` returns `allow` for no path and for a
Claude-managed worktree under `.claude/worktrees/`, and for everything else returns:

```
{behavior: "ask", decisionReason: {type: "safetyCheck", reason: "permission-root relocation…"}}
```

A safety-check `ask` **overrides a hook's allow** by design — the hook approves, the
pipeline runs anyway, the founder is asked. The plugin's worktrees are siblings of the
repository, so they are never "managed" and always take that branch.

So the plugin says the line that actually ends it, and **stops saying it once it has been
acted on**: it reads `permissions.allow` in the founder's own settings and goes quiet when
`EnterWorktree` is there. Advice that keeps appearing after it has been followed is advice
the founder learns to scroll past.

Not done: moving provisioned worktrees under `.claude/worktrees/` to get the managed
`allow`. It removes the prompt outright, and it also puts a worktree inside the working
tree, which this plugin has a test forbidding — `?? .claude/` in every `git status`. That
is a founder-visible trade, not a bug fix, and it is theirs to make.

### Attempts can be retired (#92)

`claude-bp-attempt drop <id>`. Every other layer here could already be retired — decisions,
curated documents, review findings, worktrees. Attempts were the one write-only ledger, and
in the reporting repository both entries described failures that never happened: filed by
the scope-drift defect closed in #71, then carried into ALREADY TRIED on every session
start, warning about things nobody had failed at.

Done in-process rather than through a tool call, which sidesteps the second half of the
report: the files are untracked and live in the main checkout, where the worktree gate
refused the deletion and offered a tree that could not hold them.

### A file that exists and is untracked has nowhere else to go either

#68 established that a path git IGNORES has no second tree and no merge that could carry a
change to it. The same dead end is reached from the untracked side: the file is here, no
commit holds it, so nothing can carry its deletion across.

A path that does not exist yet is still refused, and deliberately — creating a file is
carryable in the ordinary way, and getting this loose would open every cross-tree write.


## v1.8.3

Issue #89. A session was refused access to its own former worktree — by itself, with the
refusal telling it to go ask the owner, who was the reader.

### Why two records exist at all

Session identity is `(harness id, worktree)`, and deliberately so: four concurrent
`claude -p` children were once found sharing one `CLAUDE_CODE_SESSION_ID` and collapsing
into a single incoherent record. The tree is part of the identity because it has to be.

The consequence nobody had followed through is that **one chat working in two trees leaves
two live records**, both naming the same running process. Every rule asking "is somebody
standing in that tree" then answered yes about the session doing the asking, and occupancy
could never be released by moving: a session that used a scratch worktree once held it
against itself for the rest of its life.

### The identity that unites them is the process

Not the id, which is per tree by design, and not anything looser, which would hide the real
sibling the per-tree identity was introduced to surface. The guard now skips records
carrying the asking session's own pid, read from its own registry entry rather than
re-resolved — the two must be the same number, and reading it is what makes the rule
testable at all.

Only a pid resolved to the CLI itself counts. An unresolved one is some ancestor that real
siblings genuinely share.

### On the report's own suggestion

It proposed passing `exclude=<session id>`, which is where I started. It does not close
this: the two records hold *different* ids by construction, so excluding one leaves the
other. Worth recording because the diagnosis was right and the remedy was one level off —
the same shape as the cascade the last four releases were.


## v1.8.2

Issue #87. v1.8.0 added green-run recording to **two of the pre-push hook's three tiers**,
and missed the one that actually fires for most projects.

The template has two literal tiers; the middle one is generated at install time, and that
is the tier a project reaches when it has a detected runner and no `check:` target. It
still ended in `exec`, which replaces the shell — harmless while passing or failing was the
hook's only job, and silently fatal the moment it had a second one. A push ran 2299 tests
and the merge gate still said no run had been observed.

The tier runs its command and records, like the other two.

### The coverage was the real defect

The test written for v1.8.0 exercised the `check:` tier — the one that already worked. It
was green and it proved nothing about the path the reporter was on, which their own issue
had described exactly.

There is now **one test per tier** rather than one per fix, enumerated so a tier cannot be
added without one, and it was verified by reverting the fix and watching it fail. A test
that has not been seen to fail is a test that has not been seen.


## v1.8.1

Issue #85, reported against v1.8.0 within the hour and correct: **the fix that just
shipped could not reach the repositories that needed it.**

`claude-bp-ci local` calls `install()`, which short-circuited on the hook merely EXISTING.
`ensure()` has rewritten a stale hook since #33 — "the hook now carries the version that
wrote it and is rewritten in place when that is older, only ever over our own file" — and
`install()` predates that. So the one command whose entire purpose is running the checks
locally was the one that declined to update the checks, and the only upgrade path left was
a session restart.

That matters for what a stale hook silently does in between. Across `1.0.x` the body
changed several times, including a release where a project *with* a suite pushed with
nothing run. A founder who runs this after an upgrade reasonably believes they now have the
shipped gate; they had whatever their last session start wrote.

`install()` now refreshes a hook an older plugin wrote, and says which versions:
`pre-push hook updated 1.6.0 -> 1.8.1` rather than `already installed`. It is not routed
through `ensure()`, which honours the opt-out — asking for the hook back is consent, and
`install()` clears the decline for exactly that reason.

Unchanged, and tested so it stays that way: a hook this plugin did not write is still
displaced and chained, never overwritten.


## v1.8.0

Issue #83, and the sharpest shape yet: **the gate asked for evidence the only session able
to merge was structurally unable to produce.**

v1.7.0 made the merge gate judge the pull request's head instead of the session's tree, and
four blockers collapsed to one. That one was `no test run has ever been observed on <head>`
— and it could not be cleared. `record_green` was reachable only from the Stop gate, which
writes for the branch of the tree the *session* occupies. The session that merges stands in
the main checkout, by this plugin's own design, because a merge is not a write to a working
tree.

So a suite run inside the branch's own worktree counted for nothing. And so did the run
**this plugin performs itself** on every push: the pre-push hook ran `make test` in that
worktree, saw it pass, let the push through, and recorded nothing. Two thousand two hundred
and ninety-nine passing tests, observed twice in the right tree and once by the gate's own
hook, all discarded.

### What changed

The pre-push hook records the run it just watched pass. That is evidence by the same
standard the Stop gate uses (decision 0004): the plugin ran the project's declared command
and saw the exit code. It is not a new mechanism, a new trust assumption, or a new cost —
the run was already happening on every push, and only the bookkeeping was missing.

The recorder is baked with absolute paths, because a git hook runs with a stripped
environment and cannot rely on `claude-bp-ci` being on PATH — the same reason the test
command is baked rather than resolved. Its failure is swallowed: a push whose checks passed
must never be refused because the bookkeeping did not land. And it sits *after* the exit
code check, so a red run records nothing, which is the half worth a test of its own.


## v1.7.0

Three reports, and the theme is **a rule that only exists at the moment of refusal**.

### The worktree rule is now standing context (#81)

`EnterWorktree` refuses to act on its own judgement — its own description says to use it
ONLY when instructed by the user or by project instructions in CLAUDE.md or memory, and
never when "worktree" is absent from them. This plugin's requirement lived exclusively in
the pre-tool refusal, which arrives *after* a write is blocked and does not persist.

So the agent read "do not ask, just move" at the moment of failure and began the next task
with no standing instruction: asking the founder again, or editing the main checkout and
being refused again. **Forty-two refusals across four transcripts of one day**, each
provisioning a branch nobody asked for.

The rule is now published at SessionStart, naming `EnterWorktree` and the path, because
that tool's own gate is what has to be satisfied. Empty for a session already in a worktree
and for a repository that has switched the rule off — which together are the steady state.

**One tree per session, whatever the task says now.** The name was derived from the task
statement, and the task statement is re-captured on every substantive message, so each new
instruction bought another tree and another branch named after a slug of it.

**And entering a tree that does not exist yet is approved.** Claude Code creates its own
worktrees under `.claude/worktrees`; requiring the path to be an existing working tree left
the creating call unapproved, so the founder was asked to authorise the move this gate had
just ordered.

### The ledger recorded the session's branch, not the pull request's (#79)

The one session that coordinates — reading pull requests, merging, releasing — sits in the
main checkout, which is what the worktree rule leaves it doing, because a merge is not a
write to a working tree. Every pull request it opened was filed as being ON the base
branch. "No commits on top of main" is then not strict but *unsatisfiable*: a branch cannot
gain commits over itself.

The head is now taken from where it actually is — the structured tool's `head`, `gh`'s
`--head`, or the branch of the tree a `cd` moves to. And a record whose head equals its base
is treated as misfiled rather than as a reason to refuse forever.

This was the root cause under #74, which v1.6.0 fixed one level too high.

### Findings expire when their rule does (#80)

Fixing a detector did not clear what the broken detector filed. The `sql-interpolation`
corrected in v1.6.0 was still counted ten sightings later, over code the current rule reads
as clean, and it kept the merge gate refusing.

A finding is a claim about code as it stands, so it is now re-asked before it is counted —
one regex over one file — and retires itself when its rule no longer fires. Conservative in
one direction only: an unknown detector, an unreadable file, or one path where the rule
still fires all keep the finding.

The review rules moved into a module to make that possible, which is also what lets them be
tested directly rather than through the hook.

**`url-credentials` no longer fires on documentation.** A comment naming the shape of a
connection string — the words for user and password, not values — was reported as a secret,
while the line under it read the value from the environment, which is the practice the rule
exists to encourage. Placeholder words are recognised as placeholders, in English and in
Russian, because a comment is written in the language of whoever wrote it.

### Found while looking, not reported

The plugin watched `~/.claude/CLAUDE.md` and every rival tool's instruction file, and not
**the project's own CLAUDE.md** — the one file here that is always legitimate and most
likely to grow. It is now watched too, with a threshold that means something and a remedy
of trimming rather than excluding, because it is not a competitor.


## v1.6.0

Four reports, **one shape: the gate judged a proxy instead of the thing.** Where the
session stands instead of who a write could hurt (#68); the tree it occupies instead of
the pull request being merged (#74); the shape of a string instead of whether it is unsafe
(#75); a substring of the command line instead of the command (#76).

Each was also a closed loop — the refusal blocked the one action that would have resolved
it. That is the property worth naming, because it is what turns a false positive from an
annoyance into a dead end.

### Reading is not doing (#76)

`echo` of the merge invocation was refused as a merge. So was `grep` for it in
documentation, and a script whose JSON payload contained it — which is how the tool for
investigating this gate became blocked by this gate.

Gates now decide on the parsed command: the program being run and its subcommand, never a
substring of the line. Quoted text is an argument, not an action. A line the tokeniser
cannot read falls back to the old match, so a command crafted to break parsing does not
become one that walks past.

**The same shape one gate over, unreported:** `PRODUCTION_DEPLOY` matched `--prod` anywhere
in the line, so `echo "deploy with --production"` was a production deploy. Found by looking
for the shape rather than by waiting for the report.

### A path git ignores has nowhere else to go (#68, reopened)

The first fix reached only the cross-tree rule. A session standing IN the main checkout met
the `require_worktree` rule instead — same dead end, same unreachable remedy, and the config
file that would switch the guard off is itself in the main checkout, so neither exit could
be taken from a session. Three worktrees were provisioned as side effects of the refusals,
and the retired production SSH key it was trying to delete stayed on disk.

Ignored paths are now exempt from the worktree and trunk rules too, and the refusal no
longer provisions a tree nobody can use.

### A merge is not a write to a working tree (#74)

A session in a main checkout could never merge. Every reason named the wrong subject: "no
commits on top of main" measured on a checkout not supposed to carry any, an UNVERIFIED
finish belonging to another session's task hours earlier, findings in files the pull
request never touched.

The merge gate now resolves against the pull request's head — commits over its base, its
own green run, its own unverified finishes, findings intersected with its own diff. The two
checks that are genuinely about a working tree rather than a branch are not asked of a tree
the pull request has nothing to do with.

### A reviewer that flags the safe form teaches you to ignore it (#75)

`sql-interpolation` fired on a psycopg placeholder with its parameter binding — the form
people are told to use *instead of* interpolation. Interpolation now means what it says: an
f-string with a placeholder, or a literal handed to `%` or `+`. A literal followed by a
comma is a parameter binding.

`url-credentials` fired on a development default pointing at localhost, with nothing to
leak and nothing to rotate — and refused the bug report about itself, twice, the second
time with every component replaced by a placeholder. A local host plus a user equal to its
password is now recognised as the development default it is.

**And a finding can be ruled out.** `claude-bp dismiss <detector> <path>` records the
judgement in Tier A, committed, keyed on detector and path rather than on an item id that a
new review replaces. Before this the only exits from a false positive were rewriting
correct code or switching the gate off.


## v1.5.1

Issue #71, and the same root as the two before it: **measured from the wrong point.**

`git pull --ff-only` moved a local trunk past eighteen commits other sessions had already
merged, and the stop gate reported all 41 of their files as this session's scope drift.
The session had touched eight files, none of them on the list.

The advice is what makes it serious. "Revert what is out of scope", followed literally
here, means rewinding other people's merged work — a gate whose remedy is destructive on
its own false positive is worse than no gate. And any session that syncs with main before
finishing trips it, which this project's own workflow requires.

Scope is now measured from the merge base with the **remote** trunk when upstream has
moved past the session's baseline. Work arrives from other sessions by being pushed and
pulled, so `origin/<trunk>` is what "somebody else's, already merged" means.

The remote half is load-bearing and was found by the suite, not by reading. Measuring
against the LOCAL trunk erases the session's own work — `merge-base(main, HEAD)` is HEAD
for a session working on main, so the diff came back empty and every gate reading it
stopped firing. The escalation-ceiling tests went from 2 blocks to 0 and said so.


## v1.5.0

Three reports, one shape: **a gate refusing on a question next to the one worth asking.**

### The guard asked "is this tree mine" (#67, #68)

It should ask "would anyone lose work". A worktree made by hand — which is what this
project's own convention tells people to do — is never in the provisioned registry, so it
was a stranger forever. The session owning a branch could not run a git command in that
branch's tree, ran the suite in a throwaway clone instead, and was then refused the merge
for having no observed run: each gate's exit blocked by the other, no legitimate move left.

Sibling worktrees are now refused only while a live session is standing in them. The data
was already loaded for the board, and the sweep already reasons this way when it removes
unused trees — a tree safe to delete is a tree safe to write in.

The main checkout stays guarded whoever is or is not in it. Under this gate nobody is
*supposed* to be there, so occupancy would exempt it permanently, and its tracked files
belong to every branch rather than to whoever happens to be standing there.

**A path git cannot carry has no other tree.** "Make the change in your own tree and merge
it" is not a remedy for a file git ignores: it does not exist in the other tree and no
commit will move it. Both exits led back to each other, and a session that had just
rotated a production SSH key could not delete the retired one. Ignored paths are now let
through — *ignored*, not merely untracked, because a new file git would happily track is
carryable the ordinary way and getting that distinction wrong in the loose direction would
have opened every write into another session's tree.

### The merge gate counted findings from files the pull request never touched (#69)

The workflow *requires* `git merge origin/main` before merging, and that import carried
every open finding in main onto the branch. A pull request of eight markdown files was
refused over SQL interpolation in a Python module it never touched — and the longer main
got, the more a branch inherited, so syncing with main could never go green.

Findings are now matched against the pull request's own diff from the merge base. When git
cannot answer, every finding is kept: losing a real one is worse than repeating a stale one.

### "No test run has ever been observed on this branch" — while it had (#69)

The green record sat in the worktree's own Tier A, so a run observed in the branch's tree
was invisible from anywhere else in the same clone. It also carried no branch test at all,
so a run on one branch answered for every other — the same defect `_unverified_here` was
fixed for, still open here in the permissive direction.

It moves to the git common dir, one file per branch: shared by every worktree of the clone,
and still dying with the clone, which is what keeps it evidence rather than an assertion
that could be committed and pulled onto a machine where nothing ran. The old location is
still read for one release, with its branch checked.


## v1.4.0

Issue #66, and the worst kind of failure this project can have: **every signal said the
work was safe while it was one command from gone.**

Thirty tasks were migrated into the ledger. `park` printed thirty ids, `list` showed all
thirty, `adopt --check` reported `0 left`. An ignore rule covering
`.claude/claude-bestpractice/` meant git could see none of them — so the ledger lived
inside one working tree, and `git worktree remove` would have taken it.

Nothing was looking, because every command asks the filesystem and the filesystem was
fine. Only git disagreed.

### What the plugin now does

`git check-ignore` against Tier A, and say so in the three places it matters: on the board
every session, on stderr at the moment `park` writes, and as a doctor check that plants the
rule and proves the report fires.

`park` no longer prints "pick it up in another session" when that is false. The file is
still written and the exit code is still zero — a caller that read failure would park the
same task twice — but the promise is withdrawn and replaced with what actually happened.

### What it does not do

It does not edit the ignore rule. The plugin has never written one, in any released
version — searched across the whole history — so the rule belongs to the founder or to
another tool, and rewriting somebody's ignore file on the strength of a guess about why it
is there is not a repair.

### The probe is a path that cannot exist

`git check-ignore` reports nothing for a path already in the index, because a tracked file
is not subject to exclude rules. Probing the directory, or any real task file, therefore
answers "visible" as soon as one file inside has been committed — a false all-clear in
precisely the case that matters most, a repository that was healthy once and has been
hidden since. Checked against git rather than reasoned about, and the test that pins it
would have failed on the obvious implementation.

### The migration line names a command (#65)

`adopt` on its own was a count repeated every session with nothing that starts anything —
one repository carried the same 66 items indefinitely with every gate green. The line now
names the next action the way the worktree refusal names the destination, and names the
way out as well:

```
31 open item(s) in 2 checkbox document(s) tracked outside the work ledger —
`claude-bp-plan adopt --brief docs/pre-release-todo.md` to migrate,
`adopt --ignore <paths>` if a document is curated and stays put
```

Both exits, deliberately. A signal a repository can never discharge is one it learns to
scroll past, which costs the signals that matter.


## v1.3.3

Issue #63. `adopt` raised `.github/pull_request_template.md` as "3 open item(s) not in the
ledger". Its checkboxes are a **form**, ticked in the pull request body on GitHub and never
in the file — so the count can never fall, no migration can change it, and it surfaces on
every run forever.

GitHub's template paths are now skipped the way `.claude/` already is: every location it
reads a template from, in the spellings it accepts.

Worth saying plainly, because this feature has retracted two conventions already: these
paths are **GitHub's own and documented**, not a guess about how somebody might name a
file. Everything else under `.github/` stays in scope — a release checklist there is real
work.

### `--ignore` takes several at once

A repository that has kept its registries by hand has more than one of them, and five
invocations to say one thing is a tax on the decision rather than a record of it:

```
claude-bp-plan adopt --ignore docs/pre-release-todo.md,docs/store-listing.md
```


## v1.3.2

Issue #61, and the sharpest catch of the series: v1.3.1 added the "checkbox lists are the
only shape I recognise" disclaimer **only on the path where nothing was found** — which is
the path where it matters least.

A repository with no checkbox document at all is one where nobody is mid-task; the reader
is unlikely to act on the message. The **mixed** repository is where it is believed,
because a one-item list reads as a result rather than as an absence:

```
work tracked outside the ledger:

  docs/pre-release-todo.md  —  25 open item(s) not in the ledger
```

Not a word that a second registry sits beside it, unreadable. That is the exact reading
that produced the field report v1.3.1 had to correct — the reporter saw a one-item list,
saw their primary registry absent, and concluded the omission was deliberate. The fix
printed the honest sentence in the one place that reading could not happen.

The caveat is now one shared constant printed on **both** paths, so they cannot drift.

### The report said this was the last such place. It was not.

`migrate.line` — the line injected into **every** session, and therefore read far more
often than the command — had the same defect: `28 open item(s) in 2 document(s) tracked
outside the work ledger` presents as complete. It now names its own scope in the words it
was already spending, rather than paying for a second sentence against a 400-token budget:

```
28 open item(s) in 2 checkbox document(s) tracked outside the work ledger —
`claude-bp-plan adopt` for what to do about it, including what it cannot see
```

Two tests of my own were pinned to the exact wording and broke on this edit. They now
assert against the constant, because a test that breaks on a rephrasing teaches nothing
when it does.


## v1.3.1

v1.3.0 replaced one invented convention with another, and the field report that confirmed
it working contains the proof — offered generously, as if it were correct behaviour:

> «А наш `docs/TODO.md` не поднят, и это правильно: в нём ноль чекбоксов — он устроен как
> реестр с ID и статусами, а не как список галочек.»

It is not correct. That document is the repository's **primary** registry, created
deliberately so decided-but-unbuilt work stops evaporating. It tracks real work and the
plugin cannot see it — because v1.2.0 quietly expected a filename, and v1.3.0 quietly
expected a checkbox. Same mistake, one layer down.

### The part that actually had to be fixed

Missing a document is forgivable. **Announcing that nothing was missed is not**:

```
$ claude-bp-plan adopt
nothing tracked outside the ledger        ← with two planned items in docs/TODO.md
```

That is a claim about the repository, and it was false. It is the quiet failure this whole
project is written against — a dead system looking exactly like a quiet one — committed by
the thing that warns about it. It now names what it looked for and admits the rest is
invisible:

```
no documents found tracking work in checkbox lists (`- [ ] …`).
That is the only shape this recognises — a registry in any other form is
invisible to it. Point it at one directly:
  claude-bp-plan adopt --brief <file>
```

The escape hatch did not work either. `--brief` on such a document printed "tracks 0 open
item(s)", no items, and then "For each item NOT yet in the ledger…" — instructions for an
empty list. It now says it cannot enumerate the format, reports the one thing it does know
(how many tasks name the document as their source), and hands the reading to the session.

`--check` no longer reports "0 left" for a format it cannot read. A count nobody can stand
behind is a green light nobody earned.

### What was deliberately not done

A third detection shape was not added. Guessing at markdown tables, or at status
vocabulary like `planned` / `todo`, would be the same mistake a third time — and #51 is
already the record of what vocabulary guessing costs. Detection stays best-effort and says
so; the founder points at anything it missed.


## v1.3.0

v1.2.0 looked for work outside the ledger by matching filenames, and a field report showed
that missing an entire real setup:

| the document | why it was missed |
|---|---|
| `docs/TODO.md` — a deliberate registry, PR #460 | bare `TODO.md` excluded on purpose |
| `docs/pre-release-todo.md` — 26 checkboxes | hyphen in the wrong place |
| `.claude/commands/todo.md` | whole directory skipped |

Three documents tracking real work, none matching the `TODO-<name>.md` shape the plugin
was quietly expecting. **Nobody had agreed to that convention.**

### Found by what is inside, not by what it is called

A registry does not announce itself in its filename, but it does in its contents: a list
of checkboxes. Every markdown style counts — `-`, `*`, `+`, `1.`, `2)` — finished items do
not, and two items is the floor, because prose containing one stray checkbox is prose.

### Delegated to the agent, and then counted

Prose cannot be turned into a handoff by a regular expression, and the plugin does not run
a model. A session can read the document; the plugin cannot. So it hands the job over:

```
$ claude-bp-plan adopt --brief docs/TODO.md
docs/TODO.md tracks 3 open item(s); 0 of them are in the ledger.

  - перемерить лимит MegaMarket
  - переписать скоринг словаря
  - выкатить OTA-политику

For each item NOT yet in the ledger, read enough of the repository to fill in a
real handoff, then run:

  claude-bp-plan park "<title>" --paths <files> --note "<...>" --source docs/TODO.md

`park` refuses a title with no files and no substance, so a thin one will not land.
Run `claude-bp-plan adopt --check docs/TODO.md` when you are done: it counts what is
left rather than taking your word for it.
```

**That last line is what makes this delegation rather than persuasion**, which the product
constraints forbid. The plugin does not read the prose or judge the result — it counts
what the document tracks, counts what the ledger holds *from that document*, and the gap
is a number. `--check` exits non-zero while anything is left:

```
$ claude-bp-plan adopt --check docs/TODO.md
docs/TODO.md: 3 open item(s), 1 in the ledger, 2 left        # exit 1
```

An agent that says it migrated a registry and left twenty items behind is contradicted by
arithmetic, not by opinion. That is decision 0002 — evidence, never assertion — applied to
migration.

### A curated registry can be left alone for good

`claude-bp-plan adopt --ignore docs/TODO.md` records the decision in Tier A, and the file
stops being raised. A warning nothing can clear is one the founder learns to scroll past,
which costs the warnings that matter.

Nothing is moved on its own. Scratch notes the plugin's own absence caused are still
imported mechanically; documents are named, counted, and left where they are.

### Fixed on the way

The board line dropped scratch TODO files entirely once it started counting checkbox
items — they are prose and have none. Caught by its own test. It now reports both, and
counts **items** rather than files: "2 documents" says nothing about what is at stake,
"28 open items in 2 documents" decides whether it is worth a turn.


## v1.2.0

Parking a task for a session that has not happened yet, and taking over the workaround
that existed because the ledger could not.

### A TODO is a handoff, and a handoff has a bar

The scene: a chat with more work in it than belongs in one chat, and the founder saying
"leave that for another session". Until now the honest answer was a markdown file somebody
invented on the spot — `docs/scoring/TODO-dictionary-realign.md` — which is a **second task
system** in a repository that already had one, with nothing keeping the two honest.

The work ledger already had the right bones: one file per task, lifecycle in the directory,
claims released when a session dies. It was missing the three things a handoff needs.

`claude-bp-plan park` refuses anything that is not one:

```
$ claude-bp-plan park "Пересобрать словарь" --note "потом доделать"
not a handoff somebody else could pick up:
  - no files named — the next session has nowhere to start
  - the note is under 80 characters — say what is already known, what was ruled out,
    and where it stands
```

A parked task is read by a session that was not in the room. It has the title and nothing
else, so a thin one costs its reader the entire rediscovery the parking session was trying
to save. Refusing is the same trade the evidence gate makes: a moment now against an hour
later.

`claude-bp-plan show 0007` hands the next session everything in one read — the files, the
reasoning, what was already ruled out, the branch it was parked from.

**Deliberately not on the board.** The board is injected into every session and pays for
itself each time; a full handoff is wanted by exactly one session, the one picking it up.
Putting it in front of the other seven is how a context budget dies. The board carries the
title; the detail is one command away.

### An upgrade repairs what it finds, and takes over what it caused

New in `migrate.py`, and the third of these is the one nobody does:

- **Old state keeps loading.** A field added today is absent in everything written before
  it, and absent has to read as a default rather than a parse failure.
- **Broken state is repaired rather than stepped around.** A half-written JSON file under
  `.claude/` survives every upgrade — each reader catches its own decode error and carries
  on with a default, so nothing breaks loudly and nothing is ever fixed. It is now set
  aside as `.broken`, with the original kept, because deleting a founder's file to fix a
  parse error is not a trade this plugin gets to make.
- **A workaround the plugin caused is taken over once the plugin grows the feature.**
  Hand-written `TODO-*.md` files are found, surfaced on the board, and adopted by
  `claude-bp-plan adopt`: the text becomes a task, the files the note *actually* mentions
  become its `paths` (only the ones that resolve — prose is full of things that look like
  filenames), and the original is rewritten to a pointer at the task. Not deleted: every
  link to it still resolves and git keeps the text.

A bare `TODO.md` is left alone. That is usually a document a project maintains on purpose,
and adopting it would be taking over something that was never a workaround — the hyphen is
the whole distinction.

Repairs run themselves on session start. **Adoption does not.** It rewrites files in the
founder's repository, and a plugin that edits `docs/` on its own initiative during an
upgrade is one nobody installs twice.

### Two defects found by running it

`adopt` was not idempotent: a second run adopted its own pointer, filed a task whose body
was the pointer text, and left a fresh pointer for the third — one task per invocation,
forever. Exactly the pollution class the last four releases have been about.

The migration ledger was written to the working tree, dirtying `git status` on every
session start. An existing test caught it, because a previous version of this plugin did
the same thing with the stage marker. It lives in the git common dir now, which is what
decision 0001 says about bookkeeping that describes a clone rather than the repository.


## v1.1.0

First minor bump, and the reason is the point: nineteen releases went out as `1.0.x`
including ones that added whole behaviours. Anyone reading the version to decide whether
an upgrade was safe was reading a number that could not tell them. `RELEASING.md` now
writes the rule down — a new capability is a minor bump, a fixed defect is a patch — and
this release adds a capability.

### The plugin reports its own defects, without spending a turn on them

Every failure fixed so far was found by someone hitting it and writing it up by hand. That
works while the person hitting it owns the repository. It stops the moment anyone else
installs it: they hit the same defect, work around it, and nothing here ever learns.

`hookio.guard` is the single handler every gate failure already passes through, so it is
the one place worth recording them. A crash is now written to disk with the gate, the
exception and the plugin frame it came from, deduplicated by all three, and **injected
nowhere**. The agent is mid-task; a defect in the tooling is not its problem to read
about. The only trace is one line in `claude-bp status`, on a surface the founder is
already looking at.

`claude-bp-report` shows exactly what would be sent; `claude-bp-report send` files it.

**It does not send on its own, and that is deliberate.** Filing an issue uses the
installer's own GitHub credentials and posts publicly under their name, in a repository
they do not own, carrying whatever the report holds. Nobody installing a plugin expects
that. So the default is `"report_defects": "local"` — capture and hold. `"auto"` files
automatically and exists for the case where consent is real: the owner running it on their
own machines. `"off"` does not capture at all.

Nothing from the repository leaves. Credentials are scrubbed with the same pass the
pre-write gate uses, and paths outside the plugin are erased **including their basename** —
an early version kept it and put `billing.py` into a report whose own last line promised
"nothing from the repository it ran in". The claim was made true rather than softened.

The network still never touches a hook, which is what the five-hour-limit audit rests on.
Sending lives in a CLI: the only place allowed to be slow and the only place allowed to
fail.

### A bad release can now say so

A released version cannot be withdrawn. The tag is permanent and `claude plugin` keeps
serving it, so v1.0.13 — which cannot run `make check` under a virtualenv, and therefore
cannot push — still looks fine from the outside. That is how somebody stays on it.

`upgrade.KNOWN_BAD` names those versions with one line of why, and every session running
one is told, on the board and in `claude-bp status`. It reaches the person on the bad
version, which release notes never do. A version belongs there when it **broke something
that worked**, not when a later release improved on it.

### Releasing is written down

`RELEASING.md`: what each bump means, how a release is cut, what to do when one turns out
to be bad, and why this repository kept needing `git merge -s ours` — squash-merging a
long-lived branch rewrites the commit, so the branch tip stops being an ancestor of `main`
and every subsequent push is rejected. The fix is a repository setting rather than a
technique, and it is named there.


## v1.0.19

Asked whether "remember this" is scalable — whether the plugin understands that a new
instruction may *replace* an old one rather than pile up next to it. It does not, and the
reason turned out to be worse than a missing feature.

### The retirement path existed and nothing could reach it

The design is already right, and stated in the module that owns it: a decision is a
historical fact, so it is never rewritten. It is retired by a **later record naming it in
`supersedes:`**, and `build_index` has honoured that field all along — a retired record
drops out of the index, which is what a session is handed.

Nothing in the plugin ever wrote that field. `render` did not emit it and `accept` had no
flag, so the only way to retire a decision was to hand-edit markdown nobody was going to
open. In practice records piled up and contradictory policies stayed live side by side in
every session's context — the exact pollution the design was built to prevent.

Now: `accept --supersedes 0001,0002` retires several at once, and an acceptance **without**
the flag prints the live decisions already covering the same files, at the moment the
founder is already deciding. Shown, not acted on: two records about the same files are
about the same thing, which is the only signal available without a model, but "about the
same thing" is not "contradicts", and silently retiring a decision the founder still wants
is worse than leaving one they have to read past. `validate` now reports a `supersedes:`
naming a decision that does not exist, or naming itself.

### Every decision claimed the whole source tree

Found on the way, and the larger half. `render` read `subject_paths` as a list of dicts;
`extract` stores plain strings. For every real draft the list came back **empty**, so the
record fell through to its default:

```
paths: src/**
```

`paths:` is what stops a decision loading in sessions it has nothing to do with. The
validator refuses a record without one *precisely* because "no scope" means "every
session" — and `src/**` is the same thing said differently, so it passed the check that
existed to catch it. Every accepted decision has been global since the layer shipped, and
collision detection could never have worked either, because nothing overlapped anything.

Both shapes are read now: plain strings from `extract`, stamped dicts from
`provenance.stamp`.


## v1.0.18

Two halves of one reported failure: the worktree rule refused the tree it had just handed
over, and then asked the founder for permission to do the thing it had just ordered.

### The gate refused its own worktree (#deadlock)

Reproduced end to end. A session in the main checkout writes a file:

1. **Refused** — "this is the main checkout, not a worktree", and a worktree is
   provisioned in the same breath.
2. Writing into **that** worktree — **refused**, as *"belongs to another session's
   worktree"*. It belonged to this session, created by this plugin, seconds earlier.

A closed loop with nowhere left to write, and the second refusal was not merely wrong but
wrongly explained, which is the failure mode this project is most prone to.

`provisioned_for` already guarded the git verbs against exactly this (issue #37). The
write path — which is what a founder actually hits — never got the same exemption. It has
it now, and a sibling session's tree is still refused, whoever provisioned it.

### Entering a worktree is no longer a question

The founder was shown a permission prompt asking whether Claude might enter a worktree —
seconds after a gate refused a write for not being in one. That is the plugin
interrupting the founder with its own instruction.

A plugin manifest carries commands, agents, skills, hooks and output styles, and **no
permission rules**, so the only way to pre-approve anything is a PreToolUse hook answering
`allow`. Two things were missing: `EnterWorktree` was not in the hook's matcher, so the
gate never saw the call at all, and there was no way to answer `allow` if it had.

Approved only for a working tree of this repository. Anywhere else the gate stays
**silent** rather than approving — silence leaves the founder's normal permission flow in
charge, and vouching for a directory this plugin knows nothing about is not its to do.
That distinction is asserted directly on the raw hook response, because the suite's own
helper reports silence *as* "allow" and could not have told the two apart.


## v1.0.17

The decision inbox only ever heard corrections, so the commonest kind of durable
instruction was invisible to it.

Every marker was correction-shaped — it fired on the moment a human overruled the agent.
But a founder stating a policy is correcting nothing. «запомни навсегда», «на будущее»,
«правило для всех чатов», "from now on", "as a rule" all scored **None**, and so did a
522-character message laying out release policy for three app stores. The subsystem whose
entire job is to stop durable instructions being forgotten was deaf to the exact sentence
that says *do not forget this*.

A `standing` marker now leads the table, because an explicit "remember this" is the least
ambiguous decision record there is — the founder has already done the hard half of writing
one. Both languages, as of v1.0.15, and a table test asserts each phrasing fires.

Precision is held by requiring the phrase, not the keyword. Description that happens to
contain the word is still ignored:

| | |
|---|---|
| «запомни навсегда: версии во всех трёх сторах одинаковые» | `standing` |
| «это всегда падает на проде под нагрузкой» | none |
| "from now on tag every release with the same number" | `standing` |
| "I do not remember whether we shipped that build" | none |
| «как правило это занимает минут двадцать» | none |

`always use X, never Y` now reads as `standing` rather than `rejection`. Markers are
ordered by strength and only the strongest is kept: a sentence that opens by saying what
to do forever is policy that names its alternative, not a rejection that happens to be
permanent.

Also fixed, from the same investigation:

- **The quote was cut at 400 characters with no mark.** A policy is prose. That 522-character
  message was stored as 400, ending mid-word, and the record put the fragment under
  `## Why` as the founder's own words — issue #41's defect, in a second file. The cap is
  now 1000 and a cut is marked with the same wording the prompt gate uses.
- **«или нет,» read as a correction.** It is the tail of a question — "ставили мы версию
  или нет" — and it filed a draft every time the founder wondered aloud.

## v1.0.16

The escalation ceiling counted blocks without looking at the clock, so it fired on an
interruption as readily as on a loop.

Four blocked Stops minutes apart is the thing the ceiling exists to stop: a session
retrying the same failure forever, burning turns. Four blocked Stops spread across an
afternoon are four separate attempts by a founder who went away in between — and the
commonest reason they went away is that Claude stopped answering. The five-hour usage
limit lands mid-turn, the session resumes hours later with the counter exactly where it
was, and one more block filed an UNVERIFIED finish plus a permanent `outcome: failed`
attempt against work whose only fault was being interrupted.

A block now records when it happened, and a gap of more than an hour starts the streak
over. A record written before this existed carries no timestamp and keeps counting, so an
in-flight streak is not reset by the upgrade itself.

Found by auditing what survives a session that stops mid-flight, not from a report.

### What that audit checked, and what it found

The plugin makes **no network calls and no model calls** — verified mechanically across
the shipped tree. Its gates are Python subprocesses over git and the filesystem, so a
usage limit, an API outage or an expired token cannot reach them. Everything else held:

- a hook `SIGKILL`ed while holding the state lock is reclaimed by the next hook in 0.1s,
  by asking the kernel whether the holder's pid still exists rather than waiting out a
  timeout;
- truncated `sessions/*.json`, `leases.json`, `open-items.jsonl` and `pull-requests.jsonl`
  are all survived — the gates run, they do not wedge;
- a session idle for five hours stays live and keeps its baseline, so the diff anchor
  does not move under it;
- a session whose window is closed is reaped, its baseline is remembered for a resume, and
  its file leases are handed back;
- work committed in the turn the limit interrupted is still demanded by the next Stop —
  the anchor is the baseline, not the turn.

The one remaining cost is named rather than fixed: a session interrupted while holding
file leases keeps them for up to their 30-minute TTL, so a sibling wanting those exact
paths waits that long. It clears itself with nobody acting.


## v1.0.15

Issues #50 and #51. Both are the same failure of imagination: code that was correct for
the machine and the language it was written on.

### `make check` could not run under a virtualenv (#50)

Self-inflicted, in v1.0.13, by the test written to prove the liveness fix. It copied
`sys.executable` to a file named `claude` so the process tree would contain a process the
walk could find. Under a virtualenv that interpreter cannot start: it locates its stdlib
through the `pyvenv.cfg` beside its own executable, and a copy has no way back.

```
AssertionError: 0 != 1 : Could not find platform independent libraries
```

Green for the author, red for anyone running the project the ordinary way — and the
pre-push hook runs `make check` on every push, so it blocked pushing too. Second failure
of this shape in this suite.

The fix is not a better way to relocate an interpreter. The shim is now a shebang script
that runs `sys.executable` **from its own path**, so there is nothing to relocate and the
whole class is gone; verified under both a symlinked and a `--copies` virtualenv, with the
child reporting the venv's own prefix. The assertion that the shim started is also
separated from the assertions about liveness — a broken fixture now says so instead of
sending the reader to the wrong file, which is what `0 != 1` did here.

### The decision inbox was English-only (#51)

v1.0.14 stopped the inbox filling with the plugin's own output. What that exposed is that
the classifier deciding what *is* a decision only ever spoke English, so for a founder
working in Russian the inbox went from reliably wrong to reliably **empty** — five markers
out of five silent on instructions whose English translations all classified correctly:

| | Russian | English |
|---|---|---|
| decision | «мы решили использовать Decimal вместо float» → none | `decision` |
| rejection | «никогда не используй float для денег» → none | `rejection` |
| correction | «нет, не так — бери timestamp из source_products» → none | `correction` |
| constraint | «КБЖУ должно быть всегда на 100 г» → none | `constraint` |
| rationale | «так нельзя, потому что сломается прод» → none | `constraint` |

Empty is the worse failure, because it reads exactly like a session that made no
decisions. There is nothing to notice.

Every marker now carries its vocabulary in both languages, side by side in one table, and
a test asserts each one fires on both — so the asymmetry cannot quietly return the next
time a marker is edited. Russian pleasantries («нет, спасибо», «не сейчас», «ладно,
забудь») join the noise filter, and both `е` and `ё` spellings are accepted.

Also fixed, from the same report: `rejection` required `never` to be followed by a verb
from a fixed list, so `"use Decimal here, never float"` — the most natural way to say it —
scored nothing in English either. The comma before `never` is what carries the rejection,
and it does not fire on "I have never seen this before".

## v1.0.14

A pull request is now an obligation rather than a notification.

The failure it closes: the session and the founder agree on a change, the session opens a
pull request — and then stops, waiting for an approval nobody asked it to wait for. The PR
sits. The session ends. Nothing in the repository remembers it, so the next session does
not pick it up either, and the work is finished in every sense except the one that counts.

An obligation is discharged in exactly one of two ways, and there is no third:

**Merged, by the session that opened it.** No approval step, because there is no reviewer
— that is the operating mode this whole plugin is built for. A session whose branch passes
the final check merges its own pull request, and a turn that tries to end with one still
open is interrupted once and told to.

**Handed to the founder, with the blockers named.** When the final check finds something,
the merge is REFUSED — not negotiated, not repaired. That half is deliberate and it is the
reason the first half is safe to have. A model asked to make a branch mergeable will make
it mergeable, and at merge time the available moves are weakening an assertion, widening a
tolerance, or reverting the change that surfaced the problem. All three satisfy the letter.
Which one is acceptable is the founder's decision, so the gate stops there and says so:

> refusing to merge #48 — the final check found 2 thing(s):
>   - the test suite is red
>   - 1 review finding(s): secret in src/config.py
> Tell the founder exactly this and stop. Do NOT merge, and do NOT push changes to make
> the check pass.

The final check is the same one `claude-bp-ship --pr` already ran before opening a pull
request — unfinished merge, no commits, red suite, never-verified branch, unverified
finish, uncommitted work — plus the review findings already on the board. All local, all
free: this runs inside a PreToolUse hook, and a gate that costs a network round trip on
every tool call is a gate that gets switched off.

Both spellings are watched. `gh pr create` and `gh pr merge` reach the same API as the
structured tools, and a gate that only sees the structured tool is one an agent walks past
on its first `Bash` call.

### Interrupted once, then carried

The Stop gate raises an open pull request exactly once, and the hand-off is written
*before* the block rather than after — so a session that ignores it, crashes, or hits the
escalation ceiling does not meet it again. One unignorable interruption is the whole
budget. Past that it lives on the board and in `claude-bp status`, which is what keeps it
from being forgotten across sessions rather than merely across turns.

Turn it all off with `{"manage_pull_requests": false}`.

### Fixed on the way: the gates' own state read as uncommitted work

`delivery.ready` called `git status --porcelain` and treated any output as "there are
uncommitted changes". `.claude/` holds the stage marker, the green ledger and the config,
all written by the gates themselves and all untracked in a repository that has never
committed them — so every session made its own tree read as dirty within seconds of
starting, and `claude-bp-ship --pr` reported that as a reason not to ship. The evidence
gate has exempted the same prefix all along.

## v1.0.13

Issues #43, #44, #45 and #47. All four are the same shape: state that accumulated in a
real repository over two days of parallel work, where each individual write looked
correct and the pile did not. Three sessions that could not see each other, an inbox of
96 decisions with no human in it, 70 open items with four distinct texts, and a session
whose recorded task was «Делай».

### Sessions never saw each other (#43)

The pid watched for liveness was `os.getppid()`, documented as "the Claude Code process
that owns the session". It is not. Claude Code spawns hooks through a shell that exits
with the hook, so the recorded pid was dead milliseconds later and **every session read
every other as dead**. In a repository with three active chats:

```
OTHER LIVE SESSIONS: none. This session is alone on the repository.
claude-bp status: SESSIONS — none live, 0 live claim(s), 5 stale
reaped.jsonl: 122 entries for 3 real sessions
```

The contended-file refusal — one of this plugin's headline behaviours — could not fire
between two real chats, because a lease is gated on its holder's pid and that pid was a
wrapper that had already exited.

It survived five releases behind a green suite and a passing doctor check for the exact
behaviour, because under test the hook's parent is the test runner, which stays alive for
the assertion. The rule and its proof disagreed only in the environment that ships.

Two changes. The owner is now found by walking the process tree to the CLI itself, and
the record says how its pid was obtained — a pid that was never resolved to the CLI is
not evidence of death, so nothing is reaped on it. The doctor gained the check that would
have caught this: spawn two sessions the way Claude Code does, through a shell that
exits, and assert they can still see each other and hold a lease against each other.
It fails on v1.0.12 with `live=[]`.

Upgrading clears the records the bug left behind — a pre-fix record is retired once it
has also stopped heart-beating, which a live session never does for long.

### The decision inbox filled with the plugin's own voice (#44)

96 drafts: 57 the gate's own refusals quoted back at itself, 39 the compaction preamble,
0 from a human. Claude Code writes hook feedback into the transcript as a `type: "user"`
record, and this plugin's refusals are full of the words the classifier looks for — "not
done yet", "must", a list of paths. So every blocked Stop filed the gate's message as a
founder decision, and the loop fed itself: the more the gate blocked, the more
"decisions" appeared.

Synthetic records are now dropped before classification. The rules are anchored at the
start of the record, where the harness puts its prefixes, so a founder talking *about* a
block is still heard.

### 70 open items, 4 distinct (#45)

One review finding was stored 34 times, another 31. The caller's item id carries a
timestamp, so identical findings could never collide by construction, and a
commit-triggered review filed a fresh row every time it ran. The board asserted each copy
separately to every session, each had to be retired separately when its subject moved,
and the four rows that said something new were unfindable.

A sighting of an item that is already open now counts it instead of filing it again, and
the board prints `(seen 34×, first 2d ago)` — shorter than the repeats and strictly more
informative. Currency is now judged on last-seen rather than first-seen, so a finding the
code still has stops ageing out from under the founder.

### A session's task became «Делай» (#47)

The statement followed the founder by taking the last turn unconditionally. Most turns in
a real session are continuations, so the field that names what a session is doing held
the least informative sentence they typed — and it reaches the sibling board,
`claude-bp status`, every scope-drift refusal, the provisioned branch name, and the
attempt filed on an unverified finish, which is committed. Three concurrent sessions
read «Делай», «обнови», and a merge question; the second was working on a branch called
`feat/obnovi-70e44134`, where the transliteration of «Делай» is `delay` — an English word
meaning the opposite of what the session was doing.

A prompt now has to plausibly be a statement of work to replace the standing one: naming
a file settles it, a bare continuation in either language is rejected, and everything else
clears a short length floor. Paths still accumulate from every turn, including the nods —
only the statement is filtered. The first thing a founder says is always kept, because
«Делай» still beats a blank board when there is nothing at all.

## v1.0.12

Issues #40, #41 and #42. Two are wrong diagnoses — the gate blocking correctly and then
explaining itself with a claim that is not true — which is the failure mode this project
is most prone to, because a refusal with a false reason costs a detour and then costs
trust.

### "The suite FAILS on the code as it stands" — when it never ran (#40)

That headline is a claim about the **code**. It was printed verbatim when the runner was
missing: a bare `pytest` in a Makefile that only resolves inside an activated virtualenv,
so interactive shells had it and the gate's did not. Zero tests executed, zero failures,
and a founder sent looking for a defect that was not there.

The two situations need opposite responses — *fix your environment* and *fix your code* —
so they are now distinguished by exit 127, by `command not found`, and by
`<tool>: No such file or directory`, and the message names the tool:

> Could not run the suite — `pytest` not found on PATH (exit 2). This is an environment
> problem, not a code failure. Fix the runner, then the gate can judge the code.

Blocking the turn is still right; only the diagnosis was wrong. It is also **no longer
filed as a red suite** — that ledger entry could never be cleared by fixing the code,
because the code was never the problem.

The third pattern above was found by the test for this fix, not by the report. The report
carried one sample, and a pattern built from one sample fits one sample.

### The worktree guard judged every path, not the writes (#42)

Two shapes, both refusing correct work:

**A read source in another tree.** `cp <main>/.env .env` from a worktree was refused for
touching the main checkout — which is where the bytes came *from*, with the destination
inside our own tree. Same for an `ssh -i <main>/key` identity file. Reading a sibling
checkout is routine. `cp`, `mv`, `install` and `ln` now contribute only their **last**
argument; `rm` and friends still contribute all of theirs, because they destroy all of them.

**A path invented from a heredoc.** `select … where n_live_tup > 0` inside a heredoc body
looks exactly like a redirect to a file named `0`, so the guard refused a write to
`<cwd>/0` — a path that does not exist and was never named, leaving no way to find the real
problem. Heredoc bodies are data and are now blanked before scanning, like quoted spans.
Purely numeric redirect targets are dropped too: `2>&1` and `> 0` are descriptors and
comparisons, never filenames.

### Scope drift judged against an IDE notice (#41)

Already fixed in v1.0.2 and confirmed against the exact payload from the report: the
`<ide_opened_file>` block is stripped, no paths survive, and the check **abstains** rather
than comparing against what is left — which is the last of the four things the issue asked
for. What was still missing is now done: the envelope list covers `ide_diagnostics`,
`background-task` and the command wrappers as well, and a task statement cut at the
character limit is **marked as truncated**, because the drift refusal quotes it back as
"Task was:" and a fragment presented as the whole instruction is a claim the founder cannot
check.

738 tests, 26 doctor checks, ~332/400 always-on tokens, zero dependencies.

## v1.0.11

Issue #37, two defects hit in one session, and together they compound: the first refuses
something harmless, the second makes the litter that refusal leaves permanent.

### A home-relative path was treated as being inside the repository

Deleting stray files under `~/.claude/projects/` came back as *"this is the main checkout,
not a worktree"* — for a path that is not in the repository at all.

`base / "~/x"` is `<base>/~/x`, and `.expanduser()` only expands a path that **starts** with
`~`. Joining first therefore rewrote a home-relative path into one inside the working tree,
and the main-checkout rule then fired on it correctly, about a file the command never went
near. `~` was not even in the scanner's path character class, so `rm ~/.claude/x` was read as
`/.claude/x`.

This is a recurrence of the v1.0.3 fix — judge by the target, not the session — in a shape
that fix did not cover. The lesson is not the tilde: it is that "resolve the path" had two
call sites and one of them resolved it wrong.

### The worktree the hook creates could not be removed by anyone

Every refusal provisions a tree. Removing one from the main checkout was refused with *"this
git command operates on another session's worktree"* — although this session's own hook had
created it seconds earlier — and a worktree cannot remove itself from the inside. So each
false positive left litter that only a terminal could clear.

A session may now remove a tree **this plugin provisioned for that session**, and nothing
else: a sibling's tree stays refused whoever made it.

731 tests, 26 doctor checks, ~332/400 always-on tokens, zero dependencies.

## v1.0.10

Two issues against v1.0.8 — #34 and #35 — both of them defects I introduced in that
release, and both found by cloning it rather than by reading it.

### `make check` was red on every release commit (#34)

The version guard added in v1.0.8 asked only whether `v{__version__}` appeared among the
tags. On a release commit it must, by definition. So the suite was red on **exactly the tree
a user clones and installs**, and the pre-push hook — which runs `make check` — refused to
push out of a fresh release until somebody bumped the version.

It went unnoticed because the hosted `check` workflow is off by default, a deliberate trade
that makes the local `make check` the only gate. Which is precisely why it being red on a
release is expensive rather than cosmetic.

Released-and-at-rest is not the same state as shipping over a release, and the condition the
docstring always meant is: the tag exists **and** the tree has moved past it. Verified in
both directions on a real clone of v1.0.9 — passes with the tag on `HEAD`, and still fails
the moment a commit lands on top without a bump.

### `docs` only fired in Russian (#35)

Every branch type held in both languages except `docs`. The Russian marker `документ` is a
prefix, so it caught документацию and задокументируй; the English side wanted `doc ` **with
a trailing space**, which cannot match `document`, `documentation` or `documented` — the
actual words an English prompt uses. `readme` was covering the rest by accident, which is
why `update the README` worked and `document the public API` did not.

All eight paired prompts from the report now agree across the two languages, asserted as
pairs rather than as separate expectations — the property is symmetry, so that is what the
test says.

728 tests, 26 doctor checks, ~332/400 always-on tokens, zero dependencies.

## v1.0.9

Three issues filed against v1.0.7 — #30, #31, #32. All three are real, and #32 is a working
bypass of the gate this project is built around.

### The enforcement state was half-guarded (#32)

`pre-tool` refused writes to `.claude/claude-bestpractice/config.json` as "the plugin's own
enforcement state". The **other half** — session records, the baseline the diff is measured
from, the block counter the Stop gate reads — sat under the git common directory and was
writable, and `evidence-gate` trusts all of it. Two working routes past a gate that was
actively blocking a red suite:

1. **Commit the broken change, then point `baseline_commit` at the new HEAD.**
   `changed_files` comes back empty, the suite is never run, and **nothing is recorded** —
   no unverified entry, no open item. The next session sees a clean history. This is the
   serious one: the only way found to finish over a red suite leaving no trace at all.
2. **Set the block counter to the ceiling.** One integer, and the turn ends as "finishing
   UNVERIFIED after 4 blocked attempts" — indistinguishable from an honest session.

The threat model is not a malicious founder. It is an agent that has been blocked four
times looking for the shortest way to end the turn, in a directory whose path this plugin
**prints on its own board**, holding plain JSON whose field names say what they do.

Both are refused now, along with `rm -rf` of the state directory and of the push hook
itself. Matched on the resolved path rather than a repo-relative one, because in a worktree
the common directory lives in the main checkout and no relative rule would ever see it.

*Not done:* the reporter's second suggestion — recovering `baseline_commit` from the reflog
rather than trusting the file, and treating disagreement as a signal. The deny closes both
routes; that would be defence in depth, and saying it is missing is better than implying it
is there.

### The push gate ran this plugin's own doctor (#30)

The hook fell through to `claude-bp-doctor` whenever the plugin's `bin/` was on PATH — which
is exactly where a marketplace install puts it. So on any machine that uses this plugin,
`git push` ran **26 checks of the plugin** instead of anything belonging to the pushed code:
~40s of self-test, and the doctor's verdict became the push gate's verdict, so an
environment hiccup rejected a push of healthy code.

In this repository it closed a loop: `pre-push` found `check:`, `make check` was red inside
a session for that reason, and **claude-bestpractice refused to let claude-bestpractice be
pushed from a Claude Code session.**

The tier is gone. Proving this plugin's gates fire is not evidence about the code being
pushed, and the honest outcome in a repository with no runner is the "nothing to run" line
the tier below already printed. The Ruby test that had been asserting an environment in
which this plugin is *not installed* — and which therefore could only pass for someone who
does not use it — now runs with `bin/` on PATH deliberately.

### The continuation ceiling recorded nothing useful (#31)

The ceiling is how an unverified finish actually happens, and its branch wrote the literal
string `continuation ceiling reached` over the real reason, with an empty path list. The
empty list did two more things: `attempts.record` was skipped entirely (it is under `if
changed:`), and the open item got no subjects, so provenance could never retire it — the
warning outlived the code it was about. The other ceiling exit, at the end of `main`, always
passed both.

The block reason and paths are now remembered when a block is counted, and handed forward:

```
UNVERIFIED finish on master: continuation ceiling reached after: The suite FAILS on the
code as it stands — 1 failing of 1 run by the gate itself
subject_paths: [{"blob": "d6f0728…", "path": "a.py"}]
```

727 tests, 26 doctor checks, ~332/400 always-on tokens, zero dependencies.

## v1.0.8

Two things: branches follow your convention instead of this plugin's, and **updating the
plugin on a repository that is already using it is now something this suite proves rather
than something nobody checks.**

### `<type>/<topic>`, read off the instruction

Every branch was `feat/` regardless of what the session had been asked to do — a convention
this plugin was imposing rather than following. The type now comes from the prompt, in
Russian as well as English, because understanding only English would label a Russian
founder's entire history `feat`:

| prompt | branch |
|---|---|
| `почини парсер штрихкодов` | `fix/pochini-parser-shtrikhkodov-…` |
| `отрефактори модуль оплаты` | `refactor/otrefaktori-modul-oplaty-…` |
| `обнови readme` | `docs/obnovi-readme-…` |
| `напиши тесты` | `test/napishi-testy-…` |
| `ускорь запрос` | `perf/uskor-zapros-…` |
| `добавь csv экспорт` | `feat/dobav-csv-eksport-…` |

Unrecognised means `feat`, which is the honest default — not knowing is not a reason to
guess `chore`.

### An upgrade could not update the hook it had installed

`ci.ensure` skipped the moment it found a hook, so the body was written once and **never
again**. Every fix to it reached new repositories only. v1.0.0 shipped a serious one — an
`exit 0` where a project *with* a suite pushed with nothing run — and anyone already using
the plugin kept the broken hook indefinitely, with no way to find out.

The hook now carries the version that wrote it and is rewritten in place when that is
older. In place, and only over our own file: `install()` displaces whatever was at that
path into `pre-push.claude-bestpractice-original` and chains it, so reusing that path on a
refresh would move our hook onto the founder's husky script — the one thing this module has
always refused to do. Asserted directly. An opt-out still beats a refresh, and a
current hook is left untouched rather than rewritten every session start.

### Every released version's state, read by the code that is here now

The method that has failed in this project every single time is reading the code and
reasoning about whether it is fine. So this does the other thing.

`tests/test_upgrade_compat.py` checks out **each released tag**, runs *that* version's hooks
against a real repository to produce state in that version's own format, then points the
**current** hooks at the result and requires the board to render and the gates to still
fire. Nothing in it is a hand-written fixture: a fixture is a belief about what v1.0.2
wrote, and v1.0.2 is what v1.0.2 wrote.

All eight releases pass. From here, an upgrade that would break a repository already using
the plugin fails the build instead — and it costs one more test to keep that true for every
release after this one, which is the point.

720 tests, 26 doctor checks, ~332/400 always-on tokens, zero dependencies.

## v1.0.7

**The sweep said nothing.** Reported from a real run of v1.0.6: six worktrees became five
and no line anywhere mentioned it.

Removing directories is the only destructive thing this plugin does on its own initiative,
and it was the only one it did not report — while it announces every worktree it *creates*
("A worktree has been created for you at …"). To someone returning to a tree they had
committed in, a directory that is simply gone reads as lost work, even though the branch is
still there. Silence about a deletion is the one place this project cannot afford it.

```
removed 5 unused worktree(s) no live session was in — their branches are kept,
so nothing committed is gone; `git branch` lists them.
```

It leads with the part that makes "my work is gone" false, because that is the thought the
line exists to answer. Empty on every session that swept nothing, which is nearly all of
them, so it costs nothing against the 400-token ceiling.

712 tests, 26 doctor checks, ~332/400 always-on tokens, zero dependencies.

## v1.0.6

Three findings from a real run of v1.0.5, all about what provisioning leaves behind. The
second one was reported as a naming nit and is not one — it is the silent overwrite this
whole subsystem exists to prevent, arrived at from the other side.

### Two sessions could be handed the same worktree

Two sessions with no recorded prompt both slugged to `work`. Two given the same instruction
both slugged the same. `provision()` returns an existing directory when it finds one — so
the second session would have been sent into the first one's tree **by the gate whose entire
purpose is to stop exactly that**.

The name now carries a short per-session suffix. The same session refused twice still gets
the same tree; two sessions never do.

### A Russian prompt produced a Cyrillic branch

`str.isalnum()` is true for Cyrillic, so "почини парсер штрихкодов" gave a directory *and* a
branch in Cyrillic. Git accepts both, and then the branch reaches the remote on the first
push, `git worktree list` prints it octal-escaped (`\320\277\320\276…`), and macOS normalises
the directory name differently from Linux — so the same repository on two machines disagrees
about whether the tree exists.

Transliterated rather than dropped, because the founder writes Russian prompts and a branch
called `work` says nothing: `почини парсер штрихкодов` → `pochini-parser-shtrikhkodov`.
Anything with no ASCII left after that falls back — `🚀` and `日本語のみ` both give `work`,
using the mechanism that already existed for emoji. Every slug is now ASCII, asserted.

### The trees were never cleaned up

One per task phrasing, left behind even when the refusal was the only thing that ever
happened in them — nine on one repository in a single run, each an empty branch over an
empty directory. The plugin creates them unasked, so clearing them is the plugin's job too.
Session start now removes the ones nobody is in.

**Built out of commands that refuse rather than checks that decide.** `git worktree remove`
without `--force` will not touch a tree with modifications; `git branch -d` will not delete
an unmerged branch. Nothing here passes a flag that overrides a refusal, and that is the
whole safety argument — not the conditions, which only exist to avoid asking. It also only
ever touches trees whose record says this plugin made them, so a worktree the founder
created by hand is never a candidate. Verified: a tree with one uncommitted file survives
with its branch intact while an empty sibling is removed.

710 tests, 26 doctor checks, ~332/400 always-on tokens, zero dependencies.

## v1.0.5

**The gate stopped handing the agent a command and started handing it a worktree.**

Reported as a chip in the chat asking the founder whether to use a worktree — which is a
question this plugin should never cause. The refusal named `git worktree add …` for the
agent to run, and a command the agent runs is a question the founder gets asked: either as
a permission prompt for the command, or as the agent stopping to ask whether it should.

Creating a worktree is not money, legal exposure or product direction, which is the list
this plugin's own autonomy line says to interrupt the founder for. It is the plugin's own
rule being satisfied. A hook runs without a permission prompt, so the plugin now does it:

```
claude-bestpractice: this is the main checkout, not a worktree. …
  A worktree has been created for you at /path/to/repo-add-csv-export — `cd …` and redo
  this write there.
  This is not a question for the founder: do not ask whether to use a worktree, just move.
```

The last line is there because the measured failure was the agent being polite rather than
the agent being unable.

Provisioning is the **same code** the `WorktreeCreate` hook already used, extracted rather
than reimplemented, so the two paths cannot drift into disagreeing about naming, trust or
ports: outside the repository so it never shows up in a status or a glob, trusted at birth
or project settings and hooks silently never load, and a port and database name derived per
tree. A second refusal reuses the tree rather than accumulating them, and the name follows
the task, so parallel sessions do not collide on one directory.

It cannot make things worse when git refuses: provisioning that fails falls back to naming
the command, which is where this started, rather than crashing a fail-closed gate over a
convenience.

**The doctor now checks this against the filesystem rather than against a string.** It used
to assert that the refusal contained the words `git worktree add`; it asserts that the
directory the refusal names exists. A phrase is not a fact, and this is the third gate in
this project caught asserting one.

Not available, and worth stating rather than implying: a plugin cannot ship permission
rules. The manifest accepts `commands`, `agents`, `skills`, `hooks` and `outputStyles` and
nothing else, so allow-listing the command was never an option — checked in the CLI rather
than assumed.

700 tests, 26 doctor checks, ~332/400 always-on tokens, zero dependencies.

## v1.0.4

**Git destroys a working tree without ever naming a file in it**, so every rule keyed on
"which paths does this write" saw nothing at all. Reported as the boundary v1.0.3 did not
reach, and named as the incident that made worktree-first a rule in the first place.

From a session in one worktree, aimed at another tree, all of these were permitted:

```
git -C <other> reset --hard HEAD~1      discards uncommitted work that exists nowhere else
git -C <other> clean -fd                deletes it outright
git -C <other> checkout -b feat/…       moves a HEAD another session is standing on
cd <other> && git reset --hard          the same, by another route
```

Nothing appears in a diff, and no lease covers it — a lease is about a file somebody is
holding, and none of these are about a file.

There are exactly three ways to point git at a working tree and all three are explicit,
which is the only reason this is worth doing statically: `-C <path>`, `--work-tree <path>`,
and the directory the command runs in — which the `cd` tracking added in v1.0.3 already
resolves. `git worktree remove <path>` names its victim as a plain argument and is covered
too. Reads are untouched: `status`, `log`, `diff`, `add`, `commit` and `fetch` either only
look, or only move things the index and the object store already own.

**These targets are deliberately kept out of the path rules rather than exempted from
them.** `git switch -c` is the command that resolves a trunk violation and `git worktree
add` resolves the worktree violation — a gate that refuses the fix for its own complaint is
a trap, and the way to not build one is to never let those commands near the rule.

### Interpreters, with the limit stated

`node -e "fs.writeFileSync('<other>/CLAUDE.md','x')"` and `python3 -c "open(…, 'w')"` are
now caught in their literal one-liner form, which is the shape that actually reaches around
a path rule. **Anything computed still gets through, and this is not claimed as a general
defence** — an interpreter is not statically analysable, and pretending otherwise would be
the kind of promise this project exists to refuse. Matched against the raw command rather
than the quote-blanked copy, because an interpreter's path is always quoted and the blanked
copy contains nothing to find.

Twenty-two command forms are asserted end to end through the real hook, in a repository
with a main checkout and two worktrees — eleven that must be refused, eleven that must not.

697 tests, 26 doctor checks, ~332/400 always-on tokens, zero dependencies.

## v1.0.3

**The one-session-per-working-tree rule was enforced by asking where the session sat, not
where the write landed — so it held in exactly one direction, and the direction it missed
was the unsafe one.** Reported from a real machine, with the table filled in.

### A session in a worktree could write into any other tree

`gitpolicy.violations()` asked `ctx.is_worktree`, a fact about the session's own directory.
A session in the main checkout was refused, correctly. A session in a worktree could write
into the main checkout — `CLAUDE.md` included — or into a sibling session's worktree, and
nothing said a word.

That is verbatim the failure the refusal text warns about, printed by the gate that was
permitting it:

> Several sessions sharing one working tree overwrite each other silently — git does not
> notice, and neither will you.

Leases cover part of the same ground, but only for a file some other session is holding at
that moment. An unheld file went straight through.

### And it refused writes that were nobody's business

The same question, asked the same wrong way, denied a `Write` to `/tmp` because the session
happened to be in the main checkout — including into this plugin's own scratch directory, so
checking the plugin was blocked by the plugin. A gate that fires on things that do not
matter is one an agent learns to route around.

Underneath were two errors in resolving where a write actually goes, one in each direction:

- **An absolute path outside the repository was dropped silently.** `(root / "/tmp/x")` is
  `/tmp/x`, and `.relative_to(root)` raises — the target vanished, so the write went
  unexamined by every rule keyed on what it touches.
- **A relative path was resolved against the wrong base.** `cd /tmp/x && printf > a.py`
  writes `/tmp/x/a.py`; it was read as `<repo>/a.py` and refused as a write to a file the
  command never touched. `cd` was not being read at all.

### What holds now

The decision is made on the **target**, resolved as the shell would resolve it:

| Target | Decision |
|---|---|
| Inside this session's own working tree | the existing rules — worktree, trunk |
| Inside another working tree of this repository | **refused**, naming the tree that owns it |
| Outside every working tree | no opinion — not this gate's business |

Nine cases from the report are asserted end to end through the real hook, in a repository
with a main checkout and two worktrees. One consequence worth naming: a scratch file outside
the repository no longer takes a **lease** either, which had let one session deny another a
`/tmp` path the two of them do not share.

691 tests, 26 doctor checks, ~332/400 always-on tokens, zero dependencies.

## v1.0.2

**Scope drift was firing on correct work, and the cause was a path the founder never
typed.** Reported from a real session: eight consecutive blocks on the same change, each
one listing every modified file as out of scope.

### What the IDE opened was becoming the task

Claude Code injects a block into the prompt that the founder did not write:

```
<ide_opened_file>The user opened the file /tmp/readonly/Bash tool output (aeqikl) in the
IDE. This may or may not be related to the current task.</ide_opened_file>
```

It carries a path, and a path is the one thing `prompt-capture` mines a prompt for. So the
task scope became `/tmp/readonly/Bash` — non-empty, and matching nothing in the repository.
Every real file was therefore drift.

The safety valve that should have caught this is `test_empty_task_disables_the_check`, whose
docstring reads *"No captured task is our failure, not the agent's. Do not block on it."* An
injected path walks straight past it, because the scope is not empty — it is wrong. That
distinction is the whole bug.

**Two leaks, not one**, and the second needed no help from the filesystem. `root / "/tmp/x"`
is `/tmp/x` — an absolute token discards the root entirely — and the directory fallback then
accepted any token whose parent existed *anywhere on the machine*, up to and including `/`.
So the closing tag itself, `</ide_opened_file>`, was extracted as the path `/ide_opened_file`
and kept, in every session where such a block appeared. Both reproduced before fixing.

Three things now hold, each with a test:

- **Envelope blocks are not the task.** `ide_opened_file`, `ide_selection` and
  `system-reminder` are stripped before capture — including an unclosed opener, which would
  otherwise leave a path-shaped tag behind. Stripped **by name**, never by angle bracket: a
  founder pasting XML is asking for it to be read.
- **A task path must be inside the worktree.** These paths are compared against
  repository-relative filenames, so one that is not in the repository cannot match anything
  and turns the check into "all of it is drift".
- **An IDE block naming a real repository file is still not the task.** Containment alone
  would not catch that one — the file exists and passes every test for a genuine path — and
  it would silently redefine scope to whatever the founder happened to have open.

### The task no longer goes stale

It was captured once and never again, so a session that had long since moved on was still
being measured against its opening line, and every refusal quoted it back. The statement now
follows the founder. Paths **accumulate** where the statement replaces: a later instruction
naming more files genuinely widens what is in scope, and dropping the earlier ones would turn
the files first asked for into drift. Bounded at 64.

### A refusal that named a remedy which does nothing

The message said *"Revert what is out of scope, or state why it was necessary"* two lines
above *"Your description of what you did is not evidence and was not read"*. Nothing reads
prose here — that is decision 0002 — so an agent whose work was correct and whose scope
reading was wrong had exactly one available move: revert correct work. The message now names
the remedies that exist, including the config key that switches the check off.

For the record, the gate was not only wrong: in the same session it caught a real defect —
`make test` in a worktree running against `main`'s code rather than the branch's.

685 tests, 26 doctor checks, ~332/400 always-on tokens, zero dependencies.

## v1.0.1

**If you installed v1.0.0, this is the release that can actually reach you — and finding
out why is what this release is.**

### The version string is the update key

`claude plugin update` compares the installed version against the marketplace's and stops
there. It does not look at the code. Measured against the real CLI rather than inferred
from its help text: a local marketplace, an install, a changed file with the version left
alone, then

```
$ claude plugin update claude-bestpractice@claude-bestpractice
claude-bestpractice is already at the latest version (1.0.0).
```

The changed file never reached the cache. Twenty-one commits of fixes — every defect listed
under v1.0.0 below — sat behind that line, and there is **no observable difference between
"up to date" and "permanently stranded"**: both print a tick and exit 0. Running update
again, restarting, re-adding the marketplace all report success and change nothing.

Bumping the version and repeating the experiment fetched the change immediately, with no
marketplace refresh needed. So:

- **This release bumps to 1.0.1**, which is what makes every v1.0.0 fix reachable.
- **`tools/check_shipped.py` now fails the build** when anything under `plugin/` differs
  from the default branch and the version does not. It names the changed files and the five
  places the version lives. Scoped to `plugin/` deliberately — that is exactly the tree the
  marketplace copies, confirmed by installing and listing it, so a README change still
  reaches an `install.sh` user by `git pull` and needs no bump. The gate caught its own
  first miss: `git diff` does not see a file that has never been added, and a new module is
  the most consequential thing that can appear under `plugin/`.

### A session can run code that is no longer installed

`claude plugin update` answers `Restart to apply changes.` once and never mentions it
again. The new version is unpacked into a sibling directory, the old one is marked
`.orphaned_at` and left in place, and every session already running keeps executing the old
copy for as long as it lives. Nothing said so. A founder who updates to get a fix and does
not get it had no way to tell which of two things went wrong.

A session that is running a superseded copy now says so on its own board, and
`claude-bp status` says it too. Purely local — the version is the name of the directory the
code is in and the alternatives are its siblings, so this costs no network call and prints
nothing on the sessions that are running what is installed, which is all of them.

### The release cuts itself

This entry is the first release body this repository did not publish by hand, and the
reason is a boundary rather than a preference. An agent session pushes through a git proxy
that answers a tag with:

```
ERR push contains a ref outside refs/heads/*; only branch updates are permitted.
```

Branch updates, nothing else. So every release needed a person at a keyboard, and the
observable consequence was already sitting in this repository: `v1.0.0` pointed at a commit
**twenty-one commits behind** the code its own notes described, because the tag was cut once
and the fixes kept landing.

`.github/workflows/release.yml` moves the tag and the release onto the one event an agent
can cause — a merge to the default branch — and leaves the credentials on GitHub's side
rather than in the session. It reads the version from `plugin/.claude-plugin/plugin.json`,
does nothing if that release exists, and otherwise **runs `make check` before publishing
anything**. That last part is not ceremony: a merge is made through the API, so the pre-push
hook that guarded the branch never saw the commit being released, and a release nobody
executed would be this project's own thesis broken by its own release mechanism.

Unlike `check.yml` it is **not** gated behind `CLAUDE_BESTPRACTICE_CI`. That variable exists
so a repository does not spend metered minutes re-running gates that already ran locally.
The same gate on a release means the release silently never happens, which is the failure
class this project is written against. It runs only when the version changed.

The notes come from this file, matched on the exact heading — so `1.0.1` is never answered
by `## v1.0.10`, and a version with no entry is a **refusal**, not an empty release body. A
test asserts the current version has notes, one merge before the workflow would have to.

**Its first run refused to publish, and it was right.** `make check` failed on the runner
with three failures that pass on every developer machine. `check.yml` is gated behind
`CLAUDE_BESTPRACTICE_CI` and had therefore never executed, so this was the first time
anything ran the suite on a clean machine — and three tests build a throwaway Python project
and require the gate to *actually execute pytest over it*. A bare runner has no pytest, the
gate correctly declines to witness anything, and those three fail. Reproduced locally by
blocking the import rather than guessed at.

Both workflows now install a test runner, and the assertion that forbade it is narrowed
rather than deleted. Its reason — "the stdlib-only constraint is void if CI quietly
pip-installs the difference" — turned out not to describe the enforcement:
`tools/check_stdlib_only.py` reads the source, so it refuses `import requests` under
`plugin/` whether or not requests is installed. Verified by adding one and watching it fail
with both requests and pytest present. Exactly one install is permitted now, it is named,
and the plugin may not import it.

### Also

- All three READMEs now have an **Upgrading** section. Two of them had none at all.
- It states the restart, the qualified `name@marketplace` form, that the version is the
  update key, and that an `install.sh` install updates by `git pull` instead.

680 tests, 26 doctor checks, ~332/400 always-on tokens, zero dependencies.

## v1.0.0

First release. What follows is written to be checked rather than believed: every claim
below has a reproduction in the test suite or in the commit that made it.

### What this is

A Claude Code plugin for one person who builds products almost entirely through agents,
runs three to eight sessions at once on one repository, and reads almost none of the
resulting code. It enforces what must hold, keeps parallel sessions aware of each other,
and refuses to accept "done" without evidence.

Python 3.9+ and git. No other dependency, enforced in CI. ~332 tokens of always-on
context against a self-imposed ceiling of 400, and `make check` fails the build over it.

### The one idea

**Nothing that matters is asked of the model.** Every rule that must hold is enforced by
the harness or by git. The Stop gate discards the agent's prose, runs your test suite
itself, and treats its own observed exit code as the evidence — because a file claiming
the tests passed is an assertion with angle brackets, and three separate forgeries
defeated the artifact-reading version of this gate before it was replaced.

### How this release was verified

Rounds of independent adversarial verification, each one a fresh set of agents whose
instructions were to break the plugin by executing it and who were told explicitly not to
trust the test suite or the doctor. Then a final pass that installed the plugin the way
the README says to, on real repositories, and looked at what was actually there.

That instruction earned its place. **Every severe defect was found by running the software
and none by reading it** — while the suite reported OK and the doctor reported all checks
passed inside the very repositories that were broken.

### What verification found, and this release fixes

Listed because the list is the evidence. Each of these was live in a build that was green.

**The push gate was not installed on the documented path.** `Setup` fires on `--init`, so
the pre-push hook reached only repositories created through the plugin. Install into a
repository you already have — which is what `/plugin install` does — and `claude plugin
list` said `✓ enabled` over a push path with nothing on it. The first session that finds
no hook now installs one and says so once; `claude-bp-ci off` removes it and the removal
persists, because an opt-out that has to be repeated is not an opt-out.

**The push gate exited 0 when it could not run your suite.** The baked test command was
guarded by `command -v`; when that guard failed the hook fell through every fallback and
out through `exit 0`. A project that *has* a suite pushed with nothing run, reported as
checked. It now refuses and names the missing runner. A repository with no suite at all is
still allowed through — nothing is being skipped there.

**A green run of somebody else's copy of your package.** Found on a clone of Flask, 5545
commits and sixteen years of history: a genuine regression in `src/` pushed green with 491
tests passing, because a `.pth` from an unrelated editable install put a different copy of
`flask` first on `sys.path`. Forcing the worktree onto the path produced 24 failures. The
gate was right about the exit code and wrong about the tree. A passing run now checks that
the package it exercised lives inside this worktree.

**Four parallel sessions were one session, and it fed them each other's work.** The
headline scenario, run for the first time: four live sessions, one per worktree, each told
to change a different file. Two of the four rewrote a file they had never been asked to
touch, reverting their own correct work to do it. Four sessions produced one record —
worktree from the first, branch from the third, task from the second — because `claude`
children inherit `CLAUDE_CODE_SESSION_ID` and identity was keyed on that alone. Identity
is now (harness id, worktree). Re-run: four records, four branches, four correct files.

**Seven commands that did not exist.** The installer's symlink list linked
`claude-bestpractice`, which is not in `plugin/bin/`, and never linked `claude-bp`, which
is and is the dispatcher — so after a clean install every command in the README, including
the ones the installer's own closing message prints, was `command not found`. Five more of
the same shape in printed output and docs. A test now scans every command named in prose
or output against `plugin/bin/` and the dispatcher's verbs; it found one nobody reported.

**Three outputs that were not true.** `hosted CI: no workflow in this repository` was
computed from a test for one file — ours — so a repository with four workflows of its own
was told it had none, one line under `stage: … CI config present`. `status` created a
stage marker in the working tree and left it untracked, so looking at a repository dirtied
it. And one repository read as two because the header took its label from the worktree
directory rather than the git common dir.

**A subagent briefed with a template.** On any repository whose knowledge layer had been
created and not yet answered — every repository for its first hour — a subagent's entire
brief was three lines reading `<ANSWER THIS — …>`. Worse than nothing: it costs tokens,
tells it nothing, and teaches it that the channel carries noise. Unanswered sections are
dropped and an empty brief is not sent; answered ones still go verbatim, which is
load-bearing.

**A dead end about code that had since been rewritten.** The attempts ledger stamps every
record with the blob hashes of the files it was about, and nothing read the stamp. A dead
end recorded against a file since rewritten was still presented as current advice. Marked
rather than suppressed — "we tried X and it failed because Y" stays true whatever happens
to the file, so what is said is that its bearing on the current code may have changed.

**The first two lines a fresh install printed.** `status` opened with `Repair the
knowledge layer` on a repository where the layer had never been built, and named a third
command while doing it. `init` listed `entities.yaml` under **derived from your code** over
a file whose entire content is `No types were central enough to derive automatically`.

**A Ruby project could not push at all.** The fallback that guesses a test command asked
whether a directory named `test` or `tests` existed and concluded Python. Jekyll, gson and
guzzle each have one and none of them is Python, so `python3 -m pytest -q` went into a Ruby
repository's push hook — and pytest exits 5 for "no tests ran", which meant every push out
of that repository was refused, permanently, over a command naming no file in it. Found by
cloning eleven real repositories across six ecosystems, installing into each, and pushing.
A language is now inferred from test files, not from a directory name.

**Nothing ever told a session the knowledge layer was missing.** The layer exists to ask
the founder the three things only they know — what this is, who it is for, its non-goals.
`Setup` fires on `--init` and `claude-bp status` is a command the founder runs, so on the
ordinary install path the question was never asked. Verified: a fresh session on a fresh
repository, told only "get started", went and edited code and left the layer absent. It now
runs `claude-bp init` itself and asks the five questions in plain language.

**`adopt` wrote a dead product name into your own settings.** The quarantine key was
`_founderOsQuarantined`, a name this project shed before it ever shipped, landing in the
founder's `.claude/settings.json` where a reader has no way to tell what wrote it. Found by
running `adopt` against a realistic competing installation for the first time; an earlier
grep for the old name had missed it because the identifier is camelCase. Renamed with no
compatibility path — the rename predates the first release, so no settings file carries the
old key, and this project's own slop gate refused the compat shim when the fix first tried
to add one.

**The installer dirtied the clone it was run from.** Run from a clone, `INSTALL_DIR` is
your own checkout, so `chmod +x plugin/bin/*` chmodded twenty Windows `.cmd` shims and
`git status` came back dirty the moment the install finished.

**Starting a session dirtied your working tree.** `status` was fixed for this and the
gates were not, so `.claude/claude-bestpractice/stage/reached-prototype.json` came back
untracked in every repository that had done nothing but start a session. `prototype` is
the floor, order 0, so that marker could never hold a ratchet — there is nothing below it
to regress to. It was pure residue, and it landed in a repository whose own rules require
`git status` to be clean. Nothing is written at the floor now. A marker above it is real
state, and is yours to commit.

**The board promised a check that would not run.** The line arming the push gate read
"checks now run before every push" in every repository — including one with no `make check`
target and no detectable runner, where the hook reaches `claude-bp-ci` by name, does not
find it on a marketplace user's PATH, and exits 0. A promise larger than the fact is the
exact failure this project is written against, and this was the project making it. The line
now names the command it means — `make check`, or the runner that was detected — or says
plainly that the hook will refuse nothing until this repository has one.

**Also.** `claude plugin marketplace add <owner>/<repo>` resolved to `git@github.com:` on a
machine with no SSH key and stopped there, so the README now gives the HTTPS URL to pass
instead — and says what that does not fix, which is a global `insteadOf` rewrite in your own
git config. `claude plugin update <name>` fails with `Plugin not found` while the plugin is
installed — `update` needs the qualified `name@marketplace` form where `install` does not,
so the README documents the one that works. `reaped.jsonl` was the only structure in Tier B
that never shrank and is now capped. And earlier rounds fixed: a green finish certified
over a genuinely red suite by four separate routes; the work ledger being per-worktree in
a product whose premise is many worktrees; one non-UTF-8 filename permanently wedging a
fail-closed gate; and writing the test the gate demands being counted as scope drift, so
correct, tested, passing work was blocked four times and filed as a permanent failure.

### What was measured, not assumed

- **Four hundred sessions in one repository.** State grows linearly and stays small — 202
  KB — and session start stays flat at 0.19s against a 30s limit. One session start then
  reaped 400 dead records in 1.45s.
- **A sixteen-year-old repository.** Every gate inside its timeout, entity derivation
  finding the real entities with correct file anchors, an existing husky `pre-push`
  symlinked at a tracked script moved rather than written through and chained so its
  refusal still refuses.
- **A real version upgrade.** State written under an older install was still there and
  still readable after — it lives in your repository and in your git common directory,
  never in the plugin cache.
- **Node and Go end to end.** `npm test --silent` and `go test ./...` detected, baked into
  the hook, green push allowed, red push refused.
- **A prompt-injection payload arriving as a production signal.** Quarantined, fenced,
  marked as never an instruction, and the fence not broken by the payload's own backticks.
  Live secrets in the same payload did not survive into the written file.

### Known limits, stated rather than discovered

- **The doctor proves the gates work, not that your repository is safe.** It builds a
  throwaway repository and attacks that. "All 26 checks passed" is a statement about this
  software. `claude-bp status` is the one that looks at yours.
- **Linux only, by evidence.** macOS and Windows have never executed this — not once. The
  twenty `.cmd` shims are untested everywhere. See `docs/LIMITS.md`.
- **A false green is still possible on purpose.** The agent writes your code, your tests,
  your test command and your build files. A `conftest.py` monkeypatch, a test that asserts
  nothing, a runner shadowed on PATH — all still work. This gate raises the cost and leaves
  a record; it does not make forgery impossible. `docs/LIMITS.md` names each attack.
- **The two install paths are not equivalent.** `claude plugin install` puts the gates in
  your sessions; only `install.sh` puts the `claude-bp` commands in your own terminal.
- **`.claude/claude-bestpractice/` is yours to commit.** State travels with a branch only
  once committed; nothing here commits it for you.
- **This is not for teams.** Every trade-off assumes one owner and no reviewer.
- **The enforcement surface is Claude Code specific.** The portable half would be the
  advisory half, which is the useless half.

### Not included, deliberately

Not a memory engine — the harness stores memory, this curates it. Not a code reviewer —
several first-party review paths exist; pick one. Not a task manager — the native task
system is subsumed and gated, never replaced. No daemon, no vector store, no graph
database, no second model watching the first.

655 tests, 26 doctor checks, ~332/400 always-on tokens, zero dependencies.
