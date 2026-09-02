"""What the repository claims about itself, checked against itself.

None of this is exotic; all of it shipped wrong at least once. The version lived in
three files that disagreed (0.1.0, 0.0.0, and 1.0.0 in a README badge), and the
advertised one-command install pointed at a `main` branch this repository does not
have, so the single line every new user runs first returned 404. Both are invisible to
every other gate here: they are claims about the packaging, and the packaging is the
part no test exercises.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

from helpers import BIN, REPO_ROOT, RepoCase, session_record_for

READMES = ("README.md", "docs/README.ru.md", "docs/README.zh.md")


def scope():
    """`tools/_scope.py`, loaded by path.

    `tools/` is not a package and these tests do not run from it, so an import statement
    cannot reach it. Worth reaching: a provisioned worktree is a second copy of this
    repository INSIDE it, and every walk that does not exclude one finds everything twice.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("_scope", REPO_ROOT / "tools" / "_scope.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


class TestVersionAgreement(unittest.TestCase):
    def declared(self) -> str:
        source = read("plugin/lib/claude_bestpractice/__init__.py")
        match = re.search(r'__version__\s*=\s*"([^"]+)"', source)
        self.assertIsNotNone(match, "__init__.py has no __version__")
        return match.group(1)

    def test_manifests_match_the_package(self) -> None:
        version = self.declared()
        plugin = json.loads(read("plugin/.claude-plugin/plugin.json"))
        self.assertEqual(plugin["version"], version)

        market = json.loads(read(".claude-plugin/marketplace.json"))
        entries = [p for p in market["plugins"] if p["name"] == plugin["name"]]
        self.assertEqual(len(entries), 1, "plugin listed zero or twice in the marketplace")
        self.assertEqual(entries[0]["version"], version)

    def test_every_readme_badge_matches(self) -> None:
        version = self.declared()
        for rel in READMES:
            badge = re.search(r"badge/version-([0-9][^-\s)]*)-", read(rel))
            self.assertIsNotNone(badge, f"{rel} has no version badge")
            self.assertEqual(badge.group(1), version, f"{rel} badge is stale")


class TestTheReleaseCutsItself(unittest.TestCase):
    """A tag is a ref outside `refs/heads/*`, and an agent session cannot push one.

        ERR push contains a ref outside refs/heads/*; only branch updates are permitted.

    So the release moved to a workflow fired by a merge, which is the one event an agent
    can cause. That makes the changelog load-bearing: it is now the release body, read by
    a program with nobody watching.
    """

    def declared(self) -> str:
        source = read("plugin/lib/claude_bestpractice/__init__.py")
        return re.search(r'__version__\s*=\s*"([^"]+)"', source).group(1)

    def test_the_current_version_has_notes_to_release(self):
        """Bumping the version without writing the entry would publish an empty release.

        The workflow refuses instead, so this catches it one merge earlier — while the
        person who bumped it is still here.
        """
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "tools" / "release_notes.py"), self.declared()],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertGreater(len(proc.stdout.strip()), 200, "the entry is a stub")

    def test_a_missing_entry_is_a_refusal_not_an_empty_body(self):
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "tools" / "release_notes.py"), "9.9.9"],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout.strip(), "")

    def test_a_heading_is_matched_whole(self):
        """`1.0.1` must not be answered by `## v1.0.10`, and must not be found inside
        the prose of some other entry. Both would release the wrong notes silently."""
        sys.path.insert(0, str(REPO_ROOT / "tools"))
        try:
            import release_notes
        finally:
            sys.path.pop(0)

        text = "## v1.0.10\nten\n\n## v1.0.1\none\n\n## v1.0.0\nmentions v1.0.1 here\n"
        self.assertEqual(release_notes.section(text, "1.0.1"), "one")
        self.assertEqual(release_notes.section(text, "1.0.10"), "ten")
        self.assertIsNone(release_notes.section(text, "2.0.0"))

    def test_the_workflow_publishes_only_what_it_proved(self):
        """A release nobody executed is this project's own thesis, broken by its own
        release mechanism. A merge is made through the API, so the pre-push hook that
        guarded the branch never saw the commit being released."""
        workflow = read(".github/workflows/release.yml")
        self.assertIn("make check", workflow)
        self.assertLess(
            workflow.index("make check"), workflow.index("gh release create"),
            "the suite must run before the release is published",
        )

    def test_every_workflow_that_runs_the_suite_installs_a_runner_for_it(self):
        """Three tests drive a real pytest over a throwaway project; a bare runner has none.

        This was latent for as long as the repository existed: `check.yml` is gated behind
        a variable and had never executed, so nothing had ever run `make check` on a clean
        machine. The release workflow was the first, and it failed exactly here — which is
        the gate refusing to publish something it could not prove, working as intended.
        """
        # `check.yml` runs the gates as separate steps and never says `make check`, so each
        # workflow is checked against the target it actually invokes.
        for rel, target in (
            (".github/workflows/check.yml", "make test"),
            (".github/workflows/release.yml", "make check"),
        ):
            body = read(rel)
            self.assertIn(target, body, rel)
            self.assertIn("pip install", body, f"{rel} runs the suite with no pytest")
            self.assertLess(
                body.index("pip install"), body.index(target),
                f"{rel} installs the runner after it needs it",
            )

    def test_the_release_workflow_is_not_gated_off(self):
        """`check.yml` is gated on a variable so it costs nothing until switched on.
        The same gate on a release means the release silently never happens."""
        workflow = read(".github/workflows/release.yml")
        self.assertNotIn("vars.CLAUDE_BESTPRACTICE_CI", workflow)
        self.assertIn("contents: write", workflow, "it cannot create a tag without this")


