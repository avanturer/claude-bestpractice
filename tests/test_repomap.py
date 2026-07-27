"""The ranked repository map: extraction, graph, ranking, budget fitting."""

from __future__ import annotations

import unittest

from helpers import RepoCase

from claude_bestpractice import repomap


class TestExtraction(unittest.TestCase):
    def test_python_uses_the_ast(self):
        defines, references = repomap.extract_python(
            "import os\n\ndef handler(x):\n    return os.path.join(x)\n\nclass Runner:\n    pass\n"
        )
        self.assertEqual(defines, {"handler", "Runner"})
        self.assertIn("join", references)

    def test_python_falls_back_on_a_syntax_error(self):
        defines, _ = repomap.extract_python("def broken(:\nfunction other() {}\n")
        self.assertIn("other", defines)

    def test_generic_finds_js_and_ts_forms(self):
        defines, _ = repomap.extract_generic(
            "export function alpha() {}\nexport class Beta {}\n"
            "export const gamma = (x) => x\ninterface Delta {}\n"
        )
        self.assertEqual(defines, {"alpha", "Beta", "gamma", "Delta"})

    def test_generic_finds_go_and_rust_forms(self):
        defines, _ = repomap.extract_generic("func Serve() {}\npub fn parse() {}\nstruct Config {}\n")
        self.assertTrue({"Serve", "parse", "Config"} <= defines)

    def test_stopwords_are_not_references(self):
        _, references = repomap.extract_generic("const value = true; return value;")
        self.assertNotIn("return", references)
        self.assertNotIn("true", references)


class TestGraph(RepoCase):
    def facts(self) -> list[repomap.FileFacts]:
        return repomap.scan(self.ctx())

    def test_reference_creates_an_edge(self):
        self.write("core.py", "def compute_total():\n    return 1\n")
        self.write("api.py", "from core import compute_total\n\ndef handler():\n    return compute_total()\n")
        graph = repomap.build_graph(self.facts())
        self.assertIn("core.py", graph.get("api.py", {}))

    def test_no_edge_without_a_reference(self):
        self.write("a.py", "def alpha():\n    return 1\n")
        self.write("b.py", "def beta():\n    return 2\n")
        graph = repomap.build_graph(self.facts())
        self.assertEqual(graph.get("a.py"), {})
        self.assertEqual(graph.get("b.py"), {})

    def test_a_symbol_defined_everywhere_creates_no_edges(self):
        """Otherwise a common name connects every file to every other and ranking dies."""
        for i in range(8):
            self.write(f"m{i}.py", "def run():\n    return 1\n")
        self.write("caller.py", "def go():\n    return run()\n")
        graph = repomap.build_graph(self.facts())
        self.assertEqual(graph.get("caller.py"), {})

    def test_no_self_edges(self):
        self.write("solo.py", "def alpha():\n    return alpha()\n")
        self.assertEqual(repomap.build_graph(self.facts()).get("solo.py"), {})


class TestPageRank(unittest.TestCase):
    def test_a_referenced_file_outranks_its_referrers(self):
        graph = {"core.py": {}, "a.py": {"core.py": 1.0}, "b.py": {"core.py": 1.0}}
        ranks = repomap.pagerank(graph)
        self.assertGreater(ranks["core.py"], ranks["a.py"])

    def test_ranks_sum_to_one(self):
        """Dangling nodes must redistribute, or rank leaks out of the graph entirely."""
        graph = {"a.py": {"b.py": 1.0}, "b.py": {}, "c.py": {}}
        self.assertAlmostEqual(sum(repomap.pagerank(graph).values()), 1.0, places=5)

    def test_personalization_shifts_the_ranking(self):
        graph = {"a.py": {}, "b.py": {}}
        biased = repomap.pagerank(graph, {"b.py": 10.0})
        self.assertGreater(biased["b.py"], biased["a.py"])

    def test_empty_graph_is_safe(self):
        self.assertEqual(repomap.pagerank({}), {})


class TestPersonalization(RepoCase):
    def test_query_terms_bias_toward_matching_files(self):
        self.write("billing.py", "def charge():\n    return 1\n")
        self.write("unrelated.py", "def other():\n    return 2\n")
        facts = repomap.scan(self.ctx())
        weights = repomap.personalize(facts, "fix the billing charge path")
        self.assertGreater(weights.get("billing.py", 0), weights.get("unrelated.py", 0))

    def test_empty_query_yields_no_bias(self):
        self.write("a.py", "def alpha():\n    return 1\n")
        self.assertEqual(repomap.personalize(repomap.scan(self.ctx()), ""), {})


class TestRendering(RepoCase):
    def build(self, count: int = 30):
        for i in range(count):
            self.write(f"mod{i}.py", f"def fn{i}():\n    return {i}\n\nclass C{i}:\n    pass\n")
        facts = repomap.scan(self.ctx())
        ranks = repomap.pagerank(repomap.build_graph(facts))
        return facts, ranks

    def test_fits_the_budget(self):
        facts, ranks = self.build()
        for budget in (50, 200, 1_000):
            with self.subTest(budget=budget):
                rendered = repomap.render(facts, ranks, budget)
                self.assertLessEqual(len(rendered), int(budget * repomap.CHARS_PER_TOKEN))

    def test_a_larger_budget_shows_more(self):
        facts, ranks = self.build()
        self.assertGreater(len(repomap.render(facts, ranks, 1_000)), len(repomap.render(facts, ranks, 100)))

    def test_lists_symbols_per_file(self):
        facts, ranks = self.build(count=3)
        rendered = repomap.render(facts, ranks, 1_000)
        self.assertIn("mod0.py:", rendered)
        self.assertIn("fn0", rendered)

    def test_zero_budget_yields_nothing(self):
        facts, ranks = self.build(count=3)
        self.assertEqual(repomap.render(facts, ranks, 0), "")


class TestGenerate(RepoCase):
    def test_generates_and_caches(self):
        for i in range(5):
            self.write(f"m{i}.py", f"def fn{i}():\n    return {i}\n")
        ctx = self.ctx()
        first = repomap.generate(ctx, query="fn1", budget_tokens=200)
        self.assertTrue(first)
        self.assertEqual(repomap.generate(ctx, query="fn1", budget_tokens=200), first)

    def test_cache_is_keyed_by_content_not_mtime(self):
        """A fresh worktree resets every mtime; the cache must survive that."""
        import os
        import time

        for i in range(5):
            self.write(f"m{i}.py", f"def fn{i}():\n    return {i}\n")
        ctx = self.ctx()
        first = repomap.generate(ctx, budget_tokens=200)

        future = time.time() + 10_000
        for i in range(5):
            os.utime(self.repo / f"m{i}.py", (future, future))
        self.assertEqual(repomap.generate(ctx, budget_tokens=200), first)

    def test_adding_a_file_invalidates_the_cache(self):
        self.write("a.py", "def alpha():\n    return 1\n")
        ctx = self.ctx()
        first = repomap.generate(ctx, budget_tokens=400)
        self.write("b.py", "def beta():\n    return 2\n")
        self.assertNotEqual(repomap.generate(ctx, budget_tokens=400), first)

    def test_empty_repo_yields_empty_map(self):
        self.assertEqual(repomap.generate(self.ctx()), "")

    def test_skips_vendor_directories(self):
        self.write("node_modules/pkg/index.js", "export function noise() {}\n")
        self.write("real.py", "def alpha():\n    return 1\n")
        paths = {f.path for f in repomap.scan(self.ctx())}
        self.assertIn("real.py", paths)
        self.assertFalse(any(p.startswith("node_modules/") for p in paths))


if __name__ == "__main__":
    unittest.main()
