"""Keep AI jobs inside the Claude subscription's usage.

The subscription has no usage API, so the engine budgets conservatively on its own: a cap on
headless runs per rolling day and week, an optional per-project weekly share, a time window (by
default the night, when the owner is not using Claude), and a cooldown after Claude reports a usage
limit. An AI job that has no budget is not started; its slot stays due and is retried on later
ticks until it is too late, then it is recorded as missed with the reason.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from .config import AIConfig
from .store import Store
from .util import iso, parse_iso

COOLDOWN_KEY = "ai.cooldown_until"


class Budget:
    def __init__(self, cfg: AIConfig, store: Store, tz: ZoneInfo):
        self.cfg = cfg
        self.store = store
        self.tz = tz

    def blocked_reason(self, now: datetime, scope: str | None = None, runs: int = 1) -> str | None:
        """None when `runs` more AI runs may start now; otherwise why not."""
        until = self.store.get(COOLDOWN_KEY)
        if until and parse_iso(until) > now:
            return f"Claude reported a usage limit; cooling down until {until}"
        if self.cfg.window and not _in_window(now.astimezone(self.tz).time(), *self.cfg.window):
            start, end = self.cfg.window
            return f"outside the AI window {start:%H:%M}-{end:%H:%M}"
        day = self.store.ai_runs_since(now - timedelta(days=1))
        if day + runs > self.cfg.max_runs_per_day:
            return f"daily AI budget used ({day}/{self.cfg.max_runs_per_day} runs in 24 h)"
        week = self.store.ai_runs_since(now - timedelta(days=7))
        if week + runs > self.cfg.max_runs_per_week:
            return f"weekly AI budget used ({week}/{self.cfg.max_runs_per_week} runs in 7 days)"
        if scope is not None and self.cfg.per_project_runs_per_week is not None:
            mine = self.store.ai_runs_since(now - timedelta(days=7), scope)
            if mine + runs > self.cfg.per_project_runs_per_week:
                return f"{scope}'s weekly AI share used ({mine}/{self.cfg.per_project_runs_per_week})"
        return None

    def start_cooldown(self, now: datetime) -> str:
        until = iso(now + self.cfg.cooldown)
        self.store.put(COOLDOWN_KEY, until)
        return until


def _in_window(now: time, start: time, end: time) -> bool:
    if start == end:
        return True
    if start < end:
        return start <= now < end
    return now >= start or now < end  # wraps midnight, e.g. 22:00-06:00
