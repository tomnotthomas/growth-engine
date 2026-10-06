"""A throwaway engine home with made-up activity, so the control app can be opened without the GEEKOM.

Everything here is fictional and marked as demo data: the snapshot says `"demo": true`, and the app
shows a banner. It is built from the example project in examples/home, inside a temporary folder.
"""

from __future__ import annotations

import random
import shutil
import tempfile
from datetime import timedelta
from pathlib import Path

from .. import guard
from ..store import FAILED, OK, Store
from ..util import iso, utcnow, write_json

REPO = Path(__file__).resolve().parents[2]

DRAFTS = (
    (
        "r/WeAreTheMusicMakers",
        "Any way to use my Windows-only plugins on a Mac?",
        "Asked twice this week; the answer is exactly what Kiln does.",
        "I had the same problem and ended up building something for it: Kiln runs the plugins you already "
        "own in a browser tab. It is in a free beta, happy to hear if it works for your setup.",
    ),
    (
        "r/audioengineering",
        "Lightweight way to test plugins before buying?",
        "Fits the guide page on trying plugins without installing them.",
        "Not a full answer, but trying them in a browser first saved me a lot of installs. I wrote down "
        "what worked for me here, including the limits.",
    ),
)


def make_demo_home(seed: int = 7) -> Path:
    rng = random.Random(seed)
    root = Path(tempfile.mkdtemp(prefix="growth-demo-"))
    home = root / "home"
    shutil.copytree(REPO / "examples" / "home", home, ignore=shutil.ignore_patterns("state", "dist"))
    now = utcnow().replace(microsecond=0)
    today = now.date()
    start = today - timedelta(days=19)
    project = home / "projects" / "example" / "project.toml"
    text = project.read_text(encoding="utf-8").replace('start = ""', f'start = "{start.isoformat()}"\nzone = ["DE", "AT", "CH"]')
    project.write_text(text, encoding="utf-8")
    state = home / "state"

    # Sign-ups per day and country, rising after launch, with a few from outside the zone.
    days, sources = [], {}
    channels = ("search", "reddit", "referral", "directories", "direct")
    for back in range(34, 0, -1):
        day = today - timedelta(days=back)
        base = 0 if day < start else 40 + (day - start).days * 6
        for country, share in (("DE", 0.62), ("AT", 0.14), ("CH", 0.12), ("NL", 0.07), ("US", 0.05)):
            confirmed = max(0, int(base * share * rng.uniform(0.7, 1.3)))
            if confirmed:
                days.append({"date": day.isoformat(), "role": "player", "country": country, "confirmed": confirmed, "referred": int(confirmed * 0.22)})
                for channel, weight in zip(channels, (0.38, 0.17, 0.22, 0.13, 0.10)):
                    key = (channel, country)
                    sources[key] = sources.get(key, 0) + int(confirmed * weight)
    stats = {
        "totals": {"confirmed": sum(d["confirmed"] for d in days), "pending": 311},
        "days": days,
        "sources": [{"source": s, "role": "player", "country": c, "confirmed": n} for (s, c), n in sources.items()],
        "cohorts": [
            {"week": (start + timedelta(days=7 * i)).isoformat(), "size": 300 + 120 * i, "invited": 70 + 40 * i} for i in range(3)
        ],
        "source_of_numbers": "demo data",
    }
    write_json(state / "projects" / "example" / "signup-stats.json", {"fetched_at": iso(now), "stats": stats, "why": "", "demo": True})

    # A few days of runs, one failure, and two Reddit drafts waiting.
    store = Store(state / "engine.db")
    try:
        for back in range(3, -1, -1):
            for hour in (0, 6, 12, 18):
                slot = (now - timedelta(days=back)).replace(hour=hour, minute=0, second=0)
                if slot > now:
                    continue
                store.claim("example", "fetch-data", iso(slot))
                failed = back == 1 and hour == 12
                store.finish("example", "fetch-data", iso(slot), FAILED if failed else OK,
                             "HttpError: GET https://app.kiln.example answered 503" if failed else "plugins_live: 214 items")
                _backdate(store, "example", "fetch-data", slot)
            build = (now - timedelta(days=back)).replace(hour=2, minute=0, second=0)
            if build <= now:
                store.claim("example", "build-site", iso(build))
                store.finish("example", "build-site", iso(build), OK, f"{rng.randint(1, 9)} changed")
                _backdate(store, "example", "build-site", build)
        store.put("last_tick", iso(now - timedelta(minutes=3)))
    finally:
        store.close()
    for i, (community, title, why, body) in enumerate(DRAFTS):
        draft_id = f"d{i}e{seed:03d}a0{i}"
        write_json(
            state / "projects" / "example" / "queue" / "reddit" / f"{draft_id}.json",
            {
                "id": draft_id, "channel": "reddit", "community": community, "thread_url": f"https://www.reddit.com/{community}/",
                "title": title, "text": body, "why": why, "created_at": iso(now - timedelta(hours=5 + 9 * i)), "status": "open",
            },
        )
    write_json(
        state / "engine" / "update.json",
        {"history": [{"at": iso(now - timedelta(days=1, hours=2)), "from": "79a0516", "to": "demo000", "status": "updated", "detail": "checks green, signed"}]},
    )
    guard.audit(state, "engine", "action.done", scope="example", job="build-site", channel="website", kind="publish", key="demo")
    guard.audit(state, "captain", "control.goal", scope="example", changes={"projects.example.goal.target": 5000})
    return home


def _backdate(store: Store, scope: str, job: str, slot) -> None:
    """Give a demo run the times it would have had, instead of the moment the demo was made."""
    store.db.execute(
        "UPDATE runs SET started_at=?, finished_at=? WHERE scope=? AND job=? AND slot=?",
        (iso(slot), iso(slot + timedelta(seconds=40)), scope, job, iso(slot)),
    )
