"""The job kinds the engine can run. A project's or the engine's config schedules instances of them.

`idempotent` jobs may be retried after a failure or an interruption; the rest never run twice for
one slot. `ai` is "required" for jobs that wait for AI budget (and are missed without it), "optional"
for jobs that run on time and do without AI when there is no budget, None for jobs that never use it. Jobs that act outside the engine also go through the side-effect ledger (JobContext.act),
so even a retried job cannot repeat an outward action.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class JobKind:
    name: str
    target: str  # "module:function"
    scope: str  # "project" or "engine"
    ai: str | None
    idempotent: bool
    channel: str | None
    description: str

    def resolve(self) -> Callable[[Any], str]:
        module, func = self.target.split(":")
        return getattr(importlib.import_module(module), func)


JOB_KINDS: dict[str, JobKind] = {
    k.name: k
    for k in (
        JobKind(
            "fetch-data",
            "growth.jobs.fetch:run",
            "project",
            ai=None,
            idempotent=True,
            channel=None,
            description="Fetch the project's data sources (e.g. the live playable-games list) into the state cache.",
        ),
        JobKind(
            "build-site",
            "growth.jobs.build:run",
            "project",
            ai=None,
            idempotent=True,
            channel="website",
            description="Generate the static site into dist/<project>/ (pages, sitemap, robots, waitlist worker).",
        ),
        JobKind(
            "indexnow",
            "growth.jobs.indexnow:run",
            "project",
            ai=None,
            idempotent=False,
            channel="indexnow",
            description="Tell IndexNow about indexable pages whose content changed; each URL+version once.",
        ),
        JobKind(
            "directories-sync",
            "growth.jobs.directories:sync",
            "project",
            ai=None,
            idempotent=True,
            channel=None,
            description="Import the launch-directory list into the project's catalogue and score each entry.",
        ),
        JobKind(
            "directories-draft",
            "growth.jobs.directories:draft",
            "project",
            ai="required",
            idempotent=True,
            channel=None,
            description="Write listing texts in the project's voice for the best-scoring directories.",
        ),
        JobKind(
            "directories-submit",
            "growth.jobs.directories:submit",
            "project",
            ai=None,
            idempotent=False,
            channel="directory-submit",
            description="From launch day: submit to verified API or plain-form directories once each; skip and report the rest.",
        ),
        JobKind(
            "deploy",
            "growth.jobs.deploy:run",
            "project",
            ai=None,
            idempotent=True,
            channel="website",
            description="When the built site changed: preview on Cloudflare, check it, and only after the launch go promote to production (rolls back if the live checks fail).",
        ),
        JobKind(
            "digest",
            "growth.jobs.digest:run",
            "engine",
            ai="optional",
            idempotent=False,
            channel="digest",
            description="Weekly report per project: what ran, traffic, sign-ups against the goal, anything blocked.",
        ),
    )
}
