"""Render one page in one language to static HTML.

The markup vocabulary (hero, steps, faqsec, art, close, proof, g-strip, ...) is generic; the
project's theme stylesheet gives it its look. Pages need no JavaScript, except the waitlist form and
status page, which still work as plain form posts without it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from . import jsonld
from .data import Item, index_evidence
from .html import attrs, esc, inline, plain, safe_href, script_json
from .spec import PLACEHOLDER, PageLang, PageSpec

ICONS = {
    "check": '<path d="M5 12a7 7 0 1 0 14 0 7 7 0 0 0-14 0M9.5 12.5l2 2 3.5-4"/>',
    "play": '<path d="M9 6.5v11l8.5-5.5z"/>',
    "pad": '<path d="M7 9h10a3 3 0 0 1 3 3v2a3 3 0 0 1-5.4 1.8L13.5 14h-3l-1.1 1.8A3 3 0 0 1 4 14v-2a3 3 0 0 1 3-3zM8.5 11v3M7 12.5h3M15.5 12h.01M17 13.5h.01"/>',
    "mail": '<path d="M4 7h16v10H4zM4 7l8 6 8-6"/>',
    "disk": '<path d="M5 6h14v12H5zM8 14h8M8 10h3"/>',
    "moon": '<path d="M18 14.5A7 7 0 0 1 9.5 6a7 7 0 1 0 8.5 8.5z"/>',
}
PLUS = '<span class="pm" aria-hidden="true"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg></span>'
ARROW = '<span class="lpill-c"><svg class="glyph" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 17 17 7M9 7h8v8"/></svg></span>'
LASTMOD = "%%lastmod%%"
MONTHS = {
    "de": ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"],
    "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
}


def long_date(day: date, lang: str) -> str:
    names = MONTHS.get(lang, MONTHS["en"])
    if lang == "de":
        return f"{day.day}. {names[day.month - 1]} {day.year}"
    return f"{day.day} {names[day.month - 1]} {day.year}"


@dataclass
class Rendered:
    page_id: str
    lang: str
    path: str
    html: str
    indexable: bool
    alternates: dict[str, str]
    sitemap: bool = True
    volatile: list[str] = field(default_factory=list)


@dataclass
class SiteContext:
    """Everything a page may refer to: other pages, collection items, measurements, UI copy."""

    project: Any
    pages: dict[str, PageSpec]
    items: dict[str, list[Item]]
    measurements: dict[str, dict[str, Any]]
    ui: dict[str, dict[str, str]]
    today: date
    checked: date | None
    site_indexable: bool
    asset_version: str = ""
    media_used: set[str] = field(default_factory=set)
    live_languages: list[str] = field(default_factory=list)
    forms: int = 0

    def live(self, lang: str) -> bool:
        return not self.live_languages or lang in self.live_languages

    @property
    def base(self) -> str:
        return str(self.project.site["base_url"])

    def text(self, lang: str, key: str) -> str:
        table = self.ui.get(lang) or self.ui.get(self.project.default_language, {})
        if key not in table:
            raise KeyError(f"ui.toml [{lang}] is missing {key!r}")
        return table[key]

    def path_of(self, page_id: str, lang: str, item: Item | None = None) -> str:
        page = self.pages[page_id]
        spec = page.langs.get(lang) or page.langs[self.project.default_language]
        return spec.path.replace("{slug}", item.slug) if item else spec.path

    def href(self, ref: str, lang: str) -> str:
        target = self._target(ref, lang)
        if not safe_href(target):
            raise ValueError(f"link {ref!r} resolves to {target!r}: only /, #, https: and mailto: targets")
        return target

    def _target(self, ref: str, lang: str) -> str:
        if ref.startswith("page:"):
            target, _, frag = ref[5:].partition("#")
            return self.path_of(target, lang) + (f"#{frag}" if frag else "")
        if ref == "app:":
            return str(self.project.brand.get("app_url", "/"))
        if ref.startswith("legal:"):
            return self.legal_path(ref[6:], lang)
        if ref.startswith("item:"):
            collection, _, slug = ref[5:].partition("/")
            for item in self.items.get(collection, []):
                if item.slug == slug and item.has_page:
                    page_id = self.item_page(item)
                    if page_id:
                        return self.path_of(page_id, lang, item)
            hub = self.project.collections.get(collection, {}).get("hub")
            return self.path_of(hub, lang) if hub in self.pages else "/"
        return ref

    def legal_path(self, key: str, lang: str) -> str:
        """The legal notice or privacy page in this language: a page id, or a path for older configs."""
        value = str(self.project.site.get(key, "") or "")
        if value in self.pages:
            return self.path_of(value, lang)
        if not value:
            raise ValueError(f"[site] {key} is not set; sign-up forms and legal links need it")
        return value

    def item_page(self, item: Item) -> str | None:
        for page in self.pages.values():
            if page.kind == "item" and item.has_page and item in self.items.get(page.collection, []):
                return page.id
        return None


class PageRenderer:
    def __init__(self, ctx: SiteContext, page: PageSpec, lang: str, item: Item | None = None):
        self.ctx = ctx
        self.page = page
        self.lang = lang
        self.item = item
        self.spec: PageLang = page.langs[lang]
        self.faq: list[tuple[str, str]] = []
        self.videos: list[dict[str, Any]] = []
        self.has_waitlist = False
        self.has_beta = False
        self.anchors: set[str] = set()
        self.role = "host" if page.nav == "host" else "player"

    # ---- text ------------------------------------------------------------------------------

    def fill(self, text: str, wrap: Any = str) -> str:
        values: dict[str, Any] = {
            "brand": self.ctx.project.brand["name"],
            "app_url": self.ctx.project.brand.get("app_url", ""),
            "year": self.ctx.today.year,
            "move_up": self.ctx.project.raw.get("waitlist", {}).get("move_up_per_referral", 5),
            "checked": long_date(self.ctx.checked, self.lang) if self.ctx.checked else "",
        }
        values.update({f"legal.{k}": v for k, v in self.ctx.project.raw.get("legal", {}).items()})
        values.update(self.hardware_values())
        if self.item:
            values.update(self.item.record)
            values.update(
                name=self.item.name,
                slug=self.item.slug,
                short=self.item.record.get("short", self.item.name),
                status_label=self.status_label(),
            )

        def sub(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in values:
                raise KeyError(f"{self.page.file.name}: no value for {{{key}}}")
            return wrap(values[key])

        return PLACEHOLDER.sub(sub, text)

    def hardware_values(self) -> dict[str, str]:
        """{hardware.floor} and {hardware.models}: the supported hardware, named the same on every page."""
        conf = self.ctx.project.raw.get("hardware_check", {})
        if not conf:
            return {}
        floor = conf.get("floor", {})
        floor_text = floor.get(self.lang) or floor.get(self.ctx.project.default_language, "") if isinstance(floor, dict) else str(floor)
        models = [str(m) for m in conf.get("models", [])]
        joiner = self.ctx.ui.get(self.lang, {}).get("list_or", "or")
        listed = models[0] if len(models) == 1 else ", ".join(models[:-1]) + f" {joiner} " + models[-1] if models else ""
        prefix = str(conf.get("models_prefix", ""))
        return {"hardware.floor": floor_text, "hardware.models": (prefix + " " + listed).strip()}

    def field(self, value: Any) -> Any:
        """A section value; "@name" takes the item's field `name` instead."""
        if isinstance(value, str) and value.startswith("@"):
            return self.item.record.get(value[1:]) if self.item else None
        return value

    def md(self, text: str) -> str:
        """Copy with markup; filled-in values are shown as they are, never read as markup."""
        values: list[str] = []

        def hold(value: Any) -> str:
            values.append(esc(value))
            return f"\x01{len(values) - 1}\x01"

        out = inline(self.fill(text, hold), lambda ref: self.ctx.href(ref, self.lang))
        return re.sub(r"\x01(\d+)\x01", lambda m: values[int(m.group(1))], out)

    def status_label(self) -> str:
        assert self.item is not None
        key = "status_live" if self.item.offered else "status_paused"
        return self.ctx.text(self.lang, key)

    # ---- page ------------------------------------------------------------------------------

    def indexable(self) -> bool:
        if not self.ctx.site_indexable or self.page.always_noindex:
            return False
        if self.page.index_if_any and self.item:
            return bool(index_evidence(self.item) & set(self.page.index_if_any))
        if self.page.index_if_any and not self.item:
            measured = set()
            for section in self.spec.sections:
                record = self.ctx.measurements.get(str(section.get("measurement", "")))
                if record:
                    measured.add("measured")
                    if record.get("clip"):
                        measured.add("clip")
            return bool(measured & set(self.page.index_if_any))
        return True

    def alternates(self) -> dict[str, str]:
        return {lang: self.ctx.path_of(self.page.id, lang, self.item) for lang in self.page.langs if self.ctx.live(lang)}

    def hreflang(self, indexable: bool) -> dict[str, str]:
        """The indexable language versions, plus x-default, when there is more than one."""
        if not indexable:
            return {}
        found = {
            lang: path
            for lang, path in self.alternates().items()
            if lang == self.lang or PageRenderer(self.ctx, self.page, lang, self.item).indexable()
        }
        if len(found) < 2:
            return {}
        default = found.get(self.ctx.project.default_language)
        return {**found, "x-default": default} if default else found

    def render(self) -> Rendered:
        body = "\n".join(self.section(s, i) for i, s in enumerate(self.spec.sections))
        indexable = self.indexable()
        path = self.ctx.path_of(self.page.id, self.lang, self.item)
        doc = "\n".join(
            [
                "<!doctype html>",
                f'<html lang="{esc(self.lang)}">',
                "<head>",
                self.head(path, indexable),
                "</head>",
                f'<body id="top" class="{esc("page-" + self.page.kind)}">',
                f'<a class="skip" href="#main">{esc(self.ctx.text(self.lang, "skip"))}</a>',
                self.nav(),
                f'<main id="main">\n{body}\n</main>',
                self.foot(),
                self.scripts(),
                "</body>",
                "</html>",
                "",
            ]
        )
        return Rendered(
            page_id=self.page.id,
            lang=self.lang,
            path=path,
            html=doc,
            indexable=indexable,
            alternates=self.hreflang(indexable),
            sitemap=self.page.in_sitemap and indexable,
            volatile=[long_date(self.ctx.checked, self.lang)] if self.ctx.checked else [],
        )

    def head(self, path: str, indexable: bool) -> str:
        base = self.ctx.base
        title = plain(self.fill(self.spec.title))
        description = self.description(indexable)
        og_title = plain(self.fill(self.spec.og_title or self.spec.title))
        og_desc = plain(self.fill(self.spec.og_description or description))
        share = self.ctx.project.site.get("share_image", "")
        if isinstance(share, dict):
            share = share.get(self.lang) or share.get(self.ctx.project.default_language, "")
        lines = [
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">',
            f"<title>{esc(title)}</title>",
            f'<meta name="description" content="{esc(description)}">',
            f'<link rel="canonical" href="{esc(base + path)}">',
        ]
        if not indexable:
            lines.append('<meta name="robots" content="noindex, follow">')
        for lang, alt in self.hreflang(indexable).items():
            lines.append(f'<link rel="alternate" hreflang="{esc(lang)}" href="{esc(base + alt)}">')
        lines += [
            f'<meta property="og:type" content="{"article" if self.page.kind in {"guide", "item"} else "website"}">',
            f'<meta property="og:title" content="{esc(og_title)}">',
            f'<meta property="og:description" content="{esc(og_desc)}">',
            f'<meta property="og:url" content="{esc(base + path)}">',
            f'<meta property="og:locale" content="{esc(self.ctx.text(self.lang, "locale"))}">',
        ]
        if share:
            self.ctx.media_used.add(share)
            lines.append(f'<meta property="og:image" content="{esc(base + "/" + share)}">')
            lines.append('<meta name="twitter:card" content="summary_large_image">')
        theme_color = self.ctx.project.site.get("theme_color", "")
        if theme_color:
            lines.append(f'<meta name="theme-color" content="{esc(theme_color)}">')
        for font in self.ctx.project.site.get("preload_fonts", []):
            lines.append(f'<link rel="preload" href="/{esc(font)}" as="font" type="font/woff2" crossorigin>')
        lines.append(f'<link rel="stylesheet" href="/assets/site.css{self.ctx.asset_version}">')
        lines.append(f'<script type="application/ld+json">\n{self.jsonld(path)}\n</script>')
        return "\n".join(lines)

    def description(self, indexable: bool) -> str:
        text = self.spec.description
        if self.item and self.item.record.get("description"):
            text = str(self.item.record["description"])
        if not indexable and self.spec.description_draft:
            text = self.spec.description_draft
        return plain(self.fill(text))

    def absolute(self, href: str) -> str | None:
        if href.startswith("/") and not href.startswith("//"):
            return self.ctx.base + href
        return href if href.startswith("https:") else None

    def jsonld(self, path: str) -> str:
        base = self.ctx.base
        brand = str(self.ctx.project.brand["name"])
        nodes: list[dict[str, Any]] = [jsonld.organization(brand, base + "/"), jsonld.website(brand, base + "/", self.lang)]
        if self.spec.crumbs:
            trail = [(plain(self.fill(c["text"])), self.absolute(self.ctx.href(c["href"], self.lang)) if c.get("href") else None) for c in self.spec.crumbs]
            nodes.append(jsonld.breadcrumbs(trail))
        if self.faq:
            nodes.append(jsonld.faq(self.faq, self.lang))
        if self.page.kind in {"guide", "item"}:
            modified = self.page.updated or LASTMOD
            nodes.append(jsonld.article(plain(self.fill(self.spec.title)), base + path, self.lang, modified, base + "/"))
        nodes += self.videos
        return jsonld.graph(nodes)

    def nav(self) -> str:
        t = lambda key: self.ctx.text(self.lang, key)  # noqa: E731
        home = self.ctx.path_of(self.ctx.project.site.get("home", "player"), self.lang)
        links = []
        if "so-gehts" in self.anchors:
            links.append(f'<a href="#so-gehts">{esc(t("nav_how"))}</a>')
        if "fragen" in self.anchors:
            links.append(f'<a href="#fragen">{esc(t("nav_faq"))}</a>')
        langs = ""
        alternates = self.alternates()
        if len(alternates) > 1:
            buttons = []
            for lang, alt in alternates.items():
                current = ' aria-current="true"' if lang == self.lang else ""
                buttons.append(f'<a href="{esc(alt)}" hreflang="{esc(lang)}" lang="{esc(lang)}"{current}>{esc(lang.upper())}</a>')
            langs = f'<div class="lang" role="group" aria-label="{esc(t("lang_label"))}" data-lang-switch>{"".join(buttons)}</div>'
        cta_key = "host_cta" if self.role == "host" else "cta"
        target = "#beta" if self.has_beta else self.ctx.path_of(self.ctx.project.site.get("home", "player"), self.lang) + "#beta"
        if self.role == "host" and not self.has_beta:
            target = self.ctx.path_of(self.ctx.project.site.get("host_home", "host"), self.lang) + "#beta"
        cta = (
            f'<a class="lpill lpill-sm solid nav-cta" href="{esc(target)}">'
            f'<span class="long">{esc(t(cta_key))}</span><span class="short">{esc(t(cta_key + "_short"))}</span></a>'
        )
        if self.page.kind in {"waitlist", "legal"}:
            cta = ""  # the status page carries its own form; legal pages sell nothing
        nav_links = f'<nav class="nav-links" aria-label="{esc(t("sections_label"))}">{"".join(links)}</nav>' if links else '<span class="nav-links"></span>'
        wordmark = esc(self.ctx.project.brand.get("wordmark", self.ctx.project.brand["name"]))
        return (
            '<header class="nav">\n'
            f'  <a class="wordmark" href="{esc(home)}" aria-label="{esc(self.fill(t("home_label")))}">{wordmark}</a>\n'
            f"  {nav_links}\n  {langs}\n  {cta}\n</header>"
        )

    def foot(self) -> str:
        t = lambda key: self.ctx.text(self.lang, key)  # noqa: E731
        cross_page = self.ctx.project.site.get("home", "player") if self.role == "host" else self.ctx.project.site.get("host_home", "host")
        cross = ""
        if cross_page in self.ctx.pages:
            key = "foot_player" if self.role == "host" else "foot_host"
            cross = (
                f'<p class="host-link">{esc(self.fill(t(key)))} '
                f'<a href="{esc(self.ctx.path_of(cross_page, self.lang))}">{esc(self.fill(t(key + "_link")))}</a></p>'
            )
        legal_links = []
        for key in ("legal_notice", "privacy"):
            if self.ctx.project.site.get(key):
                legal_links.append(f'<a href="{esc(self.ctx.legal_path(key, self.lang))}">{esc(t(key))}</a>')
        guides = []
        for page_id in self.ctx.project.site.get("footer_links", []):
            page = self.ctx.pages.get(page_id)
            if page is None or self.lang not in page.langs or page_id == self.page.id:
                continue
            title = plain(self.fill(page.langs[self.lang].title)).split(" | ")[0]
            guides.append(f'<li><a href="{esc(self.ctx.path_of(page_id, self.lang))}">{esc(title)}</a></li>')
        guide_list = f'<ul class="foot-guides" aria-label="{esc(t("footer_guides"))}">{"".join(guides)}</ul>' if guides else ""
        wordmark = esc(self.ctx.project.brand.get("wordmark", self.ctx.project.brand["name"]))
        return (
            '<footer class="foot pfoot">\n'
            f'  <span class="wordmark">{wordmark}</span>\n'
            f'  <div>{cross}{guide_list}<p class="foot-legal">{self.md(t("foot_legal_" + self.role))}</p></div>\n'
            f'  <nav aria-label="{esc(t("legal_label"))}">{"".join(legal_links)}</nav>\n'
            "</footer>"
        )

    def scripts(self) -> str:
        if not self.ctx.project.raw.get("waitlist"):
            return ""
        brand = str(self.ctx.project.brand["name"])
        # Client strings keep their own placeholders ({n}, {url}, {move_up}, {email}); only the brand is filled here.
        strings = {k[3:]: v.replace("{brand}", brand) for k, v in self.ctx.ui.get(self.lang, {}).items() if k.startswith("js_")}
        analytics = self.ctx.project.analytics.get("provider") == "posthog" and bool(self.ctx.project.analytics.get("project_api_key"))
        settings = {"lang": self.lang, "page": self.page.id, "role": self.role, "analytics": analytics}
        return (
            f'<script type="application/json" id="wl-strings">{script_json(strings)}</script>\n'
            f'<script type="application/json" id="wl-settings">{script_json(settings)}</script>\n'
            f'<script src="/assets/waitlist.js{self.ctx.asset_version}" defer></script>'
        )

    # ---- sections ---------------------------------------------------------------------------

    def section(self, section: dict[str, Any], index: int) -> str:
        if section.get("id"):
            self.anchors.add(str(section["id"]))
        method = getattr(self, "s_" + section["type"].replace("-", "_"))
        return method(section, index)

    def heading_id(self, section: dict[str, Any], index: int) -> str:
        return esc(f"h-{section.get('id') or index + 1}")

    def s_hero(self, s: dict[str, Any], i: int) -> str:
        lines = "".join(f"<span>{self.md(line)}</span>" for line in s["lines"])
        texts = [plain(self.fill(line)) for line in s["lines"]]
        longest_line = max(len(t) for t in texts)
        longest_word = max(len(w) for t in texts for w in t.split())
        sub = f'<p class="sub">{self.md(s["sub"])}</p>' if s.get("sub") else ""
        cta = self.cta(s.get("cta", "waitlist"), "hero")
        return (
            '<section class="hero" aria-labelledby="h1">\n'
            '  <div class="hero-art" aria-hidden="true"></div>\n'
            '  <div class="hero-inner">\n'
            f'    <div class="promise" style="--chars:{longest_line};--word:{longest_word}"><div class="draft">'
            '<span class="dim" aria-hidden="true"></span>'
            f'<h1 id="h1">{lines}</h1><span class="mark" aria-hidden="true"></span></div></div>\n'
            f'    <div class="cell on-paper">{sub}{cta}</div>\n'
            "  </div>\n</section>"
        )

    def s_intro(self, s: dict[str, Any], i: int) -> str:
        crumbs = self.crumbs()
        lead = "".join(f"<p>{self.md(p)}</p>" for p in s.get("lead", []))
        updated = f'<p class="updated">{self.md(s["updated"])}</p>' if s.get("updated") else ""
        return (
            '<section class="art intro" aria-labelledby="h1">\n'
            f'  <div>{crumbs}<h1 id="h1">{self.md(s["h1"])}</h1></div>\n'
            f'  <div class="body">{lead}{updated}</div>\n</section>'
        )

    def crumbs(self) -> str:
        if not self.spec.crumbs:
            return ""
        parts = []
        for crumb in self.spec.crumbs:
            text = esc(plain(self.fill(crumb["text"])))
            parts.append(f'<a href="{esc(self.ctx.href(crumb["href"], self.lang))}">{text}</a>' if crumb.get("href") else text)
        return f'<p class="crumbs">{" › ".join(parts)}</p>'

    def s_tiles(self, s: dict[str, Any], i: int) -> str:
        hid = self.heading_id(s, i)
        cards = self.field(s.get("cards", []))
        if s.get("cards") == "@similar" and self.item:
            cards = [{"title": x.name, "href": self.item_href(x)} for x in self.item.similar]
        if not cards:
            return ""
        tiles = []
        for n, card in enumerate(cards):
            title = self.md(card["title"]) if isinstance(card.get("title"), str) else esc(card.get("title"))
            inner = f'<span class="card" aria-hidden="true"><span>{esc(plain(self.fill(card["title"])))}</span></span><figcaption>{title}</figcaption>'
            if card.get("href"):
                inner = f'<a href="{esc(self.ctx.href(card["href"], self.lang))}">{inner}</a>'
            tiles.append(f'<figure style="--i:{n}">{inner}</figure>')
        copy = "".join(f"<p>{self.md(p)}</p>" for p in s.get("text", []))
        link = ""
        if s.get("link"):
            lk = s["link"]
            after = f' <span>{self.md(lk["after"])}</span>' if lk.get("after") else ""
            link = f'<p class="lib-check"><a href="{esc(self.ctx.href(lk["href"], self.lang))}">{self.md(lk["text"])}</a>{after}</p>'
        return (
            f'<section class="library" aria-labelledby="{hid}">\n'
            f'  <div class="lib-copy"><h2 id="{hid}">{self.md(s["h2"])}</h2>{copy}{link}</div>\n'
            f'  <div class="lib-tiles">{"".join(tiles)}</div>\n</section>'
        )

    def item_href(self, item: Item) -> str | None:
        page_id = self.ctx.item_page(item)
        return self.ctx.path_of(page_id, self.lang, item) if page_id else None

    def s_proof(self, s: dict[str, Any], i: int) -> str:
        hid = self.heading_id(s, i)
        mid = self.field(s["measurement"])
        record = self.ctx.measurements.get(str(mid)) if mid else None
        if self.item and s["measurement"] == "@measurement":
            record = self.item.measurement
        if not record:
            return ""  # no empty video slot and no "value to come": the section waits for a real recording
        t = lambda key: self.ctx.text(self.lang, key)  # noqa: E731
        stats = []
        for key, label in (("resolution", "proof_res"), ("fps", "proof_fps"), ("delay_ms", "proof_delay")):
            value = record.get(key)
            if value is not None:
                stats.append(f"<div><dt>{esc(t(label))}</dt><dd>{esc(_measure(key, value))}</dd></div>")
        clip = record.get("clip")
        if clip:
            self.ctx.media_used.add(clip)
            poster = record.get("poster", "")
            if poster:
                self.ctx.media_used.add(poster)
            media = (
                f'<video controls preload="none" playsinline src="/{esc(clip)}"'
                + (f' poster="/{esc(poster)}"' if poster else "")
                + f' aria-label="{esc(plain(self.fill(s["h2"])))}"></video>'
            )
            if poster and record.get("recorded_at"):
                self.videos.append(
                    jsonld.video(
                        plain(self.fill(s["h2"])),
                        plain(self.fill(s.get("text", ""))) or self.description(self.indexable()),
                        self.ctx.base + "/" + clip,
                        self.ctx.base + "/" + poster,
                        str(record["recorded_at"]),
                    )
                )
        else:
            media = ""  # measured numbers without a recording: show the numbers, not an empty video slot
        text = self.md(s["text"]) if (clip and s.get("text")) else self.md(s.get("text_measured", s.get("text_pending", s.get("text", ""))))
        clip_box = f'  <div class="proof-clip">{media}</div>\n' if clip else ""
        return (
            f'<section class="proof{"" if clip else " numbers-only"}" aria-labelledby="{hid}">\n'
            f"{clip_box}"
            f'  <div class="proof-copy"><h2 id="{hid}">{self.md(s["h2"])}</h2><p>{text}</p>'
            f'<dl class="proof-stats">{"".join(stats)}</dl></div>\n</section>'
        )

    def s_steps(self, s: dict[str, Any], i: int) -> str:
        hid = self.heading_id(s, i)
        items = []
        for n, step in enumerate(s["items"]):
            icon = ICONS.get(step.get("icon", "check"), ICONS["check"])
            items.append(
                f'<li style="--i:{n}"><span class="step-n" aria-hidden="true"><svg width="20" height="20" viewBox="0 0 24 24" '
                f'fill="none" stroke="currentColor" stroke-width="1.1" stroke-linecap="round" stroke-linejoin="round">{icon}</svg></span>'
                f"<h3>{self.md(step['h'])}</h3><p>{self.md(step['p'])}</p></li>"
            )
        return (
            f'<section class="steps"{attrs(id=s.get("id"))} aria-labelledby="{hid}">\n'
            f'  <h2 id="{hid}">{self.md(s["h2"])}</h2>\n  <ol class="step-row">{"".join(items)}</ol>\n</section>'
        )

    def s_features(self, s: dict[str, Any], i: int) -> str:
        hid = self.heading_id(s, i)
        lead = f'<p class="lead">{self.md(s["lead"])}</p>' if s.get("lead") else ""
        items = "".join(f"<li><h3>{self.md(x['h'])}</h3><p>{self.md(x['p'])}</p></li>" for x in s["items"])
        paper = " paper on-paper" if s.get("paper") else ""
        return (
            f'<section class="art features{paper}"{attrs(id=s.get("id"))} aria-labelledby="{hid}">\n'
            f'  <div><h2 id="{hid}">{self.md(s["h2"])}</h2>{lead}</div>\n'
            f'  <div class="body"><ul class="feature-list">{items}</ul></div>\n</section>'
        )

    def s_spec(self, s: dict[str, Any], i: int) -> str:
        hid = self.heading_id(s, i)
        rows = "".join(f"<dt>{self.md(r['label'])}</dt><dd>{self.md(r['value'])}</dd>" for r in s["rows"])
        note = f'<p class="note">{self.md(s["note"])}</p>' if s.get("note") else ""
        return (
            f'<section class="art spec"{attrs(id=s.get("id"))} aria-labelledby="{hid}">\n'
            f'  <div><h2 id="{hid}">{self.md(s["h2"])}</h2></div>\n'
            f'  <div class="body"><dl class="facts">{rows}</dl>{note}</div>\n</section>'
        )

    def s_faq(self, s: dict[str, Any], i: int) -> str:
        hid = self.heading_id(s, i)
        items = self.field(s["items"]) or s.get("fallback", [])
        rows = []
        for qa in items:
            question, answer = self.fill(qa["q"]), self.fill(qa["a"])
            self.faq.append((plain(question), plain(answer)))
            rows.append(
                f"<details><summary><span>{self.md(qa['q'])}</span>{PLUS}</summary><p>{self.md(qa['a'])}</p></details>"
            )
        return (
            f'<section class="faqsec"{attrs(id=s.get("id", "fragen"))} aria-labelledby="{hid}">\n'
            f'  <h2 id="{hid}">{self.md(s["h2"])}</h2>\n  <div class="faq">{"".join(rows)}</div>\n</section>'
        ) if rows else ""

    def s_article(self, s: dict[str, Any], i: int) -> str:
        hid = self.heading_id(s, i)
        body = self.field(s["body"])
        if not body:
            body = s.get("fallback", [])
        paper = " paper on-paper" if s.get("paper") else ""
        return (
            f'<section class="art{paper}"{attrs(id=s.get("id"))} aria-labelledby="{hid}">\n'
            f'  <div><h2 id="{hid}">{self.md(s["h2"])}</h2></div>\n'
            f'  <div class="body">{self.blocks(list(body) + list(s.get("after", [])))}</div>\n</section>'
        )

    def blocks(self, blocks: list[dict[str, Any]]) -> str:
        out = []
        for block in blocks:
            if "p" in block:
                out.append(f"<p>{self.md(block['p'])}</p>")
            elif "h3" in block:
                out.append(f"<h3>{self.md(block['h3'])}</h3>")
            elif "ul" in block:
                out.append("<ul>" + "".join(f"<li>{self.md(x)}</li>" for x in block["ul"]) + "</ul>")
            elif "table" in block:
                table = block["table"]
                head = "".join(f"<th>{self.md(h)}</th>" for h in table["head"])
                rows = []
                for n, row in enumerate(table["rows"]):
                    cls = ' class="us"' if table.get("highlight") == n else ""
                    rows.append(f"<tr{cls}>" + "".join(f"<td>{self.md(c)}</td>" for c in row) + "</tr>")
                out.append(f'<div class="tbl-wrap"><table class="cmp"><tr>{head}</tr>{"".join(rows)}</table></div>')
            elif "button" in block:
                btn = block["button"]
                out.append(
                    f'<p class="btn-row"><a class="lpill solid" href="{esc(self.ctx.href(btn["href"], self.lang))}">'
                    f'<span>{self.md(btn["text"])}</span>{ARROW}</a></p>'
                )
            elif "updated" in block:
                out.append(f'<p class="updated">{self.md(block["updated"])}</p>')
            elif "checked" in block:
                if self.ctx.checked:
                    out.append(f'<p class="updated">{self.md(block["checked"])}</p>')
            else:
                raise ValueError(f"{self.page.file.name}: unknown body block {sorted(block)}")
        return "".join(out)

    def s_close(self, s: dict[str, Any], i: int) -> str:
        hid = self.heading_id(s, i)
        self.has_beta = True
        return (
            f'<section class="close" id="beta" aria-labelledby="{hid}">\n'
            f'  <div><h2 id="{hid}">{self.md(s["h2"])}</h2></div>\n'
            f'  <div>{self.cta(s.get("cta", "waitlist"), "close")}</div>\n</section>'
        )

    def s_item_hero(self, s: dict[str, Any], i: int) -> str:
        assert self.item is not None
        return (
            f'<section class="g-hero" aria-label="{esc(self.item.name)}">\n'
            '  <div class="hero-art" aria-hidden="true"></div>\n'
            f'  <div class="g-hero-in">{self.crumbs()}<p class="g-title" aria-hidden="true">{esc(self.item.name)}</p></div>\n'
            "</section>"
        )

    def s_item_strip(self, s: dict[str, Any], i: int) -> str:
        assert self.item is not None
        rows = [("status", self.status_label(), "ok" if self.item.offered else "no")]
        for fact in self.item.record.get("facts", []):
            rows.append((fact["label"], fact["value"], fact.get("tone", "")))
        dl = "".join(
            f"<dt>{esc(self.fill(self.ctx.text(self.lang, 'fact_status')) if label == 'status' else self.fill(label))}</dt>"
            f"<dd{attrs(class_=tone or None)}>{'<b>' + esc(value) + '</b>' if label == 'status' else self.md(value)}</dd>"
            for label, value, tone in rows
        )
        verdict_text = self.field(s.get("verdict", "")) or s.get("verdict_fallback", "")
        verdict = self.md(verdict_text) if verdict_text else ""
        if not self.item.offered:
            if self.item.status == "blocked" and self.item.record.get("reason"):
                verdict = self.md(str(self.item.record["reason"]))
            elif self.item.native and s.get("verdict_native"):
                verdict = self.md(s["verdict_native"])
            elif s.get("verdict_paused"):
                verdict = self.md(s["verdict_paused"])
        self.has_beta = True
        return (
            '<section class="g-strip" aria-labelledby="h1">\n'
            f'  <div><h1 id="h1">{self.md(s["h1"])}</h1><p class="verdict">{verdict}</p></div>\n'
            f'  <div><dl class="facts">{dl}</dl></div>\n'
            f'  <div id="beta" class="on-paper">{self.cta(s.get("cta", "waitlist"), "hero")}</div>\n</section>'
        )

    def s_listing(self, s: dict[str, Any], i: int) -> str:
        out = []
        collection = s.get("collection", next(iter(self.ctx.items), ""))
        items = self.ctx.items.get(collection, [])
        for n, group in enumerate(s["groups"]):
            chosen = _select(items, group["source"], self.ctx.today, int(group.get("days", 14)))
            hid = f"h-list-{n + 1}"
            if chosen:
                rows = []
                for item in chosen:
                    href = self.item_href(item)
                    name = f'<a href="{esc(href)}">{esc(item.name)}</a>' if href else esc(item.name)
                    meta = self.row_meta(item, group["source"])
                    rows.append(f"<li><span class=\"name\">{name}</span>{meta}</li>")
                content = f'<ul class="game-list">{"".join(rows)}</ul>'
            else:
                content = f'<p>{self.md(group.get("empty", ""))}</p>'
            intro = f"<p>{self.md(group['text'])}</p>" if group.get("text") else ""
            out.append(
                f'<section class="art listing" aria-labelledby="{hid}">\n'
                f'  <div><h2 id="{hid}">{self.md(group["h2"])}</h2></div>\n'
                f'  <div class="body">{intro}{content}</div>\n</section>'
            )
        return "\n".join(out)

    def row_meta(self, item: Item, source: str) -> str:
        if source == "blocked":
            return f'<span class="meta">{self.md(str(item.record.get("reason", "")))}</span>'
        bits = []
        if item.free and source != "free":
            bits.append(self.ctx.text(self.lang, "free"))
        signin = item.record.get("signin")
        if signin:
            bits.append(self.fill(self.ctx.text(self.lang, "needs_signin").replace("{signin}", str(signin))))
        return f'<span class="meta">{esc(" · ".join(bits))}</span>' if bits else ""

    def s_game_search(self, s: dict[str, Any], i: int) -> str:
        """Search every checked item; no match offers the waitlist form ("tell me when it's in")."""
        hid = self.heading_id(s, i)
        t = lambda key: self.ctx.text(self.lang, key)  # noqa: E731
        collection = s.get("collection", next(iter(self.ctx.items), ""))
        hub = self.ctx.project.collections.get(collection, {}).get("hub")
        fallback = f'<noscript><p><a href="{esc(self.ctx.path_of(hub, self.lang))}">{esc(t("search_all"))}</a></p></noscript>' if hub in self.ctx.pages else ""
        sid = f"search-{i + 1}"
        text = f"<p>{self.md(s['text'])}</p>" if s.get("text") else ""
        return (
            f'<section class="art search" aria-labelledby="{hid}" data-game-search="/data/{esc(collection)}-{esc(self.lang)}.json">\n'
            f'  <div><h2 id="{hid}">{self.md(s["h2"])}</h2>{text}</div>\n'
            '  <div class="body">\n'
            f'    <label class="wl-label" for="{sid}">{esc(t("search_label"))}</label>\n'
            f'    <input id="{sid}" class="search-input" type="search" autocomplete="off" spellcheck="false" placeholder="{esc(plain(t("search_placeholder")))}" data-search-input>\n'
            '    <ul class="search-results" data-search-results aria-live="polite"></ul>\n'
            f'    <div class="search-miss on-paper" data-search-miss hidden><p class="search-miss-h">{esc(t("search_miss"))}</p>'
            f'{self.cta("waitlist", sid)}</div>\n'
            f"    {fallback}\n"
            "  </div>\n</section>"
        )

    def s_hardware_check(self, s: dict[str, Any], i: int) -> str:
        """Tells the visitor at once whether their graphics card fits. Runs in the browser; nothing is sent."""
        conf = self.ctx.project.raw.get("hardware_check", {})
        t = lambda key: self.ctx.text(self.lang, key)  # noqa: E731
        rules = {
            "models": [str(m) for m in conf.get("models", [])],
            "not_yet": [str(m) for m in conf.get("not_yet", [])],
            "not_host": [str(m) for m in conf.get("not_host", [])],
            "family": [str(m) for m in conf.get("family", [])],
        }
        # {model} is filled in the browser with the detected card; everything else is filled here.
        messages = {key: self.fill(t("hw_" + key).replace("{model}", "\x02")).replace("\x02", "{model}") for key in ("fits", "not_yet", "not_host", "below")}
        return (
            f'<div class="hw-check" data-hardware-check hidden>'
            f'<script type="application/json" data-hw-rules>{script_json({"rules": rules, "messages": messages})}</script>'
            '<p class="hw-line" data-hw-line aria-live="polite"></p></div>'
        )

    def s_waitlist_status(self, s: dict[str, Any], i: int) -> str:
        self.has_waitlist = True
        t = lambda key: self.ctx.text(self.lang, key)  # noqa: E731
        form = self.cta("host-waitlist" if self.role == "host" else "waitlist", "status")
        return (
            f'<section class="wl-status on-paper" data-waitlist-status data-role="{self.role}">\n'
            f'  {self.crumbs()}<h1 id="h1" data-status-title>{esc(t("status_title_" + self.role))}</h1>\n'
            f'  <p data-status-text aria-live="polite">{esc(t("status_loading"))}</p>\n'
            '  <div data-status-card hidden>\n'
            '    <dl class="facts"><dt data-k="position"></dt><dd data-v="position"></dd>'
            '<dt data-k="referrals"></dt><dd data-v="referrals"></dd></dl>\n'
            f'    <h2>{esc(t("share_title_" + self.role))}</h2><p>{self.md(t("share_text_" + self.role))}</p>\n'
            f'    <div class="share"><input data-invite readonly aria-label="{esc(t("invite_label"))}">'
            f'<button type="button" class="lpill lpill-sm solid" data-copy>{esc(t("share_copy"))}</button></div>\n'
            '    <div class="share-buttons" data-share-buttons></div>\n'
            f'    <p class="note"><a href="#" data-leave>{esc(t("leave"))}</a></p>\n'
            "  </div>\n"
            f'  <div class="wl-status-form" data-status-form>{form}</div>\n'
            f'  <noscript><p>{esc(t("status_noscript"))}</p></noscript>\n</section>'
        )

    def cta(self, kind: str, where: str) -> str:
        if kind == "none":
            return ""
        self.has_waitlist = True
        self.ctx.forms += 1
        role = "host" if kind == "host-waitlist" else "player"
        t = lambda key: self.ctx.text(self.lang, key)  # noqa: E731
        fid = f"wl-{where}"
        prefix = "host_" if role == "host" else ""
        button = t("host_cta") if role == "host" else t("cta")
        counter_key = "counter_" + role
        return (
            f'<form class="wl" data-waitlist action="/api/waitlist" method="post" novalidate>\n'
            f'  <input type="hidden" name="role" value="{role}"><input type="hidden" name="lang" value="{esc(self.lang)}">'
            '<input type="hidden" name="ref" value=""><input type="hidden" name="src" value="">'
            f'<input type="hidden" name="page" value="{esc(self.page.id)}">\n'
            '  <p class="hp" aria-hidden="true"><label>Website <input name="website" tabindex="-1" autocomplete="off"></label></p>\n'
            f'  <p class="wl-ref" data-ref-note hidden>{esc(self.fill(t("ref_note_" + role)))}</p>\n'
            '  <div class="wl-state">\n'
            f'    <label class="wl-label" for="{fid}">{esc(t(prefix + "wl_label"))}</label>\n'
            f'    <div class="wl-row"><input id="{fid}" name="email" type="email" autocomplete="email" inputmode="email" '
            f'spellcheck="false" required placeholder="{esc(plain(t("wl_placeholder")))}" aria-describedby="{fid}-help">'
            f'<button class="lpill solid" type="submit"><span>{esc(button)}</span>{ARROW}</button></div>\n'
            f'    <p class="wl-err" role="alert" hidden>{esc(t("wl_error"))}</p>\n'
            f'    <p class="wl-help" id="{fid}-help">{self.md(t(prefix + "wl_help"))}</p>\n'
            f'    <p class="wl-count" data-waitlist-count="{role}" hidden>{esc(t(counter_key).replace("{brand}", str(self.ctx.project.brand["name"])))}</p>\n'
            "  </div>\n"
            f'  <div class="wl-done" hidden tabindex="-1"><p class="wl-done-h">{esc(t("wl_done_title"))}</p>'
            f'<p data-done-text>{esc(t("wl_done_text"))}</p><p class="wl-help">{esc(t("wl_spam_hint"))}</p>'
            f'<p><button type="button" class="linkish" data-wrong-address>{esc(t("wl_wrong_address"))}</button></p></div>\n'
            "</form>"
        )


def _measure(key: str, value: Any) -> str:
    if key == "fps":
        return f"{value} fps"
    if key == "delay_ms":
        return f"{value} ms"
    return str(value)


def _select(items: list[Item], source: str, today: date, days: int) -> list[Item]:
    def demand(item: Item) -> tuple[int, str]:
        return (-int(item.record.get("searches", 0)), item.name.lower())

    if source == "playable":
        chosen = [x for x in items if x.status == "playable" and x.live]
    elif source == "free":
        chosen = [x for x in items if x.status == "playable" and x.live and x.free]
    elif source == "new":
        chosen = [
            x
            for x in items
            if x.status == "playable" and x.live and x.first_live[:1].isdigit() and (today - date.fromisoformat(x.first_live)).days <= days
        ]
    elif source == "blocked":
        chosen = [x for x in items if x.status == "blocked"]
    elif source == "native":
        chosen = [x for x in items if x.status == "native"]
    else:
        raise ValueError(source)
    return sorted(chosen, key=demand)