def dispatcher_verbs(dispatcher: Path) -> set:
    """What `claude-bp` accepts, read off the dispatcher rather than kept beside it.

    A hand-kept copy is the very defect the test below exists to catch, one level up: it
    went stale the first time a verb was added, and then failed on correct documentation.
    """
    import re

    found = set(re.findall(r'sub\.add_parser\(\s*\n?\s*"([a-z-]+)"',
                           dispatcher.read_text(encoding="utf-8")))
    assert {"status", "doctor"} <= found, found
    return found


class TestInstallPath(unittest.TestCase):
    """The one line every new user runs before anything else works."""

    ONE_LINER = re.compile(r"raw\.githubusercontent\.com/[^/]+/[^/]+/([^/]+)/install\.sh")

    def test_the_advertised_ref_is_one_that_resolves(self) -> None:
        # `main` is the tempting default and is wrong here: this repository's default
        # branch is not called main. HEAD resolves to whatever the default branch is,
        # and keeps resolving after a rename.
        for rel in ("install.sh",) + READMES:
            for ref in self.ONE_LINER.findall(read(rel)):
                self.assertEqual(ref, "HEAD", f"{rel} advertises ref {ref!r}")

    def test_the_installer_is_executable(self) -> None:
        self.assertTrue((REPO_ROOT / "install.sh").stat().st_mode & 0o111, "install.sh is not +x")

    def test_every_command_named_anywhere_exists(self) -> None:
        """Six bugs in one shape: prose naming a binary that is not there.

        The installer linked `claude-bestpractice` (dangling) and not `claude-bp` (the
        dispatcher), so every command in the README was `command not found` after a clean
        install — including the ones the installer's own closing message printed. `status`
        advised `claude-bestpractice adopt`; `adopt` advised `claude-bestpractice adopt
        --restore`; the README documented `claude-bp ci off` against a dispatcher that
        accepts four verbs and not that one.

        Every one is a name written by hand and never checked against `plugin/bin/`. So
        check it mechanically, once, for every file that can carry such a name.
        """
        import re

        bin_dir = REPO_ROOT / "plugin" / "bin"
        real = {p.name for p in bin_dir.iterdir() if p.is_file()}
        verbs = dispatcher_verbs(bin_dir / "claude-bp")

        # Prose and printed output only. `install.sh` refers to these paths as shell
        # globs and in comments about this very bug, and has its own structural test.
        sources = [*bin_dir.glob("claude-bp*"), REPO_ROOT / "README.md"]
        named = re.compile(r"`(claude-b[\w-]*)((?: [a-z-]+)?)")

        problems = []
        for path in sources:
            if path.suffix == ".cmd":
                continue
            for binary, verb in named.findall(path.read_text(encoding="utf-8")):
                if binary not in real:
                    problems.append(f"{path.name}: `{binary}` is not in plugin/bin/")
                elif binary == "claude-bp" and verb.strip() and verb.strip() not in verbs:
                    problems.append(f"{path.name}: `claude-bp {verb.strip()}` is not a verb")
        self.assertEqual([], problems, "\n".join(problems))

    def test_the_installer_links_every_command_and_no_gate(self) -> None:
        """Derived from bin/, never a hand-kept list — the list is what drifted."""
        installer = read("install.sh")
        self.assertIn('"$INSTALL_DIR"/plugin/bin/claude-bp ', installer)
        self.assertIn('"$INSTALL_DIR"/plugin/bin/claude-bp-*', installer)
        for gate in ("pre-tool", "session-start", "evidence-gate"):
            self.assertNotIn(
                f"/plugin/bin/{gate}", installer, f"{gate} is a hook handler, not a command"
            )

    def test_the_installer_leaves_a_clone_clean(self) -> None:
        """Run from a clone, `INSTALL_DIR` is the founder's own checkout.

        `chmod +x plugin/bin/*` therefore chmodded twenty `.cmd` shims that nothing on
        this platform executes, and `git status` came back dirty on twenty files the
        moment the install finished. Caught by a stop hook complaining about uncommitted
        changes after the installer had been run against this very repository.
        """
        installer = read("install.sh")
        self.assertNotIn(
            'chmod +x "$INSTALL_DIR"/plugin/bin/*',
            installer,
            "the installer chmods the .cmd shims and dirties the clone it ran from",
        )
        self.assertIn("! -name '*.cmd'", installer, "the .cmd shims are not excluded")

    def test_the_cmd_shims_are_not_executable(self) -> None:
        """The mode the installer must stop changing. 644 is deliberate, not an accident."""
        for shim in (REPO_ROOT / "plugin" / "bin").glob("*.cmd"):
            self.assertFalse(
                shim.stat().st_mode & 0o111,
                f"{shim.name} is +x; a Windows batch file is never executed here",
            )

    def test_the_license_the_manifests_claim_exists(self) -> None:
        plugin = json.loads(read("plugin/.claude-plugin/plugin.json"))
        text = read("LICENSE")
        self.assertIn(plugin["license"], text.replace(" License", ""))
        self.assertNotIn("[year]", text, "LICENSE still has a placeholder")
        self.assertNotIn("[fullname]", text, "LICENSE still has a placeholder")


