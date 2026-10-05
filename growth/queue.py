"""The human queue: drafts a person posts by hand. By design only Reddit may use it.

Every other channel either runs on its own or not at all. A draft passes the never-automate checks
(the same text never goes to two communities) before it is stored, and the queue renders as one
static HTML page with a "copy and open" button per draft.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .channels import CHANNELS, HUMAN_QUEUE
from .policy import Action, ProjectRules, check_action, fingerprint
from .store import Store
from .util import iso, sha256, write_atomic, write_json


class QueueRefused(Exception):
    pass


@dataclass(frozen=True)
class Draft:
    id: str
    channel: str
    community: str
    thread_url: str
    title: str
    text: str
    why: str
    created_at: str
    status: str = "open"


def queue_dir(state_dir: Path, project_id: str, channel: str) -> Path:
    return state_dir / "projects" / project_id / "queue" / channel


def add_draft(
    store: Store,
    state_dir: Path,
    project_id: str,
    rules: ProjectRules,
    *,
    channel: str,
    community: str,
    thread_url: str,
    title: str,
    text: str,
    why: str,
    now: datetime,
) -> Draft:
    known = CHANNELS.get(channel)
    if known is None or known.level != HUMAN_QUEUE:
        raise QueueRefused(f"{channel} has no human queue; it either runs on its own or not at all")
    draft = Draft(
        id=sha256(thread_url + text)[:12],
        channel=channel,
        community=community,
        thread_url=thread_url,
        title=title,
        text=text,
        why=why,
        created_at=iso(now),
    )
    folder = queue_dir(state_dir, project_id, channel)
    # Check, write and record in one transaction, so two processes can't both queue the same text.
    with store.tx():
        check_action(Action(channel=channel, kind="draft", community=community, text=text, url=thread_url), rules, store.text_communities)
        write_json(folder / f"{draft.id}.json", draft.__dict__)
        store.record_text(fingerprint(text), community, project_id, channel)
    return draft


def open_drafts(state_dir: Path, project_id: str) -> list[Draft]:
    root = state_dir / "projects" / project_id / "queue"
    drafts = []
    for file in sorted(root.glob("*/*.json")) if root.is_dir() else []:
        data = json.loads(file.read_text(encoding="utf-8"))
        if data.get("status", "open") == "open":
            drafts.append(Draft(**data))
    return drafts


def render_queue_page(project_name: str, drafts: list[Draft], esc: Callable[[Any], str]) -> str:
    """One self-contained page: each draft with its thread, its reason and a copy-and-open button."""
    rows = []
    for d in drafts:
        rows.append(
            f'<article class="draft"><header><p class="where">{esc(d.community)}</p>'
            f'<h2><a href="{esc(d.thread_url)}" rel="noopener noreferrer" target="_blank">{esc(d.title)}</a></h2></header>'
            f'<p class="why">{esc(d.why)}</p><textarea readonly rows="{min(14, 3 + d.text.count(chr(10)) + len(d.text) // 90)}">{esc(d.text)}</textarea>'
            f'<p><button type="button" data-open="{esc(d.thread_url)}">Copy and open thread</button></p></article>'
        )
    body = "".join(rows) or '<p class="empty">No drafts waiting. New ones appear here when a thread is worth answering.</p>'
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex"><title>Reddit drafts | {esc(project_name)}</title>
<style>{QUEUE_CSS}</style></head>
<body><main><h1>Reddit drafts</h1><p class="lead">{len(drafts)} waiting. Read, edit in Reddit, post from your own account, and say you build it.</p>{body}</main>
<script>document.querySelectorAll("[data-open]").forEach(function(b){{b.addEventListener("click",function(){{var t=b.closest(".draft").querySelector("textarea");t.select();(navigator.clipboard?navigator.clipboard.writeText(t.value):Promise.reject()).catch(function(){{document.execCommand("copy");}}).then(function(){{window.open(b.getAttribute("data-open"),"_blank","noopener");b.textContent="Copied";}});}});}});</script>
</body></html>
"""


QUEUE_CSS = re.sub(
    r"\s+",
    " ",
    """
:root { --bg: #121212; --ink: #efefed; --ink-2: rgb(239 239 237 / .74); --line: rgb(239 239 237 / .16);
  --paper: #e8e9e8; --on-paper: #131313; --lime: #d4f53c; --lime-edge: #9cb52a; color-scheme: dark; }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink); font: 300 17px/1.55 "Outfit", system-ui, sans-serif; }
main { max-width: 860px; margin: 0 auto; padding: 56px 20px 80px; }
h1 { font: 400 clamp(28px, 4vw, 44px)/1.05 "Michroma", system-ui, sans-serif; text-transform: uppercase; margin: 0; }
h1::after { content: ""; display: block; width: 18px; height: 3px; margin-top: 14px; background: var(--lime); }
.lead { color: var(--ink-2); margin: 18px 0 36px; }
.draft { border-top: 1px solid var(--line); padding: 26px 0 30px; }
.where { margin: 0; font: 400 12px/1.4 "IBM Plex Mono", ui-monospace, monospace; letter-spacing: .05em; text-transform: uppercase; color: var(--ink-2); }
h2 { margin: 6px 0 0; font: 500 20px/1.3 "Outfit", system-ui, sans-serif; }
h2 a { color: inherit; text-decoration-color: var(--lime); text-underline-offset: 4px; }
.why { color: var(--ink-2); font-size: 15px; }
textarea { width: 100%; padding: 16px 18px; border: 1px solid var(--line); border-radius: 14px; background: #1a1a1a; color: var(--ink); font: 400 15px/1.55 "Outfit", system-ui, sans-serif; resize: vertical; }
button { min-height: 44px; padding: 0 20px; border: 1px solid var(--ink); border-radius: 999px; background: var(--ink); color: var(--bg); font: 500 15px/1 "Outfit", system-ui, sans-serif; cursor: pointer; }
button:hover { background: #fff; border-color: var(--lime-edge); }
:focus-visible { outline: 2px solid var(--lime-edge); outline-offset: 3px; }
::selection { background: var(--lime); color: var(--on-paper); }
.empty { color: var(--ink-2); }
""",
).strip()


def write_queue_page(state_dir: Path, project_id: str, project_name: str, esc: Callable[[Any], str]) -> Path:
    path = state_dir / "projects" / project_id / "queue" / "index.html"
    write_atomic(path, render_queue_page(project_name, open_drafts(state_dir, project_id), esc))
    return path
