"""HTML helpers: escaping, and a tiny inline markup for copy in config files.

Copy may use **bold**, [link text](href) and a non-breaking space as "~" between a number and its
unit ("2~€"). Everything else is escaped, so config text can never inject markup.
"""

from __future__ import annotations

import html
import re
from typing import Callable

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


def esc(text: object) -> str:
    return html.escape(str(text), quote=True)


def inline(text: str, resolve: Callable[[str], str] | None = None) -> str:
    """Escape `text`, then turn **bold** and [text](href) into markup."""
    links: list[str] = []

    def keep_link(match: re.Match[str]) -> str:
        href = match.group(2)
        if resolve:
            href = resolve(href)
        links.append(f'<a href="{esc(href)}">{esc(match.group(1))}</a>')
        return f"\x00{len(links) - 1}\x00"

    marked = _LINK.sub(keep_link, text)
    out = esc(marked)
    out = _BOLD.sub(r"<b>\1</b>", out)
    out = out.replace("~", "&nbsp;")
    return re.sub(r"\x00(\d+)\x00", lambda m: links[int(m.group(1))], out)


def plain(text: str) -> str:
    """Copy without its markup, for titles, meta tags and structured data."""
    text = _LINK.sub(r"\1", text)
    text = _BOLD.sub(r"\1", text)
    return text.replace("~", " ")


def attrs(**values: object) -> str:
    parts = []
    for key, value in values.items():
        if value is None or value is False:
            continue
        name = key.rstrip("_").replace("_", "-")
        parts.append(name if value is True else f'{name}="{esc(value)}"')
    return (" " + " ".join(parts)) if parts else ""
