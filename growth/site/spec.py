"""Page specs: one TOML file per page in the project's pages folder, validated before any build.

A page has a `[langs.<lang>]` table per language it exists in, each with its own path, title,
description and list of sections. Copy is written per language (not translated by the engine).
An item template (`kind = "item"`) is rendered once per item of a collection, e.g. one page per game.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..util import read_toml

if TYPE_CHECKING:
    from ..config import Project

KINDS = {"landing", "guide", "hub", "item", "waitlist"}
SECTION_FIELDS: dict[str, tuple[str, ...]] = {
    "hero": ("lines",),
    "intro": ("h1",),
    "tiles": ("h2",),
    "proof": ("h2", "measurement"),
    "steps": ("h2", "items"),
    "features": ("h2", "items"),
    "spec": ("h2", "rows"),
    "faq": ("h2", "items"),
    "article": ("h2", "body"),
    "close": ("h2",),
    "item-hero": (),
    "item-strip": ("h1",),
    "listing": ("groups",),
    "waitlist-status": (),
}
CTAS = {"waitlist", "host-waitlist", "none"}
PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_.]*)\}")
SITE_KEYS = {"brand", "app_url", "year", "move_up", "checked"}


@dataclass
class PageLang:
    lang: str
    path: str
    title: str
    description: str
    sections: list[dict[str, Any]]
    og_title: str = ""
    og_description: str = ""
    description_draft: str = ""
    crumbs: list[dict[str, str]] = field(default_factory=list)


@dataclass
class PageSpec:
    id: str
    kind: str
    file: Path
    langs: dict[str, PageLang]
    collection: str = ""
    index_if_any: list[str] = field(default_factory=list)
    always_noindex: bool = False
    updated: str = ""
    nav: str = "player"
    in_sitemap: bool = True


def load_pages(project: "Project") -> dict[str, PageSpec]:
    folder = project.root / project.site.get("pages", "pages")
    pages: dict[str, PageSpec] = {}
    for file in sorted(folder.glob("*.toml")) if folder.is_dir() else []:
        raw = read_toml(file)
        pid = str(raw.get("id", file.stem))
        langs = {}
        for lang, conf in raw.get("langs", {}).items():
            langs[lang] = PageLang(
                lang=lang,
                path=str(conf.get("path", "")),
                title=str(conf.get("title", "")),
                description=str(conf.get("description", "")),
                sections=list(conf.get("sections", [])),
                og_title=str(conf.get("og_title", "")),
                og_description=str(conf.get("og_description", "")),
                description_draft=str(conf.get("description_draft", "")),
                crumbs=list(conf.get("crumbs", [])),
            )
        pages[pid] = PageSpec(
            id=pid,
            kind=str(raw.get("kind", "landing")),
            file=file,
            langs=langs,
            collection=str(raw.get("collection", "")),
            index_if_any=list(raw.get("index_if_any", [])),
            always_noindex=bool(raw.get("noindex", False)),
            updated=str(raw.get("updated", "")),
            nav=str(raw.get("nav", "player")),
            in_sitemap=bool(raw.get("sitemap", True)),
        )
    return pages


def load_keywords(project: "Project") -> list[dict[str, Any]]:
    path = project.root / project.site.get("keywords", "keywords.toml")
    return list(read_toml(path).get("cluster", [])) if path.is_file() else []


def validate_site(project: "Project") -> list[str]:
    """Every problem with the project's pages, keywords and data, as messages."""
    errors: list[str] = []
    where = f"projects/{project.id}"
    if not project.channel_enabled("website"):
        return errors
    try:
        pages = load_pages(project)
    except Exception as exc:  # a TOML syntax error names the file
        return [f"{where}: pages: {exc}"]
    if not pages:
        return [f"{where}: no page specs in {project.site.get('pages', 'pages')}/"]

    brand = str(project.brand.get("name", ""))
    forbidden_chars = list(project.site.get("forbid_chars", []))
    seen_paths: dict[str, str] = {}
    collection_keys = {cid: _item_keys(project, cid) for cid in project.collections}

    for page in pages.values():
        loc = f"{where}/{page.file.name}"
        if page.kind not in KINDS:
            errors.append(f"{loc}: kind must be one of {sorted(KINDS)}")
        if project.default_language not in page.langs:
            errors.append(f"{loc}: needs a [langs.{project.default_language}] table (the default language)")
        if page.kind == "item":
            if page.collection not in project.collections:
                errors.append(f"{loc}: collection {page.collection!r} is not in project.toml [collections]")
        for lang, spec in page.langs.items():
            lloc = f"{loc} [langs.{lang}]"
            if lang not in project.languages:
                errors.append(f"{lloc}: language not in the project's languages")
            if not (spec.path.startswith("/") and spec.path.endswith("/")):
                errors.append(f"{lloc}: path must start and end with '/'")
            if page.kind == "item" and "{slug}" not in spec.path:
                errors.append(f"{lloc}: an item page's path needs {{slug}}")
            if spec.path in seen_paths:
                errors.append(f"{lloc}: path {spec.path} is also used by {seen_paths[spec.path]}")
            seen_paths[spec.path] = f"{page.id}/{lang}"
            if not spec.title or not spec.description:
                errors.append(f"{lloc}: title and description are required")
            allowed = SITE_KEYS | (collection_keys.get(page.collection, set()) if page.kind == "item" else set())
            for text in _strings(spec):
                for name in PLACEHOLDER.findall(text):
                    if name.split(".")[0] not in allowed:
                        errors.append(f"{lloc}: unknown placeholder {{{name}}}")
                if brand and len(brand) >= 3 and re.search(rf"\b{re.escape(brand)}\b", text):
                    errors.append(f"{lloc}: write {{brand}} instead of {brand!r} so a rename is one config change")
                for char in forbidden_chars:
                    if char in text:
                        errors.append(f"{lloc}: contains {char!r}, which the project's style forbids")
            for href in _links(spec):
                if href.startswith("page:") and href[5:].split("#")[0] not in pages:
                    errors.append(f"{lloc}: link to unknown page {href}")
                if href.startswith("item:") and href[5:].partition("/")[0] not in project.collections:
                    errors.append(f"{lloc}: link to unknown collection {href}")
            for i, section in enumerate(spec.sections):
                errors.extend(_check_section(section, f"{lloc} section {i + 1}", project, pages))

    errors.extend(_check_keywords(project, pages, where))
    errors.extend(_check_data(project, where))
    return errors