class TestTranslationsStayInStep(unittest.TestCase):
    """A translation that silently stops matching is worse than no translation."""

    def test_each_readme_links_to_the_other_two(self) -> None:
        expected = {
            "README.md": ("docs/README.ru.md", "docs/README.zh.md"),
            "docs/README.ru.md": ("../README.md", "README.zh.md"),
            "docs/README.zh.md": ("../README.md", "README.ru.md"),
        }
        for rel, targets in expected.items():
            text = read(rel)
            for target in targets:
                self.assertIn(f"({target})", text, f"{rel} does not link to {target}")

    def test_relative_links_resolve(self) -> None:
        for rel in READMES:
            base = (REPO_ROOT / rel).parent
            for target in re.findall(r"\]\((?!https?:|#)([^)]+)\)", read(rel)):
                path = (base / target.split("#")[0]).resolve()
                self.assertTrue(path.exists(), f"{rel} links to missing {target}")

    def test_the_gate_table_covers_the_same_gates_everywhere(self) -> None:
        gates = {
            "setup", "session-start", "prompt-capture", "pre-tool", "review-commit",
            "worktree-create", "subagent-brief", "checkpoint", "evidence-gate",
        }
        for rel in READMES:
            text = read(rel)
            missing = {g for g in gates if f"`{g}`" not in text}
            self.assertFalse(missing, f"{rel} omits gate(s): {sorted(missing)}")

    def test_every_documented_gate_actually_ships(self) -> None:
        for rel in READMES:
            for gate in re.findall(r"^\| `([a-z-]+)` \| ", read(rel), re.M):
                self.assertTrue(
                    (REPO_ROOT / "plugin" / "bin" / gate).is_file(),
                    f"{rel} documents `{gate}`, which does not exist in plugin/bin/",
                )



