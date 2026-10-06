"""When a job is due: "every 6h", "daily 04:00", "weekly sun 18:00".

A schedule produces discrete slots. A slot is the moment a run was meant to happen, and the run
ledger stores at most one run per (scope, job, slot); that is what makes a run happen once even when
the PC was off, rebooted, or two ticks raced.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .util import parse_duration

WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
EPOCH = datetime(2000, 1, 3, tzinfo=timezone.utc)  # a Monday, 00:00 UTC
MAX_SLOTS = 2000

_DAILY = re.compile(r"^daily\s+(\d{1,2}):(\d{2})$")
_WEEKLY = re.compile(r"^weekly\s+(mon|tue|wed|thu|fri|sat|sun)\s+(\d{1,2}):(\d{2})$")
_EVERY = re.compile(r"^every\s+(\S+)$")


@dataclass(frozen=True)
class Schedule:
    text: str
    kind: str  # "every", "daily" or "weekly"
    interval: timedelta | None = None
    at: time | None = None
    weekday: int | None = None

    @property
    def period(self) -> timedelta:
        if self.kind == "every":
            assert self.interval is not None
            return self.interval
        return timedelta(days=1) if self.kind == "daily" else timedelta(days=7)

    def slots(self, after: datetime, until: datetime, tz: ZoneInfo) -> list[datetime]:
        """Every slot s with after < s <= until, oldest first, as UTC datetimes."""
        if until <= after:
            return []
        if self.kind == "every":
            return self._every_slots(after, until)
        return self._calendar_slots(after, until, tz)

    def latest(self, until: datetime, tz: ZoneInfo) -> datetime:
        """The most recent slot at or before `until`."""
        found = self.slots(until - self.period - timedelta(days=2), until, tz)
        return found[-1]

    def _every_slots(self, after: datetime, until: datetime) -> list[datetime]:
        assert self.interval is not None
        step = int(self.interval.total_seconds())
        first = (int((after - EPOCH).total_seconds()) // step + 1) * step
        last = int((until - EPOCH).total_seconds()) // step * step
        if last < first:
            return []
        if (last - first) // step >= MAX_SLOTS:
            first = last - (MAX_SLOTS - 1) * step
        return [EPOCH + timedelta(seconds=s) for s in range(first, last + 1, step)]

    def _calendar_slots(self, after: datetime, until: datetime, tz: ZoneInfo) -> list[datetime]:
        assert self.at is not None
        start_day = after.astimezone(tz).date() - timedelta(days=1)
        end_day = until.astimezone(tz).date() + timedelta(days=1)
        if (end_day - start_day).days > MAX_SLOTS * (7 if self.kind == "weekly" else 1):
            start_day = end_day - timedelta(days=MAX_SLOTS * (7 if self.kind == "weekly" else 1))
        found: list[datetime] = []
        day: date = start_day
        while day <= end_day:
            if self.kind == "daily" or day.weekday() == self.weekday:
                local = datetime.combine(day, self.at, tzinfo=tz)
                moment = local.astimezone(timezone.utc)
                if after < moment <= until:
                    found.append(moment)
            day += timedelta(days=1)
        return found


def parse_schedule(text: str) -> Schedule:
    clean = " ".join(str(text).lower().split())
    if match := _EVERY.match(clean):
        interval = parse_duration(match.group(1))
        if interval < timedelta(minutes=5):
            raise ValueError(f"schedule {text!r}: shortest interval is 5m")
        return Schedule(text=clean, kind="every", interval=interval)
    if match := _DAILY.match(clean):
        return Schedule(text=clean, kind="daily", at=_clock(match.group(1), match.group(2), text))
    if match := _WEEKLY.match(clean):
        at = _clock(match.group(2), match.group(3), text)
        return Schedule(text=clean, kind="weekly", at=at, weekday=WEEKDAYS[match.group(1)])
    raise ValueError(f"not a schedule: {text!r} (use 'every 6h', 'daily 04:00' or 'weekly sun 18:00')")


def _clock(hour: str, minute: str, text: str) -> time:
    h, m = int(hour), int(minute)
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(f"schedule {text!r}: no such time of day")
    return time(h, m)
