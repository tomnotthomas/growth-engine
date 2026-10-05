"""build-site: regenerate the project's static site from its config, curated data and the live cache."""

from __future__ import annotations

from typing import Any

from ..site.build import build_site
from ..util import write_json


def run(ctx: Any) -> str:
    result = build_site(ctx.project, ctx.state_dir, ctx.engine.dist_dir, now=ctx.now)
    # IndexNow reads this list; a URL stays in it until IndexNow has been told about that version.
    write_json(
        ctx.state_dir / "changed-urls.json",
        {
            "built_at": ctx.now.isoformat(),
            "urls": [
                {"path": p.path, "hash": result.registry["pages"][p.path]["hash"]}
                for p in result.indexable
            ],
        },
    )
    return "; ".join(result.notes + [f"{len(result.changed)} changed"])
