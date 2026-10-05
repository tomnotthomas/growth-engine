"""The goal tracker: confirmed sign-ups per day against a target, by source, and the referral k-factor.

Input is the waitlist Worker's /api/waitlist/stats answer. Definitions:

- confirmed sign-ups count on the day they confirmed (double opt-in), not when they typed the address;
- the plan line is linear from the start date to the deadline;
- needed per day = what is left of the target divided by the days left;
- projection = confirmed so far + the last 7 days' average per day x the days left;
- k-factor of a weekly cohort = confirmed sign-ups the cohort's members invited / cohort size. The
  headline k uses cohorts that are at least a week old, so their invites had time to arrive.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any


def goal_report(stats: dict[str, Any] | None, goal: dict[str, Any], today: date) -> dict[str, Any]:
    target = int(goal.get("target", 0))
    days_total = int(goal.get("days", 0))
    report: dict[str, Any] = {"name": goal.get("name", "confirmed sign-ups"), "target": target}
    if stats is None:
        report["status"] = "no data: the waitlist is not deployed or its stats endpoint is not configured"
        return report

    per_day: dict[str, int] = {}
    referred_per_day: dict[str, int] = {}
    for row in stats.get("days", []):
        per_day[row["date"]] = per_day.get(row["date"], 0) + int(row.get("confirmed", 0))
        referred_per_day[row["date"]] = referred_per_day.get(row["date"], 0) + int(row.get("referred", 0) or 0)
    total = int(stats.get("totals", {}).get("confirmed", sum(per_day.values())))
    report["confirmed_total"] = total
    report["pending_total"] = int(stats.get("totals", {}).get("pending", 0))

    last7 = [today - timedelta(days=i) for i in range(1, 8)]
    week = sum(per_day.get(d.isoformat(), 0) for d in last7)
    report["last_7_days"] = week
    report["avg_per_day_7"] = round(week / 7, 1)
    referred_week = sum(referred_per_day.get(d.isoformat(), 0) for d in last7)
    report["referral_share_7"] = round(referred_week / week, 3) if week else 0.0

    start_text = str(goal.get("start", "") or "")
    if not start_text or not days_total:
        report["status"] = "goal clock not started: set [goal] start to the launch date"
    else:
        start = date.fromisoformat(start_text)
        deadline = start + timedelta(days=days_total)
        elapsed = max(0, min(days_total, (today - start).days))
        left = max(0, (deadline - today).days)
        plan = round(target * elapsed / days_total)
        report.update(
            start=start.isoformat(),
            deadline=deadline.isoformat(),
            days_elapsed=elapsed,
            days_left=left,
            plan_to_date=plan,
            gap_to_plan=total - plan,
            needed_per_day=round((target - total) / left, 1) if left else None,
            projection=round(total + week / 7 * left),
        )
        report["on_track"] = report["projection"] >= target
        report["status"] = "on track" if report["on_track"] else "behind plan"
        series = []
        days = [today - timedelta(days=i) for i in range(14, 0, -1)]
        running = total - sum(v for d, v in per_day.items() if d >= days[0].isoformat())
        for day in days:
            running += per_day.get(day.isoformat(), 0)
            done = max(0, min(days_total, (day - start).days + 1))
            series.append(
                {"date": day.isoformat(), "confirmed": per_day.get(day.isoformat(), 0), "cumulative": running, "plan": round(target * done / days_total)}
            )
        report["series"] = series

    sources: dict[str, int] = {}
    for row in stats.get("sources", []):
        sources[row["source"]] = sources.get(row["source"], 0) + int(row.get("confirmed", 0))
    report["sources"] = sorted(({"source": s, "confirmed": n} for s, n in sources.items()), key=lambda r: -r["confirmed"])

    cohorts = [
        {"week": c["week"], "size": int(c["size"]), "invited": int(c.get("invited") or 0)}
        for c in stats.get("cohorts", [])
        if c.get("week")
    ]
    for cohort in cohorts:
        cohort["k"] = round(cohort["invited"] / cohort["size"], 2) if cohort["size"] else 0.0
    mature = [c for c in cohorts if date.fromisoformat(c["week"]) + timedelta(days=14) <= today]
    size = sum(c["size"] for c in mature)
    report["k_factor"] = round(sum(c["invited"] for c in mature) / size, 2) if size else None
    report["k_cohorts"] = cohorts[-6:]
    return report
