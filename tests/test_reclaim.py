"""A claim survives the process dying under it.

Issue #131. The session id is stable across a restart and the pid is not, so a sibling's
reaper releases the claim of a session that is, from its own point of view, still working.
It came back owning nothing, the first Stop refused the turn for having no task on the
board, and the demand it printed named files belonging to somebody else and suggested
filing a NEW task — which is how a board grows duplicates of work already on it.
"""

from __future__ import annotations

import unittest

from helpers import RepoCase, session_record_for, sid

from claude_bestpractice import plan, sessions, store


class RestartCase(RepoCase):
    def start(self, session_id: str = "worker"):
        return self.run_hook(
            "session-start", {"session_id": session_id, "hook_event_name": "SessionStart"}
        )

    def held_then_lost(self, title: str = "wire up the exporter"):
        """A session that claimed work, died, and was reaped by a sibling.

        The pid is set to 1 rather than to something absent: it is alive and is not this
        process, so the record fails on the FINGERPRINT the way a recycled pid does, which
        is the case the reaper is actually built around.
        """
        ctx = self.ctx()
        me = sid(self.repo, "worker")
        self.start("worker")
        task = plan.add(ctx, title)
        plan.claim(ctx, task.id, me, ctx.branch)

        sessions.touch(ctx, me, pid=1, pid_fingerprint="not-the-process-that-was")
        sibling = sid(self.repo, "sibling")
        sessions.register(ctx, session_record_for(ctx, sibling))
        sessions.reap(ctx, exclude=sibling)
        return ctx, me, task


class TestAClaimSurvivesARestart(RestartCase):
    def test_the_task_comes_back_to_the_session_that_held_it(self):
        ctx, me, task = self.held_then_lost()
        self.assertEqual(plan.NEXT, plan.find(ctx, task.id).state, "the reaper should free it")

        self.start("worker")
        back = plan.find(ctx, task.id)
        self.assertEqual(plan.DOING, back.state)
        self.assertEqual(me, back.owner)

    def test_the_board_says_so_rather_than_leaving_it_to_be_noticed(self):
        ctx, _me, task = self.held_then_lost()
        said = self.start("worker").stdout
        self.assertIn("reclaimed", said)
        self.assertIn(task.id, said)

    def test_work_somebody_else_picked_up_is_theirs(self):
        """The line this must not cross. Taking back what is still free is the fix; taking
        back what a live sibling has since claimed would make a returning session a thief.

        The refusal comes from `claim` itself, which is why `reclaim` goes through it
        rather than moving the file — a stricter check written beside it was tried, and it
        got the dead-holder case wrong.
        """
        ctx, _me, task = self.held_then_lost()
        other = sid(self.repo, "sibling")
        plan.claim(ctx, task.id, other, ctx.branch)

        self.start("worker")
        self.assertEqual(other, plan.find(ctx, task.id).owner)

    def test_a_session_that_never_lost_anything_takes_nothing(self):
        ctx, _me, task = self.held_then_lost()
        stranger = sid(self.repo, "stranger")

        self.start("stranger")
        self.assertNotEqual(stranger, plan.find(ctx, task.id).owner)

    def test_the_release_is_spent_rather_than_kept(self):
        """Asserted on the stored table, not on a second call's answer.

        A second `reclaim` returns nothing either way — the task is in `doing` by then — so
        that would be the weaker assertion. What needs proving is that the memory is gone,
        or a task legitimately re-planned weeks later gets pulled back into a session that
        has long since moved on.
        """
        ctx, me, task = self.held_then_lost()

        self.assertEqual([task.id], plan.reclaim(ctx, me))
        self.assertEqual({}, store.read_json(store.tier_b(ctx, plan.RELEASED_FILE), default={}))


if __name__ == "__main__":
    unittest.main()