class TestSkills(unittest.TestCase):
    """Skills are loaded on demand, so they cost nothing until they are relevant."""

    SKILLS = REPO_ROOT / "plugin" / "skills"

    def frontmatter(self, path: Path) -> dict:
        text = path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---"), f"{path.name} has no frontmatter")
        block = text.split("---", 2)[1]
        return dict(
            (k.strip(), v.strip())
            for k, _, v in (line.partition(":") for line in block.splitlines())
            if k.strip()
        )

    def test_every_skill_declares_a_name_and_a_trigger(self):
        """A description that does not say WHEN to use it never gets invoked."""
        found = list(self.SKILLS.glob("*/SKILL.md"))
        self.assertGreaterEqual(len(found), 3)
        for path in found:
            with self.subTest(skill=path.parent.name):
                meta = self.frontmatter(path)
                self.assertEqual(meta.get("name"), path.parent.name)
                description = meta.get("description", "").lower()
                self.assertTrue(
                    any(trigger in description for trigger in ("use when", "use at", "use whenever")),
                    f"{path.parent.name}: the description never says WHEN to use it, "
                    "so it will never be invoked",
                )

    def test_none_of_them_is_always_on(self):
        """The always-on budget is 400 tokens; a skill loaded eagerly would blow it."""
        hooks = (REPO_ROOT / "plugin" / "hooks" / "hooks.json").read_text(encoding="utf-8")
        for path in self.SKILLS.glob("*/SKILL.md"):
            self.assertNotIn(path.parent.name, hooks, f"{path.parent.name} is wired to an event")

    def test_they_are_specific_enough_to_act_on(self):
        """"Make it modern" is not actionable and produces the same page every time."""
        landing = (self.SKILLS / "landing-not-slop" / "SKILL.md").read_text(encoding="utf-8")
        for concrete in ("gradient", "375px", "4.5:1", "prefers-reduced-motion"):
            self.assertIn(concrete, landing)

        defaults = (self.SKILLS / "founder-defaults" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("[enforced]", defaults, "the defaults must say which are binding")


class TestItInstallsTheShortestWay(unittest.TestCase):
    """`claude plugin marketplace add <owner>/<repo>` is the shareable install.

    The CLI clones the repository and looks for `.claude-plugin/marketplace.json` at its
    ROOT. With the manifest one directory down the shorthand fails outright, which is the
    difference between a plugin someone can pass to a friend in one line and one that
    needs a paragraph of instructions.
    """

    def test_the_marketplace_manifest_is_at_the_repository_root(self):
        self.assertTrue((REPO_ROOT / ".claude-plugin" / "marketplace.json").is_file())

    def test_there_is_exactly_one_marketplace_manifest(self):
        """Two of them means the installer and the shorthand register different ids."""
        # A worktree the plugin provisioned holds a checkout of this repository, manifest
        # and all, so a bare walk reports the founder's own tree as a second registration.
        nested = scope().nested_worktrees(REPO_ROOT)
        found = [p for p in REPO_ROOT.rglob(".claude-plugin/marketplace.json")
                 if ".git" not in p.parts and not scope().is_inside(p, nested)]
        self.assertEqual(len(found), 1, f"found {[str(p) for p in found]}")

    def test_the_plugin_source_resolves(self):
        market = json.loads(read(".claude-plugin/marketplace.json"))
        for entry in market["plugins"]:
            target = (REPO_ROOT / entry["source"] / ".claude-plugin" / "plugin.json")
            self.assertTrue(target.is_file(), f"{entry['source']} has no plugin manifest")

    def test_the_installer_registers_that_marketplace(self):
        installer = read("install.sh")
        market = json.loads(read(".claude-plugin/marketplace.json"))
        self.assertIn(f'MARKETPLACE="{market["name"]}"', installer)


if __name__ == "__main__":
    unittest.main()


class TestReindexKeepsWhatItCannotRebuild(RepoCase):
    """Tier B is described as entirely derived, and four of its files record EVENTS.

    A finish that could not be proved, a suite observed failing, a decision drafted and
    not yet accepted: no amount of rescanning the repository brings an event back. Purging
    them was silent and permanent, and the command printed "Nothing durable was lost".
    """

    def seed(self) -> None:
        from claude_bestpractice import board, store

        ctx = self.ctx()
        board.add_open_item(
            ctx, item_id="u-1", text="UNVERIFIED finish on main", branch="main", session_id="s1"
        )
        store.append_jsonl(store.tier_b(ctx, "unverified.jsonl"), {"branch": "main"})
        store.append_jsonl(store.tier_b(ctx, "decision-inbox.jsonl"), {"title": "use postgres"})

    def test_the_records_survive(self):
        from claude_bestpractice import board, store

        self.seed()
        store.purge_tier_b(self.ctx())

        ctx = self.ctx()
        self.assertEqual(len(board.open_items(ctx, branch="main")), 1)
        self.assertEqual(len(store.read_jsonl(store.tier_b(ctx, "unverified.jsonl"))), 1)
        self.assertEqual(len(store.read_jsonl(store.tier_b(ctx, "decision-inbox.jsonl"))), 1)

    def test_the_genuinely_derived_state_is_still_dropped(self):
        """Otherwise this is not a rebuild, it is a no-op with extra steps."""
        from claude_bestpractice import sessions, store

        ctx = self.ctx()
        sessions.register(ctx, session_record_for(ctx, "s1"))
        self.seed()
        store.purge_tier_b(self.ctx())
        self.assertEqual(sessions.load_all(self.ctx()), [])

    def test_the_command_names_what_it_kept(self):
        """"Nothing durable was lost" was printed over deleting every one of them, and a
        claim that specific is the kind a founder stops checking."""
        self.seed()
        proc = subprocess.run(
            [sys.executable, str(BIN / "claude-bp-reindex")],
            cwd=str(self.repo), capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Kept, because nothing can rebuild them", proc.stdout)
        self.assertIn("unverified.jsonl", proc.stdout)


class TestWindowsShims(unittest.TestCase):
    """`bin/` is twenty extensionless Python scripts with a `#!/usr/bin/env python3` line.

    Windows does not read shebangs. Claude Code runs hooks through Git Bash where it
    exists and PowerShell where it does not, and PowerShell cannot execute an
    extensionless file — so on a machine without Git Bash, not one command AND NOT ONE
    GATE ran. The plugin installed, reported enabled, and enforced nothing.

    PowerShell does resolve a path without an extension through PATHEXT, so a `.cmd`
    beside each script makes the identical hook command work in both shells without a
    single entry in hooks.json changing.

    These tests assert the shims are present and shaped right. They cannot assert that
    they RUN — that needs Windows, and this suite has never been on one.
    """

    BIN = REPO_ROOT / "plugin" / "bin"

    def scripts(self) -> list[Path]:
        return sorted(p for p in self.BIN.iterdir() if p.is_file() and not p.suffix)

    def test_every_executable_has_one(self):
        missing = [p.name for p in self.scripts() if not p.with_suffix(".cmd").is_file()]
        self.assertEqual(missing, [], "these cannot run under PowerShell")

    def test_no_shim_is_orphaned(self):
        names = {p.name for p in self.scripts()}
        orphans = [p.name for p in self.BIN.glob("*.cmd") if p.stem not in names]
        self.assertEqual(orphans, [], "a shim pointing at a script that no longer exists")

    def test_they_use_crlf(self):
        """cmd.exe is unreliable with LF-only batch files."""
        for shim in self.BIN.glob("*.cmd"):
            self.assertIn(b"\r\n", shim.read_bytes(), shim.name)

    def test_they_try_the_launcher_before_bare_python(self):
        """python.org ships py.exe and python.exe and NOT python3.exe — which is the name
        the POSIX shebang resolves to, and the reason a shim is needed at all."""
        for shim in self.BIN.glob("*.cmd"):
            body = shim.read_text(encoding="utf-8")
            self.assertIn("py -3", body, shim.name)
            self.assertIn("python ", body, shim.name)

    def test_they_do_not_hardcode_a_name(self):
        """`%~n0` makes one body correct for all twenty, so a renamed command cannot
        leave a shim silently pointing at the old one."""
        for shim in self.BIN.glob("*.cmd"):
            self.assertIn("%~dp0%~n0", shim.read_text(encoding="utf-8"), shim.name)


class TestThisRepositoryRunsUnderItsOwnPlugin(unittest.TestCase):
    """The gates were never applied to the work that writes them.

    Everything known about their behaviour came from the doctor and from tests in
    sandboxes: both prove the mechanism fires on prepared state, and neither says how it
    behaves in a live turn. The difference stopped being theoretical when a merge of
    unaccepted work went straight through in a session where the plugin was not installed,
    hours after that very refusal shipped (#144).

    The declaration is committed so it travels. The version installed from it is whatever
    is on the default branch — the RELEASED plugin, not the working tree — so a session
    editing a gate is judged by the gate its users have, and a half-written one cannot
    lock the repository that is writing it.
    """

    SETTINGS = REPO_ROOT / ".claude" / "settings.json"
    PLUGIN = "claude-bestpractice@claude-bestpractice"

    def settings(self) -> dict:
        return json.loads(self.SETTINGS.read_text(encoding="utf-8"))

    def test_the_plugin_is_enabled_for_this_repository(self):
        self.assertIs(True, self.settings().get("enabledPlugins", {}).get(self.PLUGIN))

    def test_the_marketplace_is_declared_and_carries_no_machine_path(self):
        """`--scope project` writes an ABSOLUTE directory path by default, which is
        correct on the machine that ran it and wrong in the repository. Declared against
        the GitHub repo instead, which is the same string everywhere."""
        source = self.settings().get("extraKnownMarketplaces", {}).get(
            "claude-bestpractice", {}).get("source", {})
        self.assertEqual("github", source.get("source"))
        self.assertEqual("avanturer/claude-bestpractice", source.get("repo"))

    def test_the_release_checklist_says_when_to_refresh_it(self):
        """A snapshot pinned to the default branch goes stale at the next release, and
        nothing refreshes it on its own. If the step is not written down it is not done."""
        body = (REPO_ROOT / "RELEASING.md").read_text(encoding="utf-8")
        self.assertIn("claude plugin marketplace update", body)
        self.assertIn("claude plugin update", body)

    def test_the_release_checklist_says_to_recheck_after_a_merge(self):
        """A green check before a conflict resolution says nothing about the tree after
        it. Resolving to "ours" is right only when our side is a superset, and it is not
        whenever this branch REPLACED something the trunk also changed — the merge keeps
        both copies. That shipped a broken `main`: two definitions of one function, Python
        taking the older, `NameError` in the path the Stop gate runs suites with."""
        body = (REPO_ROOT / "RELEASING.md").read_text(encoding="utf-8")
        self.assertIn("run `make check` again", body)
        self.assertIn("the check that counts is the one after", body.lower())

    def test_the_release_checklist_names_the_scope_the_update_needs(self):
        """`update` defaults to user scope and the enablement is committed, so the install
        is project-scoped: without the flag it fails with "not installed at scope user".
        The step shipped without it and did not work the first time it was run."""
        body = (REPO_ROOT / "RELEASING.md").read_text(encoding="utf-8")
        self.assertIn("claude plugin update claude-bestpractice@claude-bestpractice --scope project", body)


class TestEveryCommandTheGatesNameCanBeRun(unittest.TestCase):
    """A refusal that names a command which does not exist is a dead end with a helpful
    tone.

    Three of them shipped at once: `pre-tool` refused `config.json` and sent the session to
    `claude-bp ci`, `claude-bp set` refused `test_command` and said the same, and the
    compaction demand named `claude-bp-attempt record`. None of the three is a command. The
    tests that covered those messages asserted their text, which is how the wrong spelling
    stayed in place — so this asserts by INVOKING.

    Scoped to string literals the plugin emits. Docstrings and comments are prose about the
    code and may name a command family loosely; a string the founder or the model is handed
    may not.
    """

    # A command name followed by an English word is prose inside a sentence, not an
    # invocation. Kept small on purpose: a word wrongly listed here hides a real defect.
    PROSE_AFTER = {
        "is", "are", "was", "owns", "runs", "reads", "writes", "prints", "exits", "and",
        "or", "the", "to", "on", "in", "it", "its", "a", "no", "not", "will", "does",
        "can", "by", "for", "of", "with", "at", "as", "so", "that", "this", "then",
        "than", "when", "if", "you", "your", "they", "them", "one", "here", "there",
        "now", "still", "already", "never", "always", "which", "what", "who", "why",
        "asks", "says", "said", "told", "tells", "needs", "wants", "cannot", "could",
        "would", "should", "may", "must", "did", "do", "has", "have", "had", "from",
    }

    NAMED = re.compile(r"claude-bp(?:-[a-z]+)?(?:[ \t]+[a-z][a-z-]*)?")

    def assert_each_one_runs(self, seen: dict, verb: str) -> None:
        """Invoke every command that was found, and say where it was found when it fails."""
        for phrase, files in sorted(seen.items()):
            cli, _, sub = phrase.partition(" ")
            with self.subTest(command=phrase):
                where = ", ".join(sorted(files))
                self.assertTrue((BIN / cli).exists(),
                                f"`{phrase}` in {where}: no such gate ships")
                if not sub or sub in self.PROSE_AFTER:
                    continue
                proc = subprocess.run([sys.executable, str(BIN / cli), sub, "--help"],
                                      capture_output=True, text=True, timeout=60)
                self.assertEqual(
                    0, proc.returncode,
                    f"`{phrase}` is {verb} in {where} and cannot be run:\n"
                    f"{proc.stderr.strip()[:300]}",
                )

    def emitted_strings(self, path: Path) -> list[str]:
        """Every string literal in the file that is not a docstring."""
        import ast

        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                doc = ast.get_docstring(node, clean=False)
                if doc is not None:
                    docstrings.add(id(node.body[0].value))
        return [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ]

    def test_no_emitted_string_names_a_command_that_errors(self):
        sources = sorted(
            [p for p in BIN.iterdir() if p.is_file() and not p.suffix]
            + list((BIN.parent / "lib" / "claude_bestpractice").glob("*.py"))
        )
        self.assertGreater(len(sources), 20, "the source sweep found almost nothing")

        seen: dict[str, set[str]] = {}
        for path in sources:
            for text in self.emitted_strings(path):
                for match in self.NAMED.finditer(text):
                    seen.setdefault(" ".join(match.group(0).split()), set()).add(path.name)
        self.assertTrue(seen, "no command is named anywhere, so this proves nothing")

        self.assert_each_one_runs(seen, "named")

    # Written for a reader, and read by one: the founder opens the docs, and every session
    # is handed `decisions-index.md`. A record that cites a command which exits 2 sends the
    # reader back to it with the authority of a decision — which is where this shipped.
    PROSE_FILES = ("README.md", "docs/README.ru.md", "docs/README.zh.md")

    def markdown_under_review(self) -> list[Path]:
        found = [REPO_ROOT / name for name in self.PROSE_FILES]
        found += sorted((REPO_ROOT / "docs").glob("*.md"))
        found += sorted((REPO_ROOT / ".claude" / "rules").rglob("*.md"))
        # The changelog records what was fixed, so it quotes commands that no longer run —
        # on purpose, and one of them is the fix this test guards.
        return [p for p in dict.fromkeys(found) if p.is_file() and p.name != "CHANGELOG.md"]

    def test_no_document_offers_a_command_that_errors(self):
        # Backticked spans only. A command in prose is written as code; the bare words
        # around it are English, and matching those would make this test unrunnable.
        span = re.compile(r"`([^`\n]+)`")

        seen: dict[str, set[str]] = {}
        for path in self.markdown_under_review():
            for code in span.findall(path.read_text(encoding="utf-8")):
                match = self.NAMED.match(code.strip())
                if match:
                    seen.setdefault(" ".join(match.group(0).split()), set()).add(path.name)
        self.assertTrue(seen, "no document names a command, so this proves nothing")

        self.assert_each_one_runs(seen, "offered")


class TestTheCheckersStayOutOfProvisionedWorktrees(RepoCase):
    """`make check` is the one definition of done, and it failed for anyone using the
    plugin's own worktree flow.

    The plugin provisions worktrees under `.claude/worktrees/`, which puts a second
    checkout of the repository inside the repository. `check_slop` walked into it and
    reported every file twice: measured here at 22,044 duplicate blocks against a budget of
    zero, with `complex_functions` and `long_functions` over budget too. Nothing under
    `tools/` mentioned worktrees at all, so the scope question had never been asked.
    """

    def with_a_nested_worktree(self) -> Path:
        self.write("dup.py", "def one(a, b):\n" + "".join(
            f"    x{i} = a + b + {i}\n" for i in range(12)) + "    return x0\n")
        subprocess.run(["git", "add", "-A"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "seed"], cwd=self.repo, check=True,
                       capture_output=True)
        nested = self.repo / ".claude" / "worktrees" / "work-probe"
        nested.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "worktree", "add", "-q", "-b", "probe", str(nested)],
                       cwd=self.repo, check=True, capture_output=True)
        return nested

    def test_a_nested_worktree_is_not_scanned(self):
        nested_worktrees_of = scope().nested_worktrees

        nested = self.with_a_nested_worktree()
        found = nested_worktrees_of(self.repo)
        self.assertIn(nested.resolve(), [p.resolve() for p in found])
        self.assertNotIn(self.repo.resolve(), [p.resolve() for p in found])

    def test_the_slop_checker_takes_no_file_from_inside_one(self):
        """Asserted on the scan set, not on the exit code.

        An exit code passes for the wrong reason — a small fixture stays under budget even
        when every file in it is counted twice — and that is exactly what a first version of
        this test did: it went green against the unscoped scanner. What discriminates is the
        list of files the checker decided to read.
        """
        nested = self.with_a_nested_worktree()
        proc = subprocess.run(
            [sys.executable, "-c",
             "import sys, json; sys.path.insert(0, sys.argv[1]); import check_slop; "
             "print(json.dumps([str(p) for p in check_slop.python_files(None)]))",
             str(REPO_ROOT / "tools")],
            cwd=self.repo, capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        scanned = json.loads(proc.stdout)
        self.assertTrue(any("dup.py" in p for p in scanned), "it scanned nothing at all")
        inside = [p for p in scanned if str(nested) in p]
        self.assertEqual([], inside, "the checker read files from inside the worktree")
