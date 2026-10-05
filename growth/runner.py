"""The scheduler: one `tick` decides which job slots are due and runs each of them at most once.

A systemd user timer (or cron) calls `growth tick` every few minutes. Each tick:

1. takes the engine lock, so only one process ever runs jobs (a second tick just exits);
2. marks runs left "running" by a process that died (power cut, reboot) as interrupted;
3. for every enabled job, lists the slots due since its last recorded slot, applies the catch-up
   policy (run only the latest missed slot, all of them, or none), and records slots that are too
   late as missed;
4. claims each slot in the ledger (UNIQUE per scope, job and slot) and runs the job only if the
   claim succeeded. Failed or interrupted idempotent jobs are retried on later ticks while attempts
   and lateness allow; other jobs are never run twice for one slot.
"""

from __future__ import annotations

import fcntl
import logging
import os
import traceback
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

from .ai import AIUnavailable, ClaudeRunner
from .budget import Budget
from .config import ENGINE_SCOPE, Engine, JobSpec, Project
from .jobs import JOB_KINDS
from .policy import Action, PolicyViolation, check_action
from .store import FAILED, INTERRUPTED, OK, Store
from .util import iso, parse_iso, utcnow

log = logging.getLogger("growth")


class EngineBusy(Exception):
    pass


class JobDeferred(Exception):
    """The job could not start now (e.g. no AI budget); its slot stays due."""


@contextmanager
def engine_lock(state_dir: Path) -> Iterator[None]:
    state_dir.mkdir(parents=True, exist_ok=True)
    handle = open(state_dir / "engine.lock", "w")
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise EngineBusy("another growth process holds the engine lock") from None
        handle.write(str(os.getpid()))
        handle.flush()
        yield
    finally:
        handle.close()


@dataclass
class JobContext:
    engine: Engine
    project: Project | None
    job: JobSpec
    store: Store
    budget: Budget
    slot: datetime
    now: datetime
    notes: list[str] = field(default_factory=list)

    @property
    def scope(self) -> str:
        return self.project.id if self.project else ENGINE_SCOPE

    @property
    def state_dir(self) -> Path:
        path = self.engine.state_dir / ("projects/" + self.project.id if self.project else "engine")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def ai(self) -> ClaudeRunner:
        workdir = self.engine.state_dir / "ai-workdir"
        workdir.mkdir(parents=True, exist_ok=True)
        return ClaudeRunner(self.engine.ai, self.store, self.budget, self.scope, self.job.id, str(workdir), now=self.now)

    def check(self, action: Action) -> None:
        """Raise PolicyViolation (and log it as blocked) if the action crosses a hard line."""
        rules = self.project.rules if self.project else _no_rules()
        try:
            check_action(action, rules, self.store.text_communities)
        except PolicyViolation as exc:
            self.store.record_block(self.scope, self.job.id, exc.rule, exc.message)
            raise

    def act(self, action: Action, key: str, do: "callable", *, retry_failed: bool = False) -> str:
        """Do an outward action at most once per key, after the policy check.

        Returns "done", "already" (done before), or "unknown" (an earlier attempt never reported
        back, so it is not repeated). Exceptions from `do` mark the effect failed and propagate.
        """
        self.check(action)
        state = self.store.effect_begin(self.scope, key, action.channel, action.kind, retry_failed=retry_failed)
        if state == "done":
            return "already"
        if state == "pending":
            self.notes.append(f"{key}: an earlier attempt never finished; not repeated")
            return "unknown"
        if state == "failed":
            return "failed-before"
        try:
            detail = do()
        except Exception as exc:
            self.store.effect_end(self.scope, key, "failed", str(exc))
            raise
        self.store.effect_end(self.scope, key, "done", str(detail or ""))
        if action.text and action.community:
            from .policy import fingerprint

            self.store.record_text(fingerprint(action.text), action.community, self.scope, action.channel)
        return "done"


def _no_rules():
    from .policy import ProjectRules

    return ProjectRules()


@dataclass
class TickReport:
    ran: list[str] = field(default_factory=list)
    missed: list[str] = field(default_factory=list)
    deferred: list[str] = field(default_factory=list)
    interrupted: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        out = [f"ran {r}" for r in self.ran]
        out += [f"missed {m}" for m in self.missed]
        out += [f"deferred {d}" for d in self.deferred]
        out += [f"interrupted {i}" for i in self.interrupted]
        return out or ["nothing due"]


def all_jobs(engine: Engine) -> list[tuple[Project | None, JobSpec]]:
    jobs: list[tuple[Project | None, JobSpec]] = [(None, j) for j in engine.jobs.values()]
    for project in engine.projects.values():
        jobs += [(project, j) for j in project.jobs.values()]
    return [(p, j) for p, j in jobs if j.enabled]


