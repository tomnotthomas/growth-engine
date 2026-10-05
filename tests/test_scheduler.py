"""The scheduler's guarantees: a slot runs at most once, missed time is caught up by policy,
interrupted side effects are never repeated, and AI jobs stay inside the budget."""

from __future__ import annotations

import multiprocessing
import os
import threading
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from growth.config import load_engine
from growth.policy import Action
from growth.runner import EngineBusy, JobContext, engine_lock, run_now, tick
from growth.schedule import parse_schedule
from growth.store import FAILED, INTERRUPTED, MISSED, OK, Store
from growth.util import iso

from .helpers import NOW, HomeTestCase

BERLIN = ZoneInfo("Europe/Berlin")
CALLS: list[str] = []


def count_job(ctx: JobContext) -> str:
    CALLS.append(f"{ctx.scope}/{ctx.job.id}@{iso(ctx.slot)}")
    return "counted"


def failing_job(ctx: JobContext) -> str:
    CALLS.append("fail")
    raise RuntimeError("boom")


def _tick_in_process(home: str, now: str, out: str) -> None:
    import growth.jobs as jobs

    engine = load_engine(Path(home), state_dir=Path(home) / "state", dist_dir=Path(home) / "dist")
    _patch_kinds(jobs)
    try:
        report = tick(engine, now=datetime.fromisoformat(now))
        ran = report.ran
    except EngineBusy:
        ran = []
    with open(out, "a") as handle:
        for line in ran:
            handle.write(line + "\n")


def _patch_kinds(jobs_module) -> None:
    from dataclasses import replace

    for name in ("fetch-data", "build-site"):
        jobs_module.JOB_KINDS[name] = replace(jobs_module.JOB_KINDS[name], target="tests.test_scheduler:count_job")


class ScheduleSlots(HomeTestCase):
    def test_daily_slots_follow_local_time_across_dst(self) -> None:
        schedule = parse_schedule("daily 04:00")
        start = datetime(2026, 10, 24, 0, 0, tzinfo=timezone.utc)
        slots = schedule.slots(start, start + timedelta(days=3), BERLIN)
        self.assertEqual([s.astimezone(BERLIN).hour for s in slots], [4, 4, 4])
        self.assertEqual([s.hour for s in slots], [2, 3, 3])  # CEST on the 24th, CET from 25 Oct

    def test_weekly_and_interval_slots(self) -> None:
        weekly = parse_schedule("weekly mon 05:30")
        slot = weekly.latest(NOW, BERLIN)
        self.assertEqual(slot.astimezone(BERLIN).weekday(), 0)
        self.assertLessEqual(slot, NOW)
        every = parse_schedule("every 6h")
        self.assertEqual(len(every.slots(NOW - timedelta(days=1), NOW, BERLIN)), 4)

    def test_rejects_nonsense(self) -> None:
        for text in ("hourly", "daily 25:00", "weekly funday 10:00", "every 2m"):
            with self.assertRaises(ValueError):
                parse_schedule(text)


