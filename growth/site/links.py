"""Link check for a built site: every internal link, script, stylesheet and image must exist.

Used by CI on the example project and by the deploy job before anything is uploaded.
`python -m growth.site.links dist/example/public` prints each broken reference and exits 1.
"""

from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

WORKER_ROUTES = ("/api/", "/r/")  # answered by the waitlist Worker, not by files
ATTRS = {"href", "src", "srcset", "content", "poster", "action"}


class _Refs(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.refs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if not value or name not in ATTRS:
                continue
            if name == "content" and not (tag == "meta" and value.startswith("/")):
                continue
            if name == "srcset":
                self.refs += [part.strip().split(" ")[0] for part in value.split(",") if part.strip()]
            else:
                self.refs.append(value)


def _target(public: Path, ref: str) -> Path | None:
    """The file a site-relative reference points at, or None when it is not a local file reference."""
    if ref.startswith(("//", "#", "mailto:", "tel:", "data:", "javascript:")) or re.match(r"^[a-z][a-z0-9+.-]*:", ref, re.I):
        return None
    path = unquote(urlsplit(ref).path)
    if not path or not path.startswith("/") or path.startswith(WORKER_ROUTES):
        return None
    return public / path.lstrip("/")


def _exists(target: Path) -> bool:
    return target.is_file() or (target / "index.html").is_file() or target.with_suffix(".html").is_file()


def check_links(public: Path) -> list[str]:
    problems = []
    for page in sorted(public.rglob("*.html")):
        parser = _Refs()
        parser.feed(page.read_text(encoding="utf-8"))
        for ref in parser.refs:
            target = _target(public, ref)
            if target is not None and not _exists(target):
                problems.append(f"{page.relative_to(public)}: broken link {ref}")
    for css in sorted(public.rglob("*.css")):
        for ref in re.findall(r"url\(\s*['\"]?([^'\")]+)", css.read_text(encoding="utf-8")):
            target = _target(public, ref)
            if target is not None and not _exists(target):
                problems.append(f"{css.relative_to(public)}: broken url({ref})")
    return problems


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1 or not Path(args[0]).is_dir():
        print("usage: python -m growth.site.links <built public/ folder>", file=sys.stderr)
        return 2
    problems = check_links(Path(args[0]))
    for line in problems:
        print(line)
    pages = sum(1 for _ in Path(args[0]).rglob("*.html"))
    print(f"{pages} pages checked, {len(problems)} broken references", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
