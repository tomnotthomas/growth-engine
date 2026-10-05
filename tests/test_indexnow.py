"""IndexNow pings: each batch is reserved only just before it is sent, so a failed batch is retried."""

from __future__ import annotations

from unittest import mock

from growth import net
from growth.budget import Budget
from growth.jobs import indexnow
from growth.runner import JobContext
from growth.util import write_json

from .helpers import NOW, HomeTestCase


class Batches(HomeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.edit("projects/example/project.toml", "domain_decided = false", "domain_decided = true")
        self.edit("projects/example/project.toml", "indexable = false", "indexable = true")
        self.append("projects/example/project.toml", '[channels.indexnow]\nenabled = true\n\n[jobs.ping]\nkind = "indexnow"\nschedule = "daily 05:00"\nkey = "abcdefgh12"\n')

    def ctx(self) -> JobContext:
        engine = self.engine()
        store = self.store(engine)
        project = engine.projects["example"]
        ctx = JobContext(engine=engine, project=project, job=project.jobs["ping"], store=store, budget=Budget(engine.ai, store, engine.tz), slot=NOW, now=NOW)
        write_json(ctx.state_dir / "changed-urls.json", {"urls": [{"path": f"/{n}/", "hash": "h"} for n in ("a", "b", "c")]})
        return ctx

    def statuses(self, ctx: JobContext) -> dict[str, str]:
        rows = ctx.store.db.execute("SELECT key, status FROM effects").fetchall()
        return {row["key"]: row["status"] for row in rows}

    def test_a_failed_batch_leaves_later_batches_unreserved_and_is_retried(self) -> None:
        ctx = self.ctx()
        sent: list[list[str]] = []

        def request(method, url, *, body, **kwargs):
            sent.append(body["urlList"])
            if len(sent) == 2:
                raise net.HttpError("IndexNow answered 500")
            return 200, b""

        with mock.patch.object(indexnow, "BATCH", 1), mock.patch.object(net, "request", request):
            with self.assertRaises(net.HttpError):
                indexnow.run(ctx)
            self.assertEqual(self.statuses(ctx), {"indexnow:/a/:h": "done", "indexnow:/b/:h": "failed"})
            self.assertEqual(indexnow.run(ctx), "pinged 2 URLs")
        self.assertEqual(sent[2:], [["https://kiln.example/b/"], ["https://kiln.example/c/"]])
        self.assertEqual(set(self.statuses(ctx).values()), {"done"})
