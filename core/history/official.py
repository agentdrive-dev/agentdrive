"""One official Tesla item for the Monday weekly briefing.

Pages are read from www.tesla.com for the owner's locale. A safety or recall
notice comes first. Otherwise one recent owner update is used. If nothing new
is eligible, the result is empty. Catalog usage tips are not a stand-in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from html.parser import HTMLParser
from urllib.parse import urlparse

from core.history.clock import local_today, resolve_timezone
from core.history.locale import locale_for, locale_name, site_root
from core.history.store import history_root, history_session, load_notices, save_notice
from core.history.text import dumps
from core.history.tips import on_cooldown

OFFICIAL_PREFIX = "official:"
_RECENT_DAYS = 45
_HOSTS = {"www.tesla.com", "tesla.com"}
_USEFUL = ("service", "software", "update", "charging", "owner", "warranty", "tyre", "tire", "maintenance")
_LOW_VALUE = ("order now", "inventory", "demo drive", "career", "shop now", "learn more", "read more")


@dataclass(frozen=True)
class OfficialItem:
    id: str
    kind: str
    title: str
    summary: str
    urls: tuple[str, ...]
    published: date | None

    def text(self) -> str:
        label = "Safety notice" if self.kind == "safety" else "Official update"
        lines = [f"{label}: {self.title}"]
        if self.summary:
            lines.append(self.summary)
        lines.extend(self.urls)
        return "\n".join(lines)


def fetch_text(url: str) -> str:
    """GET one www.tesla.com page. Any failure is an empty page."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc not in _HOSTS:
        return ""
    try:
        from core.fleet.http import UrllibClient

        status, text = UrllibClient().get_text(url, timeout=12)
    except Exception:
        return ""
    if status != 200 or not text:
        return ""
    return text


def take_official(home, today: date, *, tz=None, fetch=None, store=None) -> dict | None:
    """Return one new official item, or None when the slot should be omitted."""
    zone = tz or resolve_timezone(home)
    locale = locale_for(home, zone)
    getter = fetch or fetch_text
    found = _collect(locale, today, getter)
    if not found:
        return None
    with history_session(history_root(home, store), write=True) as conn:
        notices = load_notices(conn)
        if _shown_this_week(notices, today):
            return None
        chosen = _pick(found, notices, today)
        if chosen is None:
            return None
        summary = chosen.summary or _summary_from_page(getter(chosen.urls[0]))
        item = OfficialItem(
            id=chosen.id,
            kind=chosen.kind,
            title=chosen.title,
            summary=_brief(summary, chosen.title),
            urls=chosen.urls,
            published=chosen.published,
        )
        save_notice(conn, item.id, today)
    return _public(item, locale)


def render_news(home, *, as_json: bool, on: date | None = None, tz=None, fetch=None, store=None) -> str:
    zone = tz or resolve_timezone(home)
    today = on or local_today(zone)
    item = take_official(home, today, tz=zone, fetch=fetch, store=store)
    if as_json:
        return dumps({"item": item})
    if item is None:
        return ""
    return item["text"]


def _collect(locale: str, today: date, fetch) -> list[OfficialItem]:
    items: list[OfficialItem] = []
    seen: set[str] = set()
    for url in (f"{site_root(locale)}/support", f"{site_root(locale)}/blog"):
        html = fetch(url) or ""
        for item in _items_from_html(html, locale, today):
            if item.id in seen:
                continue
            seen.add(item.id)
            items.append(item)
    items.sort(key=lambda item: (0 if item.kind == "safety" else 1, -(item.published or date.min).toordinal()))
    return items


def _pick(items: list[OfficialItem], notices: dict, today: date) -> OfficialItem | None:
    for item in items:
        shown = notices.get(item.id)
        if shown is not None and on_cooldown(shown, today):
            continue
        return item
    return None


def _shown_this_week(notices: dict, today: date) -> bool:
    year, week, _day = today.isocalendar()
    for key, shown in notices.items():
        if not str(key).startswith(OFFICIAL_PREFIX):
            continue
        shown_year, shown_week, _shown_day = shown.isocalendar()
        if (shown_year, shown_week) == (year, week):
            return True
    return False


