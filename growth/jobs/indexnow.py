"""indexnow: tell IndexNow (Bing, Yandex, Seznam, Naver) about indexable pages that changed.

Each URL is pinged once per content version: the side-effect ledger key is the URL plus its hash,
so a retried or overlapping run can never ping the same version twice. Google is not pinged; it
reads the sitemap, and its Indexing API is not for normal pages.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from .. import net
from ..policy import Action
from ..util import read_json

ENDPOINT = "https://api.indexnow.org/indexnow"
BATCH = 1000


def run(ctx: Any) -> str:
    project = ctx.project
    key = str(ctx.job.params["key"])
    base = project.site["base_url"]
    host = urlparse(base).hostname
    urls = (read_json(ctx.state_dir / "changed-urls.json", {}) or {}).get("urls", [])
    sent = 0
    for start in range(0, len(urls), BATCH):
        batch = []
        for entry in urls[start : start + BATCH]:
            effect = f"indexnow:{entry['path']}:{entry['hash']}"
            state = ctx.store.effect_begin(ctx.scope, effect, "indexnow", "ping", retry_failed=True)
            if state == "go":
                batch.append((effect, base + entry["path"]))
            elif state == "pending":
                ctx.notes.append(f"{entry['path']}: an earlier ping never reported back; not repeated")
        if not batch:
            continue
        action = Action(channel="indexnow", kind="ping", url=ENDPOINT, target=host or "")
        try:
            ctx.check(action)
            status, _ = net.request(
                "POST",
                ENDPOINT,
                body={"host": host, "key": key, "keyLocation": f"{base}/{key}.txt", "urlList": [u for _, u in batch]},
                timeout=30,
                retries=1,
            )
            if status not in (200, 202):
                raise net.HttpError(f"IndexNow answered {status}")
        except Exception as exc:
            for effect, _ in batch:
                ctx.store.effect_end(ctx.scope, effect, "failed", str(exc))
            raise
        for effect, _ in batch:
            ctx.store.effect_end(ctx.scope, effect, "done", "")
        sent += len(batch)
    return f"pinged {sent} URLs" if sent else "nothing new to ping"