class NoDoubleRun(HomeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.only_core_jobs()
        CALLS.clear()
        import growth.jobs as jobs

        self.saved = dict(jobs.JOB_KINDS)
        _patch_kinds(jobs)
        self.addCleanup(lambda: jobs.JOB_KINDS.update(self.saved))

    def test_the_same_tick_twice_runs_each_slot_once(self) -> None:
        engine = self.engine()
        first = tick(engine, now=NOW)
        second = tick(engine, now=NOW)
        third = tick(engine, now=NOW + timedelta(minutes=5))
        self.assertEqual(len(CALLS), 2)  # fetch-data and build-site; the digest waits for its Monday slot
        self.assertEqual(len(first.ran), 2)
        self.assertEqual(second.ran, [])
        self.assertEqual(third.ran, [])

    def test_concurrent_ticks_in_separate_processes_run_each_slot_once(self) -> None:
        self.engine()  # validate the home first
        out = self.tmp / "ran.txt"
        ctx = multiprocessing.get_context("spawn")
        procs = [ctx.Process(target=_tick_in_process, args=(str(self.home), NOW.isoformat(), str(out))) for _ in range(4)]
        for proc in procs:
            proc.start()
        for proc in procs:
            proc.join(60)
        lines = out.read_text().splitlines() if out.exists() else []
        self.assertEqual(len(lines), 2, lines)
        self.assertEqual(len(set(lines)), 2)

    def test_claim_is_atomic_across_threads(self) -> None:
        engine = self.engine()
        store_path = engine.state_dir / "engine.db"
        Store(store_path).close()
        wins: list[bool] = []

        def claim() -> None:
            store = Store(store_path)
            wins.append(store.claim("example", "build-site", "2026-10-05T02:00:00Z"))
            store.close()

        threads = [threading.Thread(target=claim) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(wins.count(True), 1)

    def test_pc_off_for_three_days_catches_up_once(self) -> None:
        engine = self.engine()
        tick(engine, now=NOW)
        CALLS.clear()
        report = tick(engine, now=NOW + timedelta(days=3, hours=1))
        self.assertEqual(sorted(c.split("@")[0] for c in CALLS), ["example/build-site", "example/fetch-data"])
        store = self.store(engine)
        missed = [r for r in store.runs_since(NOW) if r.status == MISSED]
        self.assertTrue(any(r.job == "fetch-data" for r in missed))
        self.assertTrue(report.missed)

    def test_catchup_all_runs_every_missed_slot_within_max_late(self) -> None:
        self.edit("projects/example/project.toml", 'schedule = "every 6h"', 'schedule = "every 6h"\ncatchup = "all"\nmax_late = "1d"')
        engine = self.engine()
        tick(engine, now=NOW)
        CALLS.clear()
        tick(engine, now=NOW + timedelta(hours=18))
        self.assertEqual(sum(1 for c in CALLS if "fetch-data" in c), 3)

    def test_slots_too_late_are_recorded_as_missed(self) -> None:
        self.edit("projects/example/project.toml", 'schedule = "every 6h"', 'schedule = "every 6h"\ncatchup = "all"\nmax_late = "7h"')
        engine = self.engine()
        tick(engine, now=NOW)
        CALLS.clear()
        tick(engine, now=NOW + timedelta(hours=24))
        store = self.store(engine)
        statuses = sorted(r.status for r in store.runs_since(NOW) if r.job == "fetch-data")
        self.assertIn(MISSED, statuses)
        self.assertEqual(sum(1 for c in CALLS if "fetch-data" in c), 1)  # only the 00:00 slot is under 7 h late

    def test_new_job_does_not_backfill_history(self) -> None:
        engine = self.engine()
        tick(engine, now=NOW)
        self.assertEqual(sum(1 for c in CALLS if "build-site" in c), 1)


class Interruptions(HomeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.only_core_jobs()
        CALLS.clear()
        import growth.jobs as jobs

        self.saved = dict(jobs.JOB_KINDS)
        self.addCleanup(lambda: jobs.JOB_KINDS.update(self.saved))
        self.jobs = jobs

    def _kind(self, name: str, **changes) -> None:
        from dataclasses import replace

        self.jobs.JOB_KINDS[name] = replace(self.jobs.JOB_KINDS[name], **changes)

    def test_a_run_cut_off_by_a_reboot_is_interrupted_and_not_repeated(self) -> None:
        self._kind("fetch-data", target="tests.test_scheduler:count_job", idempotent=False)
        self._kind("build-site", target="tests.test_scheduler:count_job")
        engine = self.engine()
        slot = parse_schedule("every 6h").latest(NOW, BERLIN)
        store = self.store(engine)
        self.assertTrue(store.claim("example", "fetch-data", iso(slot)))  # the PC dies right here
        report = tick(engine, now=NOW, store=store)
        self.assertIn(f"example/fetch-data@{iso(slot)}", report.interrupted)
        self.assertEqual(store.run("example", "fetch-data", iso(slot)).status, INTERRUPTED)
        self.assertFalse(any("fetch-data" in c for c in CALLS))

    def test_an_idempotent_job_is_retried_after_failing(self) -> None:
        self._kind("fetch-data", target="tests.test_scheduler:failing_job")
        self._kind("build-site", target="tests.test_scheduler:count_job")
        engine = self.engine()
        for minutes in (0, 5, 10, 15, 20):
            tick(engine, now=NOW + timedelta(minutes=minutes))
        self.assertEqual(CALLS.count("fail"), 3)  # max_attempts
        store = self.store(engine)
        slot = parse_schedule("every 6h").latest(NOW, BERLIN)
        run = store.run("example", "fetch-data", iso(slot))
        self.assertEqual((run.status, run.attempts), (FAILED, 3))

    def test_a_non_idempotent_job_is_not_retried(self) -> None:
        self._kind("fetch-data", target="tests.test_scheduler:failing_job", idempotent=False)
        self._kind("build-site", target="tests.test_scheduler:count_job")
        engine = self.engine()
        for minutes in (0, 5, 10):
            tick(engine, now=NOW + timedelta(minutes=minutes))
        self.assertEqual(CALLS.count("fail"), 1)

    def test_a_second_process_never_runs_jobs_while_one_holds_the_lock(self) -> None:
        engine = self.engine()
        with engine_lock(engine.state_dir):
            with self.assertRaises(EngineBusy):
                tick(engine, now=NOW)

    def test_manual_runs_do_not_disturb_the_schedule(self) -> None:
        self._kind("fetch-data", target="tests.test_scheduler:count_job")
        self._kind("build-site", target="tests.test_scheduler:count_job")
        engine = self.engine()
        self.assertEqual(run_now(engine, "example", "fetch-data", now=NOW), OK)
        report = tick(engine, now=NOW)
        self.assertEqual(len(report.ran), 2)


class SideEffects(HomeTestCase):
    def _ctx(self) -> JobContext:
        from growth.budget import Budget

        engine = self.engine()
        store = self.store(engine)
        project = engine.projects["example"]
        return JobContext(
            engine=engine,
            project=project,
            job=project.jobs["build-site"],
            store=store,
            budget=Budget(engine.ai, store, engine.tz),
            slot=NOW,
            now=NOW,
        )

    def test_an_effect_happens_once_per_key(self) -> None:
        ctx = self._ctx()
        done: list[int] = []
        action = Action(channel="indexnow", kind="ping", url="https://api.indexnow.org/indexnow")
        self.assertEqual(ctx.act(action, "ping:/a:1", lambda: done.append(1)), "done")
        self.assertEqual(ctx.act(action, "ping:/a:1", lambda: done.append(1)), "already")
        self.assertEqual(done, [1])

    def test_an_effect_with_unknown_outcome_is_never_repeated(self) -> None:
        ctx = self._ctx()
        ctx.store.effect_begin("example", "ping:/b:1", "indexnow", "ping", retry_failed=True)  # power cut after this
        done: list[int] = []
        action = Action(channel="indexnow", kind="ping", url="https://api.indexnow.org/indexnow")
        self.assertEqual(ctx.act(action, "ping:/b:1", lambda: done.append(1)), "unknown")
        self.assertEqual(done, [])
        self.assertTrue(ctx.notes)

    def test_a_blocked_action_is_logged_and_not_done(self) -> None:
        from growth.policy import PolicyViolation

        ctx = self._ctx()
        done: list[int] = []
        with self.assertRaises(PolicyViolation):
            ctx.act(Action(channel="reddit", kind="post", text="hi", community="r/test"), "post:1", lambda: done.append(1))
        self.assertEqual(done, [])
        self.assertEqual(ctx.store.blocks_since(NOW - timedelta(days=1))[0]["rule"], "no-scripted-community-posting")


class AIBudget(HomeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.only_core_jobs()
        self.monday = datetime(2026, 10, 5, 3, 31, tzinfo=timezone.utc)  # 05:31 Berlin, just after the digest slot

    def test_digest_waits_outside_the_ai_window(self) -> None:
        engine = self.engine(window=(time(1, 0), time(2, 0)))
        report = tick(engine, now=self.monday)
        self.assertTrue(any("weekly-digest" in d and "outside the AI window" in d for d in report.deferred))
        self.assertFalse(any("weekly-digest" in r for r in report.ran))

    def test_digest_runs_once_inside_budget(self) -> None:
        engine = self.engine()
        log = self.tmp / "claude.log"
        with mock.patch.dict(os.environ, {"FAKE_CLAUDE_LOG": str(log), "ANTHROPIC_API_KEY": "sk-must-not-leak"}):
            first = tick(engine, now=self.monday)
            second = tick(engine, now=self.monday + timedelta(minutes=5))
        self.assertTrue(any("weekly-digest" in r for r in first.ran))
        self.assertFalse(any("weekly-digest" in r for r in second.ran))
        calls = log.read_text().splitlines()
        self.assertEqual(len(calls), 1)
        self.assertIn('"keys": []', calls[0])  # no API key reached Claude
        self.assertNotIn("--bare", calls[0])

    def test_daily_cap_defers_then_misses(self) -> None:
        engine = self.engine(max_runs_per_day=0)
        report = tick(engine, now=self.monday)
        self.assertTrue(any("daily AI budget used" in d for d in report.deferred))
        later = tick(engine, now=self.monday + timedelta(days=3))
        self.assertTrue(any("weekly-digest" in m for m in later.missed))
        store = self.store(engine)
        run = store.run("engine", "weekly-digest", iso(parse_schedule("weekly mon 05:30").latest(self.monday, BERLIN)))
        self.assertEqual(run.status, MISSED)
        self.assertIn("AI was unavailable", run.summary)

    def test_usage_limit_starts_a_cooldown(self) -> None:
        engine = self.engine()
        with mock.patch.dict(os.environ, {"FAKE_CLAUDE_MODE": "limit"}):
            tick(engine, now=self.monday)
        store = self.store(engine)
        self.assertIsNotNone(store.get("ai.cooldown_until"))
        from growth.budget import Budget

        reason = Budget(engine.ai, store, engine.tz).blocked_reason(datetime.now(timezone.utc))
        self.assertIn("cooling down", reason or "")