def _items_from_html(html: str, locale: str, today: date) -> list[OfficialItem]:
    parser = _LinkParser()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        return []
    items: list[OfficialItem] = []
    for href, title, published in parser.links:
        url = _official_url(href, locale)
        if url is None or len(title) < 12:
            continue
        kind = _kind(url, title)
        if kind is None:
            continue
        if kind != "safety" and not _recent(published, today):
            continue
        slug = url.rstrip("/").rsplit("/", 1)[-1].lower()
        item_id = f"{OFFICIAL_PREFIX}{locale or 'us'}:{slug}"
        items.append(OfficialItem(id=item_id, kind=kind, title=title, summary="", urls=(url,), published=published))
    return items


def _official_url(href: str, locale: str) -> str | None:
    if not href or href.startswith(("#", "mailto:", "javascript:")):
        return None
    if href.startswith("//"):
        href = "https:" + href
    elif href.startswith("/"):
        href = "https://www.tesla.com" + href
    parsed = urlparse(href)
    if parsed.scheme != "https" or parsed.netloc not in _HOSTS:
        return None
    path = parsed.path.rstrip("/")
    parts = [part for part in path.split("/") if part]
    if locale:
        if not parts or parts[0] != locale:
            return None
        rest = parts[1:]
    else:
        if parts and re.fullmatch(r"[a-z]{2}_[a-z]{2}", parts[0]):
            return None
        rest = parts
    if len(rest) < 2 or rest[0] not in {"support", "blog"}:
        return None
    return "https://www.tesla.com" + path


def _kind(url: str, title: str) -> str | None:
    slug = url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ")
    focus = f"{slug} {title}".lower()
    if any(phrase in focus for phrase in _LOW_VALUE):
        return None
    if "recall" in focus or "safety" in focus:
        return "safety"
    if any(word in focus for word in _USEFUL):
        return "update"
    return None


def _recent(published: date | None, today: date) -> bool:
    if published is None:
        return False
    age = (today - published).days
    return 0 <= age <= _RECENT_DAYS


def _summary_from_page(html: str) -> str:
    if not html:
        return ""
    for match in re.finditer(r"<meta\b[^>]*>", html, flags=re.I):
        tag = match.group(0)
        if not re.search(r"""(?:name|property)=["'](?:description|og:description)["']""", tag, flags=re.I):
            continue
        content = re.search(r"""content=["']([^"']*)["']""", tag, flags=re.I)
        if content and content.group(1).strip():
            return content.group(1).strip()
    return ""


def _brief(text: str, title: str) -> str:
    cleaned = " ".join(str(text or "").split())
    if not cleaned or cleaned.casefold() == title.casefold():
        return ""
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", cleaned) if part.strip()]
    kept: list[str] = []
    total = 0
    for sentence in sentences:
        if len(kept) == 3 or total + len(sentence) > 360:
            break
        kept.append(sentence)
        total += len(sentence)
    return " ".join(kept)


def _public(item: OfficialItem, locale: str) -> dict:
    return {
        "id": item.id,
        "kind": item.kind,
        "title": item.title,
        "summary": item.summary,
        "urls": list(item.urls),
        "locale": locale,
        "region": locale_name(locale),
        "text": item.text(),
    }


def _parse_date(value: str) -> date | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        return None


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str, date | None]] = []
        self._href: str | None = None
        self._chunks: list[str] = []
        self._item_date: date | None = None
        self._in_time = False
        self._time_raw = ""

    def handle_starttag(self, tag, attrs):
        attr = {key.lower(): value or "" for key, value in attrs}
        if tag == "time":
            self._in_time = True
            self._time_raw = attr.get("datetime", "")
        if tag == "a":
            self._href = attr.get("href")
            self._chunks = []

    def handle_endtag(self, tag):
        if tag == "time":
            self._in_time = False
            parsed = _parse_date(self._time_raw)
            if parsed is not None:
                self._item_date = parsed
            self._time_raw = ""
        if tag == "a" and self._href:
            title = " ".join("".join(self._chunks).split())
            self.links.append((self._href, title, self._item_date))
            self._href = None
            self._chunks = []

    def handle_data(self, data):
        if self._href is not None:
            self._chunks.append(data)
        elif self._in_time and not self._time_raw:
            self._time_raw = data
