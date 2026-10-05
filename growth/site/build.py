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

from ..config import INDEXNOW_KEY
from ..policy import PolicyViolation, check_media
from ..util import parse_duration, parse_iso, read_json, read_toml, sha256, write_atomic, write_json
from .data import LiveData, load_media, load_measurements, resolve_items
from .html import esc
from .render import LASTMOD, PageRenderer, Rendered, SiteContext
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
        live_languages=live_languages(project),
    )
    theme = project.root / project.site.get("theme", "theme")
    sheets = [BASE_THEME / name for name in BASE_CSS] if project.site.get("base_theme", True) else []
    sheets += [theme / name for name in project.site.get("theme_css", [])]
    css = "\n".join(sheet.read_text(encoding="utf-8") for sheet in sheets)
    js = (ENGINE_ASSETS / "waitlist.js").read_text(encoding="utf-8")
    ctx.asset_version = "?v=" + sha256(css + js)[:10]

    if project.raw.get("waitlist"):
        _require_legal(project, pages)
    rendered: list[Rendered] = []
    for page in pages.values():
        langs = [lang for lang in page.langs if ctx.live(lang)]
        if page.kind == "item":
            for item in items.get(page.collection, []):
                if item.has_page:
                    for lang in langs:
                        rendered.append(PageRenderer(ctx, page, lang, item).render())
        else:
            for lang in langs:
                rendered.append(PageRenderer(ctx, page, lang).render())
    if ctx.forms:
        _require_legal(project, pages)

    changed = _update_registry(registry, rendered, items, today)
    for page in rendered:
        page.html = page.html.replace(LASTMOD, registry["pages"][page.path]["lastmod"])

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
        _write_robots_and_sitemap(project, rendered, registry, public)
        for job in project.jobs.values():
            if job.kind == "indexnow" and job.enabled:
                key = str(job.params.get("key", ""))
                if not INDEXNOW_KEY.fullmatch(key):
                    raise BuildError(f"jobs.{job.id}: key must be 8-128 letters, digits or dashes")
                write_atomic(public / f"{key}.txt", key)
        _write_404(project, ui, public, ctx.asset_version, ctx.live_languages)
        _write_search_data(ctx, public)
        if project.raw.get("waitlist"):
            from ..waitlist.bundle import write_bundle

            write_bundle(project, ui, tmp, ctx.live_languages)
        _swap(tmp, out)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise

    write_json(state_dir / "site-registry.json", registry)
    notes = [f"{len(rendered)} pages, {sum(p.indexable for p in rendered)} indexable"]
    if not ctx.site_indexable:
        notes.append("site is not indexable yet (robots: Disallow, every page noindex)")
    return BuildResult(out=out, pages=rendered, changed=changed, registry=registry, notes=notes)


def live_languages(project: Any) -> list[str]:
    """Languages whose pages are built. Others stay wave-ready: validated, but not generated or linked."""
    live = project.site.get("live_languages")
    return [lang for lang in project.languages if live is None or lang in live]


def missing_legal(project: Any, pages: dict[str, Any]) -> list[str]:
    """What still keeps a sign-up form from being lawful: unset legal pages or empty [legal] values they use."""
    missing = []
    legal = project.raw.get("legal", {})
    for key in ("legal_notice", "privacy"):
        value = str(project.site.get(key, "") or "")
        if not value:
            missing.append(f"[site] {key} is not set")
            continue
        page = pages.get(value)
        if page is None:
            continue
        used = set()
        for spec in page.langs.values():
            used.update(re.findall(r"\{legal\.([a-z_]+)\}", repr(spec.sections) + spec.title + spec.description))
        for name in sorted(used):
            if not str(legal.get(name, "")).strip():
                missing.append(f"[legal] {name} is empty (used by page {value})")
    return sorted(set(missing))


def _require_legal(project: Any, pages: dict[str, Any]) -> None:
    missing = missing_legal(project, pages)
    if missing:
        raise BuildError("sign-up forms need the legal notice and privacy pages: " + "; ".join(missing) + " (the previous site stays as it is)")


def _write_search_data(ctx: SiteContext, public: Path) -> None:
    """One small JSON list per collection and language for the on-site game search."""
    for cid, collection in ctx.items.items():
        for lang in ctx.live_languages or ctx.project.languages:
            rows = []
            for item in collection:
                if item.status == "unchecked" or (item.status == "playable" and not item.live):
                    continue
                status = "runs" if item.status == "playable" else item.status
                page_id = ctx.item_page(item)
                href = ctx.path_of(page_id, lang, item) if page_id else ""
                note = ""
                if item.status == "blocked":
                    note = str(item.record.get(f"reason_{lang}") or (item.record.get("reason", "") if lang == ctx.project.default_language else ""))
                rows.append({"name": item.name, "status": status, "href": href, "note": note})
            rows.sort(key=lambda r: r["name"].lower())
            write_json(public / "data" / f"{cid}-{lang}.json", rows)


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


def _write_robots_and_sitemap(project: Any, rendered: list[Rendered], registry: dict[str, Any], public: Path) -> None:
    base = project.site["base_url"]
    if not project.site.get("indexable"):
        write_atomic(public / "robots.txt", "# Not public yet: nothing may be indexed.\nUser-agent: *\nDisallow: /\n")
        return
    write_atomic(public / "robots.txt", f"User-agent: *\nAllow: /\n\nSitemap: {base}/sitemap.xml\n")
    urls = []
    for page in sorted(rendered, key=lambda p: p.path):
        if not page.sitemap:
            continue
        lastmod = registry["pages"][page.path]["lastmod"]
        alts = "".join(
            f'\n    <xhtml:link rel="alternate" hreflang="{esc(lang)}" href="{esc(base + path)}"/>' for lang, path in page.alternates.items()
        )
        urls.append(f"  <url>\n    <loc>{esc(base + page.path)}</loc>\n    <lastmod>{lastmod}</lastmod>{alts}\n  </url>")
    write_atomic(
        public / "sitemap.xml",
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">\n'
        + "\n".join(urls)
        + "\n</urlset>\n",
    )


def _write_404(project: Any, ui: dict[str, Any], public: Path, version: str, languages: list[str]) -> None:
    """One 404 page that speaks every live language, each with a link to its own home page."""
    pages = load_pages(project)
    home = pages.get(project.site.get("home", "player"))
    lang = project.default_language
    texts = ui.get(lang, {})
    lines = []
    for other in languages or [lang]:
        t = ui.get(other, {})
        path = home.langs[other].path if home and other in home.langs else "/"
        lines.append(
            f'<p lang="{esc(other)}">{esc(t.get("not_found_title", "Not found"))} <a href="{esc(path)}">{esc(t.get("not_found_link", "Home"))}</a></p>'
        )
    write_atomic(
        public / "404.html",
        f'<!doctype html>\n<html lang="{esc(lang)}">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n<meta name="robots" content="noindex">\n'
        f"<title>{esc(texts.get('not_found_title', 'Not found'))} | {esc(project.brand['name'])}</title>\n"
        f'<link rel="stylesheet" href="/assets/site.css{version}">\n</head>\n<body class="page-404">\n'
        f'<header class="nav"><a class="wordmark" href="/">{esc(project.brand.get("wordmark", project.brand["name"]))}</a></header>\n'
        f'<main class="art intro"><div><h1>404</h1></div><div class="body">{"".join(lines)}</div></main>\n</body>\n</html>\n',
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
        stable = re.sub(r"\?v=[0-9a-f]+", "", page.html)
        for text in page.volatile:
            stable = stable.replace(text, "")
        digest = sha256(stable)
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

