"""A new dependency demands a comparison — the moment a default gets executed."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest

from helpers import BIN, RepoCase


class OptionCase(RepoCase):
    def cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(BIN / "founder-os-options"), *args],
            capture_output=True, text=True, cwd=str(self.repo), timeout=180,
        )

    def comparison(self, **overrides):
        from founder_os import options

        base = dict(
            id="", problem="cache layer", metrics=["latency", "ops"],
            options=[
                options.Option("redis", {"latency": 9, "ops": 3}),
                options.Option("dict-ttl", {"latency": 7, "ops": 9}),
            ],
            chosen="dict-ttl", why="200 rpm does not need a second service",
        )
        base.update(overrides)
        return options.Comparison(**base)


class TestWhatCountsAsAComparison(OptionCase):
    def test_one_option_is_not_a_comparison(self):
        from founder_os import options

        one = self.comparison(options=[options.Option("redis", {"latency": 9, "ops": 3})], chosen="redis")
        recorded, complaint = options.record(self.ctx(), one)
        self.assertIsNone(recorded)
        self.assertIn("at least", complaint)

    def test_better_with_no_axis_is_a_preference(self):
        from founder_os import options

        recorded, complaint = options.record(self.ctx(), self.comparison(metrics=[]))
        self.assertIsNone(recorded)
        self.assertIn("no metrics", complaint)

    def test_every_option_must_be_scored_on_every_metric(self):
        from founder_os import options

        half = self.comparison(
            options=[
                options.Option("redis", {"latency": 9, "ops": 3}),
                options.Option("dict-ttl", {"latency": 7}),
            ]
        )
        recorded, complaint = options.record(self.ctx(), half)
        self.assertIsNone(recorded)
        self.assertIn("not scored", complaint)

    def test_choosing_the_loser_silently_is_refused(self):
        """Overriding the numbers is allowed. Doing it without saying why is not."""
        from founder_os import options

        recorded, complaint = options.record(self.ctx(), self.comparison(chosen="redis", why=""))
        self.assertIsNone(recorded)
        self.assertIn("scores highest", complaint)

    def test_choosing_the_loser_with_a_reason_is_accepted(self):
        from founder_os import options

        recorded, _ = options.record(
            self.ctx(), self.comparison(chosen="redis", why="we already run redis for sessions")
        )
        self.assertIsNotNone(recorded)

    def test_a_real_comparison_is_recorded(self):
        from founder_os import options

        recorded, complaint = options.record(self.ctx(), self.comparison())
        self.assertIsNotNone(recorded, complaint)
        self.assertTrue(recorded.path.exists())
        self.assertEqual(options.load_all(self.ctx())[0].chosen, "dict-ttl")


class TestDependencyDetection(unittest.TestCase):
    def names(self, text: str) -> set:
        from founder_os.options import _dependency_names

        return _dependency_names(text)

    def test_every_manifest_format(self):
        cases = [
            ('{"name":"app","dependencies":{"redis":"^4.0","express":"~4.18"}}', {"redis", "express"}),
            ('dependencies = ["httpx>=0.27", "redis==5.0"]', {"httpx", "redis"}),
            ("httpx>=0.27\nredis==5.0\n", {"httpx", "redis"}),
            ("module x\n\ngo 1.22\n\nrequire (\n\tgithub.com/gin-gonic/gin v1.9.1\n)",
             {"github.com/gin-gonic/gin"}),
            ('[dependencies]\nserde = "1.0"\ntokio = "1"', {"serde", "tokio"}),
        ]
        for text, want in cases:
            with self.subTest(text=text[:30]):
                self.assertEqual(self.names(text), want)

    def test_a_package_named_like_a_url_prefix_survives(self):
        """`http` as a prefix filter ate `httpx`; a URL filter needs its separator."""
        self.assertIn("httpx", self.names("httpx>=0.27"))
        self.assertEqual(self.names('deps = ["https://example.com/pkg.tar.gz"]'), set())

    def test_container_keys_are_not_packages(self):
        self.assertNotIn("dependencies", self.names('dependencies = ["redis==5.0"]'))


class TestTheGateDemandsIt(RepoCase):
    def stop(self):
        return self.run_hook(
            "evidence-gate",
            {"session_id": "s1", "hook_event_name": "Stop", "stop_hook_active": False},
        )

    def start_with_manifest(self, deps: dict) -> None:
        self.write("package.json", json.dumps({"name": "app", "dependencies": deps}))
        self.write("app.js", "module.exports = 1;\n")
        self.commit()
        self.run_hook("session-start", {"session_id": "s1", "hook_event_name": "SessionStart"})

    def test_adding_a_dependency_without_comparing_is_refused(self):
        self.start_with_manifest({"express": "^4.18"})
        self.write("package.json", json.dumps({"name": "app", "dependencies": {"express": "^4.18", "redis": "^4.0"}}))
        proc = self.stop()
        self.assertEqual(proc.returncode, 2)
        self.assertIn("no comparison on record", proc.stderr)
        self.assertIn("redis", proc.stderr)

    def test_bumping_a_version_is_not_a_decision(self):
        """A gate that fires on `npm audit fix` is off within a day."""
        self.start_with_manifest({"express": "^4.18"})
        self.write("package.json", json.dumps({"name": "app", "dependencies": {"express": "^4.19"}}))
        self.assertNotIn("no comparison on record", self.stop().stderr)

    def test_a_recorded_comparison_satisfies_it(self):
        from founder_os import options

        self.start_with_manifest({"express": "^4.18"})
        options.record(
            self.ctx(),
            options.Comparison(
                id="", problem="cache: redis or a ttl dict", metrics=["latency", "ops"],
                options=[
                    options.Option("redis", {"latency": 9, "ops": 3}),
                    options.Option("dict-ttl", {"latency": 7, "ops": 9}),
                ],
                chosen="redis", why="already running it for sessions",
            ),
        )
        self.write("package.json", json.dumps({"name": "app", "dependencies": {"express": "^4.18", "redis": "^4.0"}}))
        self.assertNotIn("no comparison on record", self.stop().stderr)

    def test_it_can_be_switched_off(self):
        self.configure(compare_dependencies=False)
        self.start_with_manifest({"express": "^4.18"})
        self.write("package.json", json.dumps({"name": "app", "dependencies": {"express": "^4.18", "redis": "^4.0"}}))
        self.assertNotIn("no comparison on record", self.stop().stderr)


class TestCLI(OptionCase):
    def test_add_then_list(self):
        add = self.cli(
            "add", "cache layer", "--metric", "latency", "--metric", "ops",
            "--option", "redis:9,3", "--option", "dict-ttl:7,9",
            "--chosen", "dict-ttl", "--why", "200 rpm needs no second service",
        )
        self.assertEqual(add.returncode, 0, add.stderr)
        self.assertIn("dict-ttl", self.cli("list").stdout)

    def test_a_score_count_mismatch_is_refused(self):
        proc = self.cli(
            "add", "cache", "--metric", "latency", "--metric", "ops",
            "--option", "redis:9", "--chosen", "redis",
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("score", proc.stderr)


if __name__ == "__main__":
    unittest.main()