def tick(engine: Engine, *, now: datetime | None = None, store: Store | None = None) -> TickReport:
    now = now or utcnow()
    report = TickReport()
    with engine_lock(engine.state_dir):
        own_store = store is None
        store = store or Store(engine.state_dir / "engine.db")
        try:
            for run in store.mark_stale_running():
                report.interrupted.append(f"{run.scope}/{run.job}@{run.slot}")
            budget = Budget(engine.ai, store, engine.tz)
            for project, job in all_jobs(engine):
                _tick_job(engine, store, budget, project, job, now, report)
        finally:
            if own_store:
                store.close()
    return report


def _tick_job(
    engine: Engine,
    store: Store,
    budget: Budget,
    project: Project | None,
    job: JobSpec,
    now: datetime,
    report: TickReport,
) -> None:
    scope = project.id if project else ENGINE_SCOPE
    tz = project.tz if project else engine.tz
    kind = JOB_KINDS[job.kind]
    last = store.last_slot(scope, job.id)
    if last is None:
        # A new job starts with its latest slot; it never backfills history.
        due = [job.schedule.latest(now, tz)]
    else:
        due = job.schedule.slots(parse_iso(last), now, tz)
    retry = (FAILED, INTERRUPTED) if kind.idempotent else ()
    # Earlier slots may still be retried (an idempotent job that failed last tick).
    if last is not None and retry:
        prev = store.run(scope, job.id, last)
        if prev and prev.status in retry and prev.attempts < job.max_attempts:
            due = [parse_iso(last), *due]

    if job.catchup == "latest" and len(due) > 1:
        for slot in due[:-1]:
            if store.mark_missed(scope, job.id, iso(slot), "superseded by a later slot (catch-up: latest)"):
                report.missed.append(f"{scope}/{job.id}@{iso(slot)}")
        due = due[-1:]

    for slot in due:
        label = f"{scope}/{job.id}@{iso(slot)}"
        if now - slot > job.max_late or (job.catchup == "skip" and now - slot > timedelta(minutes=15)):
            reason = f"too late to run ({now - slot} after its slot)"
            if kind.ai:
                why = budget.blocked_reason(now, scope)
                reason += f"; AI was unavailable: {why}" if why else ""
            existing = store.run(scope, job.id, iso(slot))
            if existing is None and store.mark_missed(scope, job.id, iso(slot), reason):
                report.missed.append(label)
            continue
        if kind.ai:
            why = budget.blocked_reason(now, scope)
            if why:
                report.deferred.append(f"{label}: {why}")
                continue
        if not store.claim(scope, job.id, iso(slot), retry=retry, max_attempts=job.max_attempts):
            continue
        _execute(engine, store, budget, project, job, slot, now)
        report.ran.append(label)


def run_now(engine: Engine, scope: str, job_id: str, *, now: datetime | None = None) -> str:
    """Run one job outside its schedule (manual), still under the lock and ledger. Returns its status."""
    now = now or utcnow()
    project = engine.projects.get(scope) if scope != ENGINE_SCOPE else None
    if scope != ENGINE_SCOPE and project is None:
        raise KeyError(f"no enabled project {scope!r}")
    jobs = project.jobs if project else engine.jobs
    if job_id not in jobs:
        raise KeyError(f"{scope} has no job {job_id!r}")
    job = jobs[job_id]
    slot = now.replace(microsecond=0)
    with engine_lock(engine.state_dir):
        store = Store(engine.state_dir / "engine.db")
        try:
            store.mark_stale_running()
            budget = Budget(engine.ai, store, engine.tz)
            key = "manual:" + iso(slot)
            if not store.claim(scope, job.id, key):
                return "already ran"
            return _execute(engine, store, budget, project, job, slot, now, slot_key=key)
        finally:
            store.close()


def _execute(
    engine: Engine,
    store: Store,
    budget: Budget,
    project: Project | None,
    job: JobSpec,
    slot: datetime,
    now: datetime,
    *,
    slot_key: str | None = None,
) -> str:
    scope = project.id if project else ENGINE_SCOPE
    key = slot_key or iso(slot)
    ctx = JobContext(engine=engine, project=project, job=job, store=store, budget=budget, slot=slot, now=now)
    func = JOB_KINDS[job.kind].resolve()
    try:
        summary = func(ctx) or "done"
        if ctx.notes:
            summary += " | " + "; ".join(ctx.notes)
        store.finish(scope, job.id, key, OK, summary)
        log.info("%s/%s ok: %s", scope, job.id, summary)
        return OK
    except AIUnavailable as exc:
        store.finish(scope, job.id, key, FAILED, f"AI unavailable: {exc}")
        return FAILED
    except PolicyViolation as exc:
        store.finish(scope, job.id, key, FAILED, f"blocked by policy: {exc}")
        log.warning("%s/%s blocked: %s", scope, job.id, exc)
        return FAILED
    except Exception as exc:  # a job failure must never stop the other jobs
        store.finish(scope, job.id, key, FAILED, f"{type(exc).__name__}: {exc}")
        log.error("%s/%s failed:\n%s", scope, job.id, traceback.format_exc())
        return FAILED
