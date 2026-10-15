"""Issue #267: a suite the Stop gate starts gets its share of the machine, not the machine.

The gate ran `npm test` in `mobile/`, and jest started a worker per CPU but one: 23 on a
24-thread machine, up to 0.9 GB each, while the session itself ran nothing. Six to twelve
sessions finishing on one WSL machine took it to 4 GB of 47.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

from helpers import RepoCase

from claude_bestpractice import config, evidence, witness

AFFINITY = getattr(os, "sched_getaffinity", None)
CPUS = len(AFFINITY(0)) if AFFINITY else 0
COUNTS = [sys.executable, "-c", "import os; print(len(os.sched_getaffinity(0)))"]


@unittest.skipUnless(CPUS > 2, "needs a CPU mask to narrow, and more than two CPUs in it")
class TestASuiteTheGateStartsHasItsShareOfTheMachine(RepoCase):
    def started_on(self, cpus: int) -> int:
        proc = witness.run_bounded(COUNTS, self.repo, dict(os.environ), 30, cpus)
        return int(proc.stdout.strip())

    def test_it_sees_only_the_cpus_it_is_given(self):
        self.assertEqual(2, self.started_on(2))

    def test_zero_is_every_cpu(self):
        self.assertEqual(CPUS, self.started_on(0))

    def test_the_gate_itself_gets_its_cpus_back(self):
        before = AFFINITY(0)
        self.started_on(1)
        self.assertEqual(before, AFFINITY(0))

    def test_two_gates_take_different_cpus(self):
        """Every session's gate on the same first few CPUs would queue them all there."""
        taken = set()
        for pid in (0, 2):
            with mock.patch.object(witness.os, "getpid", return_value=pid), witness._at_most(2):
                taken.add(frozenset(AFFINITY(0)))
        self.assertEqual(2, len(taken))

    def test_the_gates_own_pytest_runs_on_the_founders_number(self):
        self.configure(witness_cpus=1)
        self.write("tests/test_cpus.py", "import os\n\n\ndef test_one_cpu():\n"
                   "    assert len(os.sched_getaffinity(0)) == 1\n")
        self.commit("a test that counts its CPUs")
        verdict = evidence._verify_by_running(self.ctx(), [], ["make", "test"], [])
        self.assertTrue(verdict.ok, verdict.reason)

    def test_the_projects_own_command_runs_on_the_founders_number(self):
        self.configure(witness_cpus=1)
        code, tail = evidence.run_suite(self.ctx(), COUNTS)
        self.assertEqual((0, "1"), (code, tail.strip()))



class TestTheShareIsTheFoundersToSet(RepoCase):
    def test_four_unless_the_founder_says_otherwise(self):
        self.assertEqual(4, witness.cpus_for(self.ctx()))
        self.configure(witness_cpus=0)
        self.assertEqual(0, witness.cpus_for(self.ctx()))

    def test_the_founder_sets_it_by_saying_it(self):
        self.assertEqual({"witness_cpus": "8"}, config.switches_in("witness_cpus 8"))

    def test_where_no_mask_can_be_set_the_run_starts_as_it_did(self):
        with mock.patch.object(witness.os, "sched_setaffinity", create=True,
                               side_effect=OSError("not here")):
            proc = witness.run_bounded([sys.executable, "-c", "print('ran')"], self.repo,
                                       dict(os.environ), 30, 1)
        self.assertEqual("ran", proc.stdout.strip())


if __name__ == "__main__":
    unittest.main()
