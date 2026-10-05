"""Collection items (e.g. games): curated facts merged with the project's live data.

The curated file holds what a person checked once (status and reason, Mac version, sign-in, notes,
search volume, page wave). The live data source says what the product offers right now. Rules from
project.toml decide which items get a page and which pages may be indexed:

- a page only for a playable item that is live, has enough search demand, is in an enabled wave and
  has no Mac version; an item that had a page keeps its URL whatever its status becomes;
- the page stays noindex until it has the evidence its template asks for (a clip or measured numbers);
- live items without a curated record are only listed when the project trusts the live list.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from ..policy import Media, Number, PolicyViolation, check_media, check_numbers
from ..util import read_toml


@dataclass
class LiveData:
    fetched_at: datetime
    items: list[dict[str, Any]]


@dataclass
class Item:
    slug: str
    name: str
    record: dict[str, Any]
    status: str
    live: bool
    curated: bool
    has_page: bool = False
    indexable: bool = False
    first_live: str = ""
    measurement: dict[str, Any] | None = None
    why_no_page: str = ""
    similar: list["Item"] = field(default_factory=list)

    @property
    def free(self) -> bool:
        return bool(self.record.get("free"))

    @property
    def native(self) -> bool:
        return self.status == "native" or self.record.get("native_version", "none") != "none"

    @property
    def offered(self) -> bool:
        """Live, playable and without a native version: what the item page was made for."""
        return self.live and self.status == "playable" and not self.native


def load_measurements(project_root: Path, rel: str | None) -> dict[str, dict[str, Any]]:
    """Measured numbers with their provenance; a value without source and date fails the build."""
    if not rel:
        return {}
    path = project_root / rel
    if not path.is_file():
        return {}
    found: dict[str, dict[str, Any]] = {}
    for record in read_toml(path).get("measurement", []):
        values = {k: v for k, v in record.items() if k in {"resolution", "fps", "delay_ms"}}
        check_numbers(
            [Number(label=f"{record.get('id')}.{k}", value=v, source=record.get("source", ""), measured_at=record.get("measured_at", "")) for k, v in values.items()]
        )
        found[str(record["id"])] = record
    return found


def load_media(project_root: Path, rel: str | None) -> dict[str, Media]:
    """Every image or video the site may show, with where it came from."""
    if not rel:
        return {}
    path = project_root / rel
    if not path.is_file():
        return {}
    media: dict[str, Media] = {}
    for record in read_toml(path).get("media", []):
        item = Media(
            path=str(record["path"]),
            contains_game_footage=bool(record.get("contains_game_footage", False)),
            publisher=str(record.get("publisher", "")),
            source=str(record.get("source", "")),
        )
        if not item.source:
            raise PolicyViolation("own-footage-only", f"{item.path}: media needs a source (own-recording, neutral, licensed)")
        media[item.path] = item
    return media


def resolve_items(
    project: Any,
    cid: str,
    live: LiveData | None,
    registry: dict[str, Any],
    measurements: dict[str, dict[str, Any]],
    media: dict[str, Media],
    today: date,
) -> list[Item]:
    conf = project.collections[cid]
    curated = read_toml(project.root / conf["curated"]).get("item", [])
    match_live = str(conf.get("match_live", "id"))
    match_curated = str(conf.get("match_curated", "id"))
    live_by_key = {str(x.get(match_live)): x for x in (live.items if live else [])}
    published = registry.get("items", {}).get(cid, {})
    first_live = registry.get("first_live", {}).get(cid, {})
    min_searches = int(conf.get("min_searches", 0))
    max_wave = int(conf.get("max_wave", 0))

    items: list[Item] = []
    seen_keys: set[str] = set()
    for record in curated:
        key = str(record.get(match_curated, ""))
        seen_keys.add(key)
        is_live = key in live_by_key
        item = Item(
            slug=str(record["slug"]),
            name=str(record["name"]),
            record=dict(record),
            status=str(record.get("status", "unchecked")),
            live=is_live,
            curated=True,
            first_live=str(first_live.get(key, "")),
        )
        mid = record.get("measurement")
        if mid:
            if mid not in measurements:
                raise PolicyViolation("no-invented-numbers", f"{item.slug}: measurement {mid!r} is not in the measurements file")
            item.measurement = measurements[mid]
            clip = item.measurement.get("clip")
            if clip:
                if clip not in media:
                    raise PolicyViolation("own-footage-only", f"{item.slug}: clip {clip} is not in the media manifest")
                check_media([media[clip]], project.rules)
        item.has_page, item.why_no_page = _page_rule(item, conf, published, min_searches, max_wave)
        items.append(item)

    if conf.get("trust_live_list"):
        for key, record in live_by_key.items():
            if key in seen_keys:
                continue
            items.append(
                Item(
                    slug="",
                    name=str(record.get("name", key)),
                    record={"name": record.get("name", key)},
                    status="playable",
                    live=True,
                    curated=False,
                    first_live=str(first_live.get(key, "")),
                    why_no_page="no curated facts yet",
                )
            )

    by_slug = {i.slug: i for i in items if i.slug}
    for item in items:
        item.similar = [by_slug[s] for s in item.record.get("similar", []) if s in by_slug]
    return items


def _page_rule(item: Item, conf: dict[str, Any], published: dict[str, Any], min_searches: int, max_wave: int) -> tuple[bool, str]:
    if item.record.get("page") is False:
        return False, "page switched off in the curated file"
    if item.slug in published:
        return True, ""  # status changes keep the URL
    if item.status != "playable":
        return False, f"status {item.status}"
    if item.native:
        return False, "has a Mac version"
    if conf.get("require_live", True) and not item.live:
        return False, "not in the live playable list"
    if int(item.record.get("searches", 0)) < min_searches:
        return False, f"under {min_searches} searches a month"
    if int(item.record.get("wave", 99)) > max_wave:
        return False, f"wave {item.record.get('wave')} not enabled (max_wave {max_wave})"
    return True, ""


def index_evidence(item: Item) -> set[str]:
    """What the item can show that a searcher can't get elsewhere: a clip, measured numbers."""
    found = set()
    if item.measurement:
        if item.measurement.get("clip"):
            found.add("clip")
        if any(k in item.measurement for k in ("resolution", "fps", "delay_ms")):
            found.add("measured")
    return found
