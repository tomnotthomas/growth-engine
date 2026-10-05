"""Structured data per page (schema.org JSON-LD). No ratings, reviews or offers: nothing is invented."""

from __future__ import annotations

import json
from typing import Any

from ..policy import check_jsonld_types


def graph(nodes: list[dict[str, Any]]) -> str:
    types = set()
    for node in nodes:
        _collect_types(node, types)
    check_jsonld_types(types)
    data = {"@context": "https://schema.org", "@graph": nodes}
    text = json.dumps(data, ensure_ascii=False, indent=1)
    return text.replace("</", "<\\/")  # never close the script tag from inside the data


def organization(name: str, url: str) -> dict[str, Any]:
    return {"@type": "Organization", "@id": url + "#org", "name": name, "url": url}


def website(name: str, url: str, lang: str) -> dict[str, Any]:
    return {"@type": "WebSite", "@id": url + "#site", "name": name, "url": url, "inLanguage": lang, "publisher": {"@id": url + "#org"}}


def breadcrumbs(trail: list[tuple[str, str | None]]) -> dict[str, Any]:
    items = []
    for pos, (name, url) in enumerate(trail, start=1):
        entry: dict[str, Any] = {"@type": "ListItem", "position": pos, "name": name}
        if url:
            entry["item"] = url
        items.append(entry)
    return {"@type": "BreadcrumbList", "itemListElement": items}


def faq(questions: list[tuple[str, str]], lang: str) -> dict[str, Any]:
    return {
        "@type": "FAQPage",
        "inLanguage": lang,
        "mainEntity": [
            {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in questions
        ],
    }


def article(headline: str, url: str, lang: str, modified: str, org_url: str) -> dict[str, Any]:
    return {
        "@type": "Article",
        "headline": headline,
        "url": url,
        "inLanguage": lang,
        "dateModified": modified,
        "author": {"@id": org_url + "#org"},
        "publisher": {"@id": org_url + "#org"},
    }


def video(name: str, description: str, content_url: str, thumbnail_url: str, upload_date: str) -> dict[str, Any]:
    return {
        "@type": "VideoObject",
        "name": name,
        "description": description,
        "contentUrl": content_url,
        "thumbnailUrl": thumbnail_url,
        "uploadDate": upload_date,
    }


def _collect_types(node: Any, found: set[str]) -> None:
    if isinstance(node, dict):
        if isinstance(node.get("@type"), str):
            found.add(node["@type"])
        for value in node.values():
            _collect_types(value, found)
    elif isinstance(node, list):
        for value in node:
            _collect_types(value, found)