def _check_section(section: dict[str, Any], loc: str, project: "Project", pages: dict[str, PageSpec]) -> list[str]:
    stype = section.get("type", "")
    if stype not in SECTION_FIELDS:
        return [f"{loc}: unknown section type {stype!r}"]
    errors = [f"{loc} ({stype}): missing {name!r}" for name in SECTION_FIELDS[stype] if name not in section]
    cta = section.get("cta")
    if cta is not None and cta not in CTAS:
        errors.append(f"{loc}: cta must be one of {sorted(CTAS)}")
    if cta in {"waitlist", "host-waitlist"} and not project.raw.get("waitlist"):
        errors.append(f"{loc}: a waitlist cta needs a [waitlist] table in project.toml")
    if stype == "proof":
        measurements = project.site.get("measurements")
        if not measurements:
            errors.append(f"{loc}: a proof section needs [site] measurements")
    if stype == "listing":
        for group in section.get("groups", []):
            if group.get("source") not in {"playable", "free", "new", "blocked", "native"}:
                errors.append(f"{loc}: listing source must be playable, free, new, blocked or native")
    return errors


def _check_keywords(project: "Project", pages: dict[str, PageSpec], where: str) -> list[str]:
    errors = []
    for cluster in load_keywords(project):
        pid = cluster.get("page", "")
        page = pages.get(pid)
        if page is None:
            errors.append(f"{where}/keywords.toml: cluster {cluster.get('primary')} maps to unknown page {pid!r}")
            continue
        spec = page.langs.get(cluster.get("lang", project.default_language))
        if spec is None:
            errors.append(f"{where}/keywords.toml: page {pid} has no {cluster.get('lang')} version")
            continue
        title = _words(spec.title + " " + _h1(spec))
        for word in _words(str(cluster.get("primary", ""))):
            if word not in title and not word.startswith("{"):
                errors.append(
                    f"{where}/keywords.toml: primary keyword {cluster.get('primary')!r} word {word!r} "
                    f"is missing from page {pid}'s title and H1"
                )
    return errors


