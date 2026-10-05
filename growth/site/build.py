"""Build a project's static site into dist/<project>/ and keep the build's memory in the state dir.

Output layout (deployable as it is):

    dist/<project>/public/      the static site (works on any static host, e.g. GitHub Pages)
    dist/<project>/worker/      the waitlist API for Cloudflare Workers (when [waitlist] is set)
    dist/<project>/wrangler.toml, schema.sql, DEPLOY.md

The build refuses to finish (and leaves the previous output untouched) if a page would show an
image that is not in the media manifest, hotlink a remote image, carry structured data that claims
ratings, or show a number without a measurement source.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..policy import PolicyViolation, check_media
from ..util import parse_duration, parse_iso, read_json, read_toml, sha256, write_atomic, write_json
from .data import LiveData, load_media, load_measurements, resolve_items
from .html import esc
from .render import PageRenderer, Rendered, SiteContext
from .spec import load_pages

ENGINE_ASSETS = Path(__file__).resolve().parent.parent / "assets"
BASE_THEME = Path(__file__).resolve().parent.parent / "themes" / "base"
BASE_CSS = ("base.css", "sections.css", "pages.css", "generated.css")
_REF = re.compile(r'\b(?:src|poster)="([^"]+)"|<meta property="og:image" content="([^"]+)"')
_CSS_URL = re.compile(r"""url\(\s*(?:"([^"]*)"|'([^']*)'|([^)'"]*))\s*\)""")


class BuildError(Exception):
    pass


@dataclass
class BuildResult:
    out: Path
    pages: list[Rendered]
    changed: list[str]
    registry: dict[str, Any]
    notes: list[str] = field(default_factory=list)

    @property
    def indexable(self) -> list[Rendered]:
        return [p for p in self.pages if p.indexable]


def live_cache_path(state_dir: Path, source_id: str) -> Path:
    return state_dir / "data" / f"{source_id}.json"


def load_live(project: Any, state_dir: Path, source_id: str, now: datetime) -> LiveData | None:
    cached = read_json(live_cache_path(state_dir, source_id))
    if not cached:
        return None
    fetched = parse_iso(cached["fetched_at"])
    max_age = parse_duration(project.data[source_id].get("max_age", "3d"))
    if now - fetched > max_age:
        return None
    return LiveData(fetched_at=fetched, items=list(cached.get("items", [])))


def build_site(project: Any, state_dir: Path, dist_root: Path, *, now: datetime) -> BuildResult:
    pages = load_pages(project)
    ui = read_toml(project.root / project.site.get("ui", "ui.toml"))
    measurements = load_measurements(project.root, project.site.get("measurements"))
    media = load_media(project.root, project.site.get("media"))
    registry: dict[str, Any] = read_json(state_dir / "site-registry.json", {}) or {}
    today = now.astimezone(project.tz).date()

    items: dict[str, Any] = {}
    checked: date | None = None
    for cid, conf in project.collections.items():
        live = None
        if conf.get("source"):
            live = load_live(project, state_dir, conf["source"], now)
            if live is None and conf.get("require_live", True):
                raise BuildError(
                    f"collections.{cid}: no fresh data from {conf['source']!r}; run fetch-data first "
                    "(the previous site stays as it is)"
                )
            if live:
                checked = live.fetched_at.astimezone(project.tz).date()
        items[cid] = resolve_items(project, cid, live, registry, measurements, media, today)

    ctx = SiteContext(
        project=project,
        pages=pages,
        items=items,
        measurements=measurements,
        ui=ui,
        today=today,
        checked=checked,
        site_indexable=bool(project.site.get("indexable")),
    )
    theme = project.root / project.site.get("theme", "theme")
    sheets = [BASE_THEME / name for name in BASE_CSS] if project.site.get("base_theme", True) else []
    sheets += [theme / name for name in project.site.get("theme_css", [])]
    css = "\n".join(sheet.read_text(encoding="utf-8") for sheet in sheets)
    js = (ENGINE_ASSETS / "waitlist.js").read_text(encoding="utf-8")
    ctx.asset_version = "?v=" + sha256(css + js)[:10]

    rendered: list[Rendered] = []
    for page in pages.values():
        if page.kind == "item":
            for item in items.get(page.collection, []):
                if item.has_page:
                    for lang in page.langs:
                        rendered.append(PageRenderer(ctx, page, lang, item).render())
        else:
            for lang in page.langs:
                rendered.append(PageRenderer(ctx, page, lang).render())

    out = dist_root / project.site.get("out", project.id)
    tmp = out.with_name(f".{out.name}.build-{os.getpid()}")
    shutil.rmtree(tmp, ignore_errors=True)
    public = tmp / "public"
    try:
        for page in rendered:
            target = public / page.path.lstrip("/") / "index.html"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(page.html, encoding="utf-8")
        assets = public / "assets"
        assets.mkdir(parents=True, exist_ok=True)
        (assets / "site.css").write_text(css, encoding="utf-8")
        (assets / "waitlist.js").write_text(js, encoding="utf-8")
        for folder in project.site.get("theme_dirs", []):
            src = theme / folder
            if src.is_dir():
                shutil.copytree(src, public / folder, dirs_exist_ok=True)
        _copy_media(project, media, ctx.media_used, public)
        _check_references(public, media, project)
        _write_robots_and_sitemap(project, rendered, registry, public, today)
        _write_404(project, ui, public, ctx.asset_version)
        if project.raw.get("waitlist"):
            from ..waitlist.bundle import write_bundle

            write_bundle(project, ui, tmp)
        _swap(tmp, out)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise

    changed = _update_registry(registry, rendered, items, today)
    write_json(state_dir / "site-registry.json", registry)
    notes = [f"{len(rendered)} pages, {sum(p.indexable for p in rendered)} indexable"]
    if not ctx.site_indexable:
        notes.append("site is not indexable yet (robots: Disallow, every page noindex)")
    return BuildResult(out=out, pages=rendered, changed=changed, registry=registry, notes=notes)


def _copy_media(project: Any, media: dict[str, Any], used: set[str], public: Path) -> None:
    root = project.root / project.site.get("media_dir", "media")
    for rel in sorted(used):
        if rel not in media:
            raise PolicyViolation("own-footage-only", f"{rel} is used on a page but not listed in the media manifest")
        check_media([media[rel]], project.rules)
        src = root / rel
        if not src.is_file():
            raise BuildError(f"media file {rel} is listed but missing under {root}")
        dst = public / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _check_references(public: Path, media: dict[str, Any], project: Any) -> None:
    """No page may show a remote image, or a local one that is not in the media manifest."""
    base_host = urlparse(project.site["base_url"]).hostname
    for html_file in public.rglob("*.html"):
        text = html_file.read_text(encoding="utf-8")
        for match in _REF.finditer(text):
            ref = match.group(1) or match.group(2)
            if ref.startswith("/assets/"):
                continue
            parsed = urlparse(ref)
            if parsed.scheme in {"http", "https"}:
                if parsed.hostname != base_host:
                    raise PolicyViolation(
                        "own-footage-only", f"{html_file.relative_to(public)}: remote media {ref} (third-party art is never shown)"
                    )
                ref = parsed.path
            rel = ref.lstrip("/")
            if rel not in media:
                raise PolicyViolation("own-footage-only", f"{html_file.relative_to(public)}: {rel} is not in the media manifest")
    css = (public / "assets" / "site.css").read_text(encoding="utf-8")
    for match in _CSS_URL.finditer(css):
        ref = (match.group(1) or match.group(2) or match.group(3) or "").strip()
        if ref.startswith("data:") or ref.startswith("/fonts/") or ref.startswith("../fonts/"):
            continue
        raise PolicyViolation("own-footage-only", f"site.css: url({ref}) must be a data: URI or a font")


def _write_robots_and_sitemap(project: Any, rendered: list[Rendered], registry: dict[str, Any], public: Path, today: date) -> None:
    base = project.site["base_url"]
    if not project.site.get("indexable"):
        write_atomic(public / "robots.txt", "# Not public yet: nothing may be indexed.\nUser-agent: *\nDisallow: /\n")
        return
    write_atomic(public / "robots.txt", f"User-agent: *\nAllow: /\n\nSitemap: {base}/sitemap.xml\n")
    known = registry.get("pages", {})
    by_page: dict[str, list[Rendered]] = {}
    for page in rendered:
        by_page.setdefault(page.page_id + "|" + page.item_slug, []).append(page)
    urls = []
    for page in sorted(rendered, key=lambda p: p.path):
        if not page.sitemap:
            continue
        lastmod = known.get(page.path, {}).get("lastmod", today.isoformat())
        alts = ""
        siblings = [p for p in by_page[page.page_id + "|" + page.item_slug] if p.sitemap]
        if len(siblings) > 1:
            alts = "".join(
                f'\n    <xhtml:link rel="alternate" hreflang="{esc(s.lang)}" href="{esc(base + s.path)}"/>' for s in siblings
            )
        urls.append(f"  <url>\n    <loc>{esc(base + page.path)}</loc>\n    <lastmod>{lastmod}</lastmod>{alts}\n  </url>")
    write_atomic(
        public / "sitemap.xml",
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">\n'
        + "\n".join(urls)
        + "\n</urlset>\n",
    )


def _write_404(project: Any, ui: dict[str, Any], public: Path, version: str) -> None:
    lang = project.default_language
    texts = ui.get(lang, {})
    home = "/"
    write_atomic(
        public / "404.html",
        f'<!doctype html>\n<html lang="{esc(lang)}">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n<meta name="robots" content="noindex">\n'
        f"<title>{esc(texts.get('not_found_title', 'Not found'))}</title>\n"
        f'<link rel="stylesheet" href="/assets/site.css{version}">\n</head>\n<body class="page-404">\n<main class="art intro">'
        f"<div><h1>{esc(texts.get('not_found_title', 'Not found'))}</h1></div>"
        f'<div class="body"><p><a href="{home}">{esc(texts.get("not_found_link", "Home"))}</a></p></div></main>\n</body>\n</html>\n',
    )


def _swap(tmp: Path, out: Path) -> None:
    old = out.with_name(f".{out.name}.old-{os.getpid()}")
    if out.exists():
        os.replace(out, old)
    os.replace(tmp, out)
    shutil.rmtree(old, ignore_errors=True)


def _update_registry(registry: dict[str, Any], rendered: list[Rendered], items: dict[str, Any], today: date) -> list[str]:
    """Remember each page's content hash and lastmod, and which item pages exist (URLs are kept)."""
    pages = registry.setdefault("pages", {})
    changed = []
    for page in rendered:
        digest = sha256(re.sub(r"\?v=[0-9a-f]+", "", page.html))
        entry = pages.get(page.path)
        if entry is None or entry.get("hash") != digest:
            pages[page.path] = {"hash": digest, "lastmod": today.isoformat(), "indexable": page.indexable}
            changed.append(page.path)
        else:
            entry["indexable"] = page.indexable
    published = registry.setdefault("items", {})
    for cid, collection in items.items():
        for item in collection:
            if item.has_page:
                published.setdefault(cid, {}).setdefault(item.slug, today.isoformat())
    registry["built_at"] = today.isoformat()
    return changed