def _check_data(project: "Project", where: str) -> list[str]:
    errors = []
    for cid, conf in project.collections.items():
        curated = project.root / str(conf.get("curated", ""))
        if not curated.is_file():
            errors.append(f"{where}: collections.{cid}: curated file {conf.get('curated')!r} not found")
            continue
        items = read_toml(curated).get("item", [])
        slugs = set()
        for item in items:
            if not item.get("slug") or not item.get("name"):
                errors.append(f"{where}/{conf.get('curated')}: every item needs slug and name")
            if item.get("slug") in slugs:
                errors.append(f"{where}/{conf.get('curated')}: duplicate slug {item.get('slug')}")
            slugs.add(item.get("slug"))
            if item.get("status") not in {"playable", "blocked", "native", "unchecked"}:
                errors.append(f"{where}/{conf.get('curated')}: {item.get('slug')}: status must be playable, blocked, native or unchecked")
            if item.get("status") == "blocked" and not item.get("reason"):
                errors.append(f"{where}/{conf.get('curated')}: {item.get('slug')}: a blocked item needs its reason")
    return errors


def _item_keys(project: "Project", cid: str) -> set[str]:
    conf = project.collections.get(cid, {})
    keys = {"name", "slug", "short", "checked", "status_label", "note", "item"}
    path = project.root / str(conf.get("curated", ""))
    if path.is_file():
        for item in read_toml(path).get("item", []):
            keys.update(item.keys())
    return keys


def _strings(spec: PageLang) -> list[str]:
    out = [spec.title, spec.description, spec.og_title, spec.og_description, spec.description_draft]
    out += [c.get("text", "") for c in spec.crumbs]

    def walk(value: Any) -> None:
        if isinstance(value, str):
            out.append(value)
        elif isinstance(value, list):
            for v in value:
                walk(v)
        elif isinstance(value, dict):
            for k, v in value.items():
                if k not in {"type", "cta", "href", "source", "measurement", "icon", "id", "collection"}:
                    walk(v)

    walk(spec.sections)
    return [s for s in out if s]


def _links(spec: PageLang) -> list[str]:
    found: list[str] = [c.get("href", "") for c in spec.crumbs]

    def walk(value: Any) -> None:
        if isinstance(value, str):
            found.extend(m.group(2) for m in re.finditer(r"\[([^\]]+)\]\(([^)\s]+)\)", value))
        elif isinstance(value, list):
            for v in value:
                walk(v)
        elif isinstance(value, dict):
            for k, v in value.items():
                if k == "href" and isinstance(v, str):
                    found.append(v)
                else:
                    walk(v)

    walk(spec.sections)
    return [f for f in found if f]


def _h1(spec: PageLang) -> str:
    for section in spec.sections:
        if section.get("type") == "hero":
            return " ".join(section.get("lines", []))
        if section.get("type") in {"intro", "item-strip"}:
            return str(section.get("h1", ""))
    return ""


def _words(text: str) -> set[str]:
    text = re.sub(r"\*\*|\{brand\}", " ", text.lower())
    return set(re.findall(r"\{[a-z_]+\}|[a-z0-9äöüß.]+", text))
